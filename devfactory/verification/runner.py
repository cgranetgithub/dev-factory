"""
Verification Runner — executes ruff, mypy, bandit, pytest inside a Docker container.
Returns a structured VerificationReport.

Every tool result carries a status: ``clean``, ``findings`` or ``error``. The third
one exists because a tool that crashes says nothing, and nothing used to parse as
"no issues". Ruff unable to write its cache to a read-only mount, mypy the same,
a missing binary — each produced empty output, and the gate passed code it had
not checked. A tool in the error state now fails the report and names itself in
the summary, with what it printed.

The classification leans on exit codes first and output second: a tool's exit
status is the one thing it reports reliably even when its output is garbage.

Ruff and bandit judge source text and need nothing installed, so each runs alone
against the read-only mount. Mypy and pytest need the project's dependencies —
without them every third-party import is ``Any``, which both invents errors and
silences real ones — so they share one container over one installed copy of the
checkout (see :meth:`VerificationRunner._run_over_installed_copy`).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from devfactory.config import settings
from devfactory.context import VerificationReport

logger = logging.getLogger(__name__)

# Where the candidate repo is mounted inside the verification container. Tools report their
# findings under this prefix, but the developer agent works in the host workspace
# and has no such directory — see _build_summary, which strips it back out.
CONTAINER_WORKDIR = "/workspace"

# Where the read-only mount is copied so the project can be installed into it.
# A fixed path, not `mktemp -d`: mypy and pytest report their findings under it,
# and _build_summary can only strip a prefix it knows in advance.
BUILD_DIR = "/build"

# The three states a tool result can be in.
CLEAN = "clean"
FINDINGS = "findings"
ERROR = "error"

# Exit codes of our own, chosen outside every tool's range so they cannot be
# confused with a verdict.
_TIMED_OUT = -1  # the container did not finish within the deadline
_SETUP_FAILED = 90  # the shared step could not `pip install .` the project

# How much of a failed tool's output reaches the summary. Enough to diagnose,
# short enough not to drown the findings of the tools that did run.
_RAW_TAIL = 1500

_COUNT = re.compile(r"(\d+) (passed|failed|error)")

# Mypy and pytest write into one stream, so the step frames each one's output and
# echoes its exit code. The framing is deliberately unlike anything either tool
# prints, so a finding quoting a marker cannot forge a section boundary.
_MARKER = "##DEVFACTORY:{}##"
_EXIT_MARKER = "##DEVFACTORY:{}_EXIT:"


class VerificationRunner:
    def __init__(self, image: str | None = None):
        self.image = image or settings.docker_test_image

    def run(self, repo_path: Path) -> VerificationReport:
        """Run full verification suite in Docker and return structured report."""
        if not repo_path.exists():
            raise FileNotFoundError(f"Repo path not found: {repo_path}")

        logger.info(f"[verification] running on {repo_path} with image={self.image}")

        ruff = self._run_ruff(repo_path)
        bandit = self._run_bandit(repo_path)
        mypy, pytest = self._run_over_installed_copy(repo_path)

        # A tool that did not run has not passed, whatever the others say.
        every_tool_ran = all(r["status"] != ERROR for r in (ruff, mypy, bandit, pytest))
        passed = (
            every_tool_ran
            and len(ruff["issues"]) == 0
            and len(mypy["errors"]) == 0
            and bandit["severity"] not in ("HIGH", "MEDIUM")
            and pytest["failed"] == 0
            and pytest["errors"] == []
        )

        summary = self._build_summary(ruff, mypy, bandit, pytest, passed)

        return VerificationReport(
            passed=passed,
            ruff=ruff,
            mypy=mypy,
            bandit=bandit,
            pytest=pytest,
            summary=summary,
            raw_output=json.dumps({"ruff": ruff, "mypy": mypy, "bandit": bandit, "pytest": pytest}),
        )

    def _docker_run(self, repo_path: Path, cmd: str, timeout: int = 120) -> tuple[str, int]:
        """Run a command inside the test Docker container.

        The repo is mounted read-only; ``timeout`` bounds the whole container run
        (the mypy/pytest step passes a larger value because it installs the
        project first — see :meth:`_run_over_installed_copy`). The exit code is
        the command's own, so the callers can read it; a timeout is reported as
        :data:`_TIMED_OUT` rather than raised, because "the tests did not finish"
        is a verification result the developer should hear about, not a pipeline
        crash.
        """
        full_cmd = [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{repo_path.absolute()}:{CONTAINER_WORKDIR}:ro",
            "--workdir",
            CONTAINER_WORKDIR,
            self.image,
            "sh",
            "-c",
            cmd,
        ]
        try:
            result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or ""
            if isinstance(partial, bytes):
                partial = partial.decode(errors="replace")
            return f"{partial}\n[timed out after {timeout}s]", _TIMED_OUT
        return result.stdout + result.stderr, result.returncode

    def _run_ruff(self, repo_path: Path) -> dict:
        # --no-cache: /workspace is read-only, so ruff cannot create its .ruff_cache
        # there. Without it ruff crashes — which is now an error state rather than
        # a false pass, but still not a useful run.
        # Exit codes: 0 clean, 1 violations found, 2 could not run.
        output, code = self._docker_run(
            repo_path, "ruff check . --no-cache --output-format=json 2>&1"
        )
        issues = _json_array(output)
        if code in (0, 1) and issues is not None and bool(issues) == (code == 1):
            return {"status": FINDINGS if issues else CLEAN, "issues": issues, "returncode": code}
        return _tool_error("ruff", output, code, issues=[])

    def _run_bandit(self, repo_path: Path) -> dict:
        # Exit codes: 0 no findings, 1 findings; anything else could not run.
        output, code = self._docker_run(repo_path, "bandit -r . -f json -q 2>&1")
        data = _json_object(output)
        if code not in (0, 1) or data is None or not isinstance(data.get("results"), list):
            return _tool_error("bandit", output, code, findings=[], severity="none")

        results = data["results"]
        severities = [r.get("issue_severity", "LOW") for r in results]
        top_severity = (
            "HIGH"
            if "HIGH" in severities
            else "MEDIUM"
            if "MEDIUM" in severities
            else "LOW"
            if severities
            else "none"
        )
        return {
            "status": FINDINGS if results else CLEAN,
            "findings": results,
            "severity": top_severity,
            "returncode": code,
        }

    def _run_over_installed_copy(self, repo_path: Path) -> tuple[dict, dict]:
        """Run mypy and pytest in one container, over one installed copy of the repo.

        The test image ships the verification tools, not the candidate project's
        dependencies. Mypy without them resolves every third-party import to
        ``Any``, so it both invents errors that exist only in a bare environment
        and stops seeing the ones that need the real types (issue #77); pytest
        without them fails at collection. Both therefore need the project
        installed — and installing it twice, once per container, would pay the
        same price twice, so they share a container.

        ``pip install .`` writes ``<pkg>.egg-info`` into the source tree, which
        the read-only mount forbids, so the checkout is copied to
        :data:`BUILD_DIR` first. The candidate checkout stays untouched.

        A failed install exits with a code of our own: neither tool ran, and both
        are reported as the environment failing rather than one as a mysterious
        test failure and the other as a mysterious type error.

        Returns:
            The mypy result and the pytest result, in that order.
        """
        # Measured 2026-09-09, two runs each, on a clean export of this repository
        # with the image already built: 19.0s / 20.6s before (four containers),
        # 22.3s / 22.2s after (three). About 2.5s per verification run, and the
        # install that dominates both — 16-18s of it — is the price the pytest
        # step already paid. What grew is mypy itself: with the dependencies
        # resolved it has real types to check instead of Any.
        #
        # Setuptools builds in-tree, so `pip install .` leaves ./build/lib holding
        # a second copy of the package. mypy refuses to check a tree with two
        # modules of the same name ("Duplicate module named ..."), which is an
        # exit 2 — a tool that did not run. Removing what the install created puts
        # the copy back in the shape CI type-checks, so the two agree; a `build/`
        # the candidate had of its own is left alone, because CI would trip over
        # that one too and the gate must not be more forgiving than CI.
        cmd = (
            f"mkdir -p {BUILD_DIR}; cp -r {CONTAINER_WORKDIR}/. {BUILD_DIR}/ 2>/dev/null; "
            f"cd {BUILD_DIR}; "
            "if [ -f pyproject.toml ] || [ -f setup.py ]; then "
            "if [ -d build ]; then OWN_BUILD=1; else OWN_BUILD=0; fi; "
            f"pip install -q . 2>&1 || exit {_SETUP_FAILED}; "
            'if [ "$OWN_BUILD" = 0 ]; then rm -rf build; fi; '
            "fi; "
            # --cache-dir in /tmp: keep mypy's cache out of the tree it reports on,
            # so the copy stays a faithful image of the candidate checkout.
            f"echo '{_MARKER.format('MYPY')}'; "
            "mypy . --ignore-missing-imports --cache-dir=/tmp/mypy_cache 2>&1; "
            f'echo "{_EXIT_MARKER.format("MYPY")}$?##"; '
            f"echo '{_MARKER.format('PYTEST')}'; "
            "pytest --tb=short -q 2>&1; "
            f'echo "{_EXIT_MARKER.format("PYTEST")}$?##"'
        )
        # The timeout pytest already had: the install dominates it either way.
        output, code = self._docker_run(repo_path, cmd, timeout=300)

        if code == _SETUP_FAILED:
            reason = "the project could not be installed (`pip install .` failed)"
            return (
                _tool_error("mypy", output, code, reason=reason, errors=[]),
                _tool_error("pytest", output, code, reason=reason, passed=0, failed=0, errors=[]),
            )

        # A section is missing when the step died before reaching that tool — a
        # timeout, or a shell that never started. The tool did not run, and the
        # whole combined output is the best evidence we have of why.
        mypy_section = _section(output, "MYPY")
        pytest_section = _section(output, "PYTEST")
        mypy = (
            _classify_mypy(*mypy_section)
            if mypy_section
            else _tool_error("mypy", output, code, errors=[])
        )
        pytest = (
            _classify_pytest(*pytest_section)
            if pytest_section
            else _tool_error("pytest", output, code, passed=0, failed=0, errors=[])
        )
        return mypy, pytest

    def _build_summary(
        self, ruff: dict, mypy: dict, bandit: dict, pytest: dict, passed: bool
    ) -> str:
        lines = ["## Verification Report\n"]
        lines.append(f"**Overall: {'✓ PASSED' if passed else '✗ FAILED'}**\n")

        ruff_count = len(ruff.get("issues", []))
        lines.append(f"- **Ruff (lint):** {_status_or(ruff, f'{ruff_count} issue(s)')}")

        mypy_count = len(mypy.get("errors", []))
        lines.append(f"- **Mypy (types):** {_status_or(mypy, f'{mypy_count} error(s)')}")

        sev = bandit.get("severity", "none")
        lines.append(f"- **Bandit (security):** {_status_or(bandit, f'severity={sev}')}")

        p, f = pytest.get("passed", 0), pytest.get("failed", 0)
        ran = f"{p} passed, {f} failed" if (p or f) else "no tests collected"
        lines.append(f"- **Pytest:** {_status_or(pytest, ran)}")

        if not passed:
            lines.append("\n### Issues to fix:")
            if ruff_count:
                for issue in ruff.get("issues", [])[:5]:
                    loc = issue.get("location", {}).get("row", "?")
                    fname = issue.get("filename", "?")
                    msg = issue.get("message", "")
                    lines.append(f"  - [ruff] {fname}:{loc} — {msg}")
            if mypy_count:
                for err in mypy.get("errors", [])[:5]:
                    lines.append(f"  - [mypy] {err}")
            for err in pytest.get("errors", [])[:5]:
                lines.append(f"  - [pytest] {err}")

        # A tool that did not run is named, with what it printed: the developer
        # cannot fix a finding nobody made, but it can often fix what stopped the
        # tool — a syntax error, a missing dependency, a broken pyproject.
        failed_tools = [
            (name, r)
            for name, r in (("ruff", ruff), ("mypy", mypy), ("bandit", bandit), ("pytest", pytest))
            if r.get("status") == ERROR
        ]
        if failed_tools:
            lines.append("\n### Tools that did not run:")
            for name, r in failed_tools:
                lines.append(f"- **{name}**: {r['error']}")
                tail = (r.get("raw") or "").strip()[-_RAW_TAIL:]
                if tail:
                    lines.append(f"  ```\n{tail}\n  ```")

        # The summary is fed back to the developer agent on a verification retry, and that
        # agent works in the host workspace — "/workspace/devfactory/foo.py" is a
        # path it cannot resolve. Every tool reports under one of the two container
        # paths (the read-only mount, or the installed copy mypy and pytest share),
        # so strip both once here rather than in each parser: the agent then
        # receives repo-relative paths it can actually open.
        text = "\n".join(lines)
        return text.replace(f"{CONTAINER_WORKDIR}/", "").replace(f"{BUILD_DIR}/", "")


def _section(output: str, tool: str) -> tuple[str, int] | None:
    """What ``tool`` printed inside the combined step, and how it exited.

    Returns ``None`` when either marker is missing, which means the step never
    got that far — the caller turns that into the error state.
    """
    opener = _MARKER.format(tool)
    start = output.find(opener)
    if start < 0:
        return None
    body_start = start + len(opener)
    closer = re.compile(re.escape(_EXIT_MARKER.format(tool)) + r"(-?\d+)##")
    match = closer.search(output, body_start)
    if match is None:
        return None
    return output[body_start : match.start()].strip("\n"), int(match.group(1))


def _classify_mypy(output: str, code: int) -> dict:
    """Read a mypy run. Exit codes: 0 clean, 1 type errors, 2 could not run."""
    errors = [line for line in output.splitlines() if ": error:" in line]
    if code == 0 and "Success:" in output:
        return {"status": CLEAN, "errors": [], "raw": output, "returncode": code}
    if code == 1 and errors:
        return {"status": FINDINGS, "errors": errors, "raw": output, "returncode": code}
    return _tool_error("mypy", output, code, errors=[])


def _classify_pytest(output: str, code: int) -> dict:
    """Read a pytest run.

    Exit codes: 0 all passed, 1 some failed, 5 nothing collected; 2, 3 and 4 are
    interruption, internal error and usage error — none of them a verdict.
    """
    counts: dict[str, int] = {"passed": 0, "failed": 0, "error": 0}
    for count, kind in _COUNT.findall(output):
        counts[kind] = int(count)
    problems = [line for line in output.splitlines() if "FAILED" in line or "ERROR" in line]

    result = {"raw": output, "returncode": code}
    if code == 0 and counts["passed"] > 0:
        return {"status": CLEAN, "passed": counts["passed"], "failed": 0, "errors": [], **result}
    if code == 5:
        # Nothing to run is not a failure of the code under test; the summary
        # says so, and the record keeps the zero.
        return {"status": CLEAN, "passed": 0, "failed": 0, "errors": [], **result}
    if code == 1 and (counts["failed"] or counts["error"]):
        return {
            "status": FINDINGS,
            "passed": counts["passed"],
            "failed": counts["failed"],
            "errors": problems[:20],
            **result,
        }
    return _tool_error("pytest", output, code, passed=0, failed=0, errors=[])


def _tool_error(tool: str, output: str, code: int, reason: str | None = None, **empty) -> dict:
    """A result in the error state, keeping the keys the scorer reads (empty)."""
    if reason is None:
        if code == _TIMED_OUT:
            reason = "did not finish in time"
        elif code == 127:
            reason = "command not found in the verification image"
        elif not output.strip():
            reason = f"exited {code} and printed nothing"
        else:
            reason = f"exited {code} with output that is not a verdict"
    logger.warning(f"[verification] {tool} did not run: {reason}")
    return {"status": ERROR, "error": reason, "raw": output, "returncode": code, **empty}


def _status_or(result: dict, detail: str) -> str:
    return "did not run" if result.get("status") == ERROR else detail


def _json_array(output: str) -> list | None:
    """The JSON array in ``output``, skipping any warning lines printed before it."""
    data = _json_from(output, "[")
    return data if isinstance(data, list) else None


def _json_object(output: str) -> dict | None:
    data = _json_from(output, "{")
    return data if isinstance(data, dict) else None


def _json_from(output: str, opener: str) -> Any:
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith(opener):
            try:
                return json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                return None
    return None
