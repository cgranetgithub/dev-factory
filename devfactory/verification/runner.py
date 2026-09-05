"""
Verification Runner — executes ruff, mypy, bandit, pytest inside a Docker container.
Returns a structured VerificationReport.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from devfactory.config import settings
from devfactory.context import VerificationReport

logger = logging.getLogger(__name__)

# Where the candidate repo is mounted inside the verification container. Tools report their
# findings under this prefix, but the developer agent works in the host workspace
# and has no such directory — see _build_summary, which strips it back out.
CONTAINER_WORKDIR = "/workspace"


class VerificationRunner:
    def __init__(self, image: str | None = None):
        self.image = image or settings.docker_test_image

    def run(self, repo_path: Path) -> VerificationReport:
        """Run full verification suite in Docker and return structured report."""
        if not repo_path.exists():
            raise FileNotFoundError(f"Repo path not found: {repo_path}")

        logger.info(f"[qa_runner] running on {repo_path} with image={self.image}")

        ruff = self._run_ruff(repo_path)
        mypy = self._run_mypy(repo_path)
        bandit = self._run_bandit(repo_path)
        pytest = self._run_pytest(repo_path)

        passed = (
            len(ruff.get("issues", [])) == 0
            and len(mypy.get("errors", [])) == 0
            and bandit.get("severity", "none") not in ("HIGH", "MEDIUM")
            and pytest.get("failed", 0) == 0
            and pytest.get("errors", []) == []
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
        (the pytest step passes a larger value because it installs the project
        first — see :meth:`_run_pytest`).
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
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout + result.stderr, result.returncode

    def _run_ruff(self, repo_path: Path) -> dict:
        # --no-cache: /workspace is read-only, so ruff cannot create its
        # .ruff_cache there. Without this it crashes and the empty output is
        # misread as "0 issues" (a false pass). No cache is fine for a one-shot verification run.
        output, code = self._docker_run(
            repo_path, "ruff check . --no-cache --output-format=json 2>/dev/null || true"
        )
        try:
            issues = json.loads(output) if output.strip().startswith("[") else []
        except json.JSONDecodeError:
            issues = []
        return {"issues": issues, "returncode": code}

    def _run_mypy(self, repo_path: Path) -> dict:
        # --cache-dir in /tmp: /workspace is read-only, so mypy cannot write its
        # default .mypy_cache there. Without this it crashes and reports no
        # errors — another false pass hiding real type errors.
        output, code = self._docker_run(
            repo_path,
            "mypy . --ignore-missing-imports --cache-dir=/tmp/mypy_cache 2>&1 || true",
        )
        errors = [line for line in output.splitlines() if ": error:" in line]
        return {"errors": errors, "raw": output, "returncode": code}

    def _run_bandit(self, repo_path: Path) -> dict:
        output, code = self._docker_run(repo_path, "bandit -r . -f json -q 2>/dev/null || true")
        try:
            data = json.loads(output)
            results = data.get("results", [])
            severities = [r["issue_severity"] for r in results]
            top_severity = (
                "HIGH"
                if "HIGH" in severities
                else "MEDIUM"
                if "MEDIUM" in severities
                else "LOW"
                if severities
                else "none"
            )
        except (json.JSONDecodeError, KeyError):
            results = []
            top_severity = "none"
        return {"findings": results, "severity": top_severity, "returncode": code}

    def _run_pytest(self, repo_path: Path) -> dict:
        # The test container ships only the verification tools, not the project's runtime
        # dependencies. Each tool runs in a fresh container, so we install the
        # mounted project (which pulls its declared deps) in the SAME command
        # that runs pytest — otherwise every `import <project>` fails at
        # collection. A non-editable `pip install .` builds in a temp dir, so it
        # works even though /workspace is mounted read-only.
        # If setup fails we surface it explicitly instead of letting it look like
        # a mysterious test failure.
        # /workspace is mounted read-only, but `pip install .` needs to write
        # <pkg>.egg-info into the source tree. Copy the repo into a writable temp
        # dir inside the container, install + run pytest there. The read-only
        # mount stays untouched (tests can't mutate the candidate checkout).
        cmd = (
            'D=$(mktemp -d); cp -r /workspace/. "$D"/ 2>/dev/null; cd "$D"; '
            "if [ -f pyproject.toml ] || [ -f setup.py ]; then "
            "pip install -q . 2>&1 || { echo '##DEVFACTORY_SETUP_FAILED##'; exit 0; }; "
            "fi; "
            "pytest --tb=short -q 2>&1 || true"
        )
        # Larger timeout than the other tools: this step also installs deps.
        output, code = self._docker_run(repo_path, cmd, timeout=300)

        if "##DEVFACTORY_SETUP_FAILED##" in output:
            return {
                "passed": 0,
                "failed": 0,
                "errors": [
                    "verification environment setup failed: `pip install .` did not complete in "
                    "the test container (check the project's dependencies build cleanly)."
                ],
                "raw": output,
                "setup_failed": True,
            }

        passed = failed = 0
        errors = []
        for line in output.splitlines():
            if " passed" in line:
                try:
                    passed = int(line.split(" passed")[0].split()[-1])
                except ValueError:
                    pass
            if " failed" in line:
                try:
                    failed = int(line.split(" failed")[0].split()[-1])
                except ValueError:
                    pass
            if "ERROR" in line or "FAILED" in line:
                errors.append(line)
        return {"passed": passed, "failed": failed, "errors": errors[:20], "raw": output}

    def _build_summary(
        self, ruff: dict, mypy: dict, bandit: dict, pytest: dict, passed: bool
    ) -> str:
        lines = ["## Verification Report\n"]
        lines.append(f"**Overall: {'✓ PASSED' if passed else '✗ FAILED'}**\n")

        ruff_count = len(ruff.get("issues", []))
        lines.append(f"- **Ruff (lint):** {ruff_count} issue(s)")

        mypy_count = len(mypy.get("errors", []))
        lines.append(f"- **Mypy (types):** {mypy_count} error(s)")

        sev = bandit.get("severity", "none")
        lines.append(f"- **Bandit (security):** severity={sev}")

        p, f = pytest.get("passed", 0), pytest.get("failed", 0)
        lines.append(f"- **Pytest:** {p} passed, {f} failed")

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

        # The summary is fed back to the developer agent on a verification retry, and that
        # agent works in the host workspace — "/workspace/devfactory/foo.py" is a
        # path it cannot resolve. Every tool reports under the container mount, so
        # strip the prefix once here rather than in each parser: the agent then
        # receives repo-relative paths it can actually open.
        return "\n".join(lines).replace(f"{CONTAINER_WORKDIR}/", "")
