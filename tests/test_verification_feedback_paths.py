"""
Tests for the verification step summary that is fed back to the developer on a retry.

The summary is the only thing the developer agent sees about its failure, so the
paths in it must be openable from the agent's working directory — not the paths
seen from inside the verification container.
"""

from __future__ import annotations

from devfactory.verification.runner import CONTAINER_WORKDIR, VerificationRunner


def _ruff_issue(filename: str, row: int, message: str) -> dict:
    """Shape of a single entry in ruff's --output-format=json output."""
    return {"filename": filename, "location": {"row": row}, "message": message}


def test_summary_strips_the_container_mount_from_ruff_paths():
    """A ruff finding is reported relative to the repo root, not to /workspace."""
    runner = VerificationRunner(image="test-image")
    ruff = {
        "issues": [
            _ruff_issue(f"{CONTAINER_WORKDIR}/devfactory/orchestrator.py", 132, "Line too long")
        ]
    }

    summary = runner._build_summary(ruff, {"errors": []}, {}, {}, passed=False)

    assert "devfactory/orchestrator.py:132" in summary
    assert CONTAINER_WORKDIR not in summary


def test_summary_strips_the_container_mount_from_every_tool():
    """mypy and pytest output go through the same normalisation as ruff."""
    runner = VerificationRunner(image="test-image")
    mypy = {
        "errors": [f"{CONTAINER_WORKDIR}/devfactory/verification/runner.py:12: error: bad type"]
    }
    pytest = {
        "passed": 0,
        "failed": 1,
        "errors": [f"ERROR collecting {CONTAINER_WORKDIR}/tests/test_new.py"],
    }

    summary = runner._build_summary({"issues": []}, mypy, {}, pytest, passed=False)

    assert CONTAINER_WORKDIR not in summary
    assert "devfactory/verification/runner.py:12" in summary
    assert "tests/test_new.py" in summary


def test_summary_of_a_passing_run_lists_no_issues():
    """A passing report keeps its shape — the fix must not alter it."""
    runner = VerificationRunner(image="test-image")

    summary = runner._build_summary(
        {"issues": []}, {"errors": []}, {"severity": "none"}, {"passed": 39}, passed=True
    )

    assert "✓ PASSED" in summary
    assert "Issues to fix" not in summary
