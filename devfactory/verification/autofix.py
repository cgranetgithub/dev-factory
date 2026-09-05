"""
Deterministic formatting pass applied between the developer and the verification gate.

Most of what failed verification in practice was mechanical: lines over the limit, unsorted
imports, unused imports, ``else`` after ``return``. Ruff fixes all of that without
a model, reliably and in under a second. Spending three LLM retries on whitespace
wastes the retry budget that should be available for real defects — and the model
demonstrably fails to spend it well.

Runs inside the same image as the verification runner, so the ruff that fixes the code is the
ruff that judges it. The mount is writable here, unlike in
:mod:`devfactory.verification.runner`, because the whole point is to modify the checkout.

Only the files the developer touched are formatted: running ``ruff format`` over the
whole repository would bury the real change under unrelated reformatting.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from devfactory.config import settings
from devfactory.verification.runner import CONTAINER_WORKDIR

logger = logging.getLogger(__name__)

# Ruff is fast; if it has not finished by then, something is wrong with the image.
_TIMEOUT_S = 120


# Wraps the "before" measurement so it can be parsed out of the combined output of
# the three ruff invocations.
_MARK_OPEN = "##DEVFACTORY_LINT_BEFORE##"
_MARK_CLOSE = "##DEVFACTORY_LINT_END##"


def autofix(repo_path: Path, files: list[str], image: str | None = None) -> int | None:
    """
    Apply ruff's safe fixes and formatter to ``files`` inside ``repo_path``.

    Args:
        repo_path: Host path of the workspace checkout.
        files:     Repo-relative Python file paths, as returned by
                   :func:`devfactory.github.git_ops.changed_python_files`.
        image:     Docker image to use; defaults to the verification image.

    Returns:
        How many lint issues the developer left behind — measured *before* fixing
        anything, so it says what the model delivered rather than what the pipeline
        salvaged. ``None`` if ruff could not run at all, or if nothing changed. A
        failure here is never fatal: verification still runs afterwards and will
        report whatever is left.
    """
    if not files:
        logger.info("[autofix] no Python files changed — skipping")
        return None

    quoted = " ".join(f"'{f}'" for f in files)
    # Measure first, then fix. Without the measurement the developer's own lint
    # hygiene becomes invisible: the pipeline would clean up after the model and
    # then score the cleaned result, flattering it.
    #
    # --no-cache: the cache would be written into the mounted checkout and then
    # committed. Two separate fix steps: `check --fix` applies safe lint fixes
    # (import sorting, unused imports, else-after-return), `format` handles layout
    # and line length. None of them must abort the chain, hence `|| true` — the
    # verification gate is what decides pass or fail, not this step.
    cmd = (
        f"echo '{_MARK_OPEN}'; "
        f"ruff check --no-cache --output-format=json {quoted} 2>/dev/null || true; "
        f"echo '{_MARK_CLOSE}'; "
        f"ruff check --fix --no-cache {quoted} || true; "
        f"ruff format --no-cache {quoted} || true"
    )
    full_cmd = [
        "docker",
        "run",
        "--rm",
        "--volume",
        f"{repo_path.absolute()}:{CONTAINER_WORKDIR}",  # writable, unlike verification
        "--workdir",
        CONTAINER_WORKDIR,
        image or settings.docker_test_image,
        "sh",
        "-c",
        cmd,
    ]

    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        # Docker missing or hung: log and continue. verification will surface the lint issues.
        logger.warning(f"[autofix] could not run ruff ({e}) — continuing without it")
        return None

    output = result.stdout + result.stderr
    left_behind = _parse_issue_count(output)

    if left_behind is None:
        logger.info(f"[autofix] ruff applied to {len(files)} file(s)")
    else:
        logger.info(
            f"[autofix] ruff applied to {len(files)} file(s) — "
            f"the developer left {left_behind} lint issue(s) behind"
        )
    logger.debug(f"[autofix] {output.strip()}")
    return left_behind


def _parse_issue_count(output: str) -> int | None:
    """Extract the pre-fix ruff issue count from the marked section of the output.

    Returns None when the section is missing or unparseable — an unknown count must
    not be scored as a clean zero.
    """
    try:
        block = output.split(_MARK_OPEN, 1)[1].split(_MARK_CLOSE, 1)[0].strip()
    except IndexError:
        logger.warning("[autofix] could not locate the lint measurement in ruff's output")
        return None

    if not block.startswith("["):
        return None
    try:
        return len(json.loads(block))
    except json.JSONDecodeError:
        logger.warning("[autofix] could not parse the lint measurement")
        return None
