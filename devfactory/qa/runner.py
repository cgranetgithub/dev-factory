"""
QA Runner — executes ruff, mypy, bandit, pytest inside a Docker container.
Returns a structured QAReport.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from devfactory.config import settings
from devfactory.context import QAReport

logger = logging.getLogger(__name__)


class QARunner:
    def __init__(self, image: str | None = None):
        self.image = image or settings.docker_test_image

    def run(self, repo_path: Path) -> QAReport:
        """Run full QA suite in Docker and return structured report."""
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

        return QAReport(
            passed=passed,
            ruff=ruff,
            mypy=mypy,
            bandit=bandit,
            pytest=pytest,
            summary=summary,
            raw_output=json.dumps({"ruff": ruff, "mypy": mypy, "bandit": bandit, "pytest": pytest}),
        )

    def _docker_run(self, repo_path: Path, cmd: str) -> tuple[str, int]:
        """Run a command inside the test Docker container."""
        full_cmd = [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{repo_path.absolute()}:/workspace:ro",
            "--workdir",
            "/workspace",
            self.image,
            "sh",
            "-c",
            cmd,
        ]
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=120)
        return result.stdout + result.stderr, result.returncode

    def _run_ruff(self, repo_path: Path) -> dict:
        output, code = self._docker_run(
            repo_path, "ruff check . --output-format=json 2>/dev/null || true"
        )
        try:
            issues = json.loads(output) if output.strip().startswith("[") else []
        except json.JSONDecodeError:
            issues = []
        return {"issues": issues, "returncode": code}

    def _run_mypy(self, repo_path: Path) -> dict:
        output, code = self._docker_run(repo_path, "mypy . --ignore-missing-imports 2>&1 || true")
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
        output, code = self._docker_run(repo_path, "pytest --tb=short -q 2>&1 || true")
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
        lines = ["## QA Report\n"]
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

        return "\n".join(lines)
