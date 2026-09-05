"""
Deterministic formatting pass applied between the developer and the QA gate.

Most of what failed QA in practice was mechanical: lines over the limit, unsorted
imports, unused imports, ``else`` after ``return``. Ruff fixes all of that without
a model, reliably and in under a second. Spending three LLM retries on whitespace
wastes the retry budget that should be available for real defects — and the model
demonstrably fails to spend it well.

Runs inside the same image as the QA runner, so the ruff that fixes the code is the
ruff that judges it. The mount is writable here, unlike in
:mod:`devfactory.qa.runner`, because the whole point is to modify the checkout.

Only the files the developer touched are formatted: running ``ruff format`` over the
whole repository would bury the real change under unrelated reformatting.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from devfactory.config import settings
from devfactory.qa.runner import CONTAINER_WORKDIR

logger = logging.getLogger(__name__)

# Ruff is fast; if it has not finished by then, something is wrong with the image.
_TIMEOUT_S = 120


def autofix(repo_path: Path, files: list[str], image: str | None = None) -> bool:
    """
    Apply ruff's safe fixes and formatter to ``files`` inside ``repo_path``.

    Args:
        repo_path: Host path of the workspace checkout.
        files:     Repo-relative Python file paths, as returned by
                   :func:`devfactory.github.git_ops.changed_python_files`.
        image:     Docker image to use; defaults to the QA image.

    Returns:
        True if ruff ran (whether or not it changed anything), False if it could
        not run at all. A failure here is never fatal: QA still runs afterwards and
        will report whatever is left.
    """
    if not files:
        logger.info("[autofix] no Python files changed — skipping")
        return False

    quoted = " ".join(f"'{f}'" for f in files)
    # --no-cache: the cache would be written into the mounted checkout and then
    # committed. Two separate steps: `check --fix` applies safe lint fixes (import
    # sorting, unused imports, else-after-return), `format` handles layout and line
    # length. Neither must abort the chain, hence `|| true` — the QA gate is what
    # decides pass or fail, not this step.
    cmd = f"ruff check --fix --no-cache {quoted} || true; ruff format --no-cache {quoted} || true"
    full_cmd = [
        "docker",
        "run",
        "--rm",
        "--volume",
        f"{repo_path.absolute()}:{CONTAINER_WORKDIR}",  # writable, unlike QA
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
        # Docker missing or hung: log and continue. QA will surface the lint issues.
        logger.warning(f"[autofix] could not run ruff ({e}) — continuing without it")
        return False

    output = (result.stdout + result.stderr).strip()
    logger.info(f"[autofix] ruff applied to {len(files)} file(s)")
    if output:
        logger.debug(f"[autofix] {output}")
    return True
