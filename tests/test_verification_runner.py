"""
Tests for how the verification runner reads each tool's result.

Issue #25. Every tool used to be parsed as "no output means no findings", so a
tool that crashed passed the gate. Docker is stubbed at ``_docker_run``: each test
scripts what a tool printed and how it exited, and asserts the classification.
"""

from __future__ import annotations

import json

import pytest

from devfactory.verification.runner import (
    CLEAN,
    ERROR,
    FINDINGS,
    VerificationRunner,
)

_RUFF_CLEAN = ("[]", 0)
_MYPY_CLEAN = ("Success: no issues found in 12 source files", 0)
_BANDIT_CLEAN = (json.dumps({"results": []}), 0)
_PYTEST_CLEAN = ("....\n4 passed in 0.10s", 0)


def _runner(monkeypatch, **scripted: tuple[str, int]) -> VerificationRunner:
    """A runner whose container answers are scripted per tool, others clean."""
    answers = {
        "ruff": _RUFF_CLEAN,
        "mypy": _MYPY_CLEAN,
        "bandit": _BANDIT_CLEAN,
        "pytest": _PYTEST_CLEAN,
        **scripted,
    }

    def fake_docker_run(self, repo_path, cmd, timeout=120):
        tool = next(name for name in answers if name in cmd)
        return answers[tool]

    monkeypatch.setattr(VerificationRunner, "_docker_run", fake_docker_run)
    return VerificationRunner(image="test-image")


def _report(monkeypatch, tmp_path, **scripted):
    return _runner(monkeypatch, **scripted).run(tmp_path)


# ── Everything clean ─────────────────────────────────────────────────────────


def test_a_clean_run_passes_with_every_tool_clean(monkeypatch, tmp_path):
    report = _report(monkeypatch, tmp_path)

    assert report.passed is True
    assert {r["status"] for r in (report.ruff, report.mypy, report.bandit, report.pytest)} == {
        CLEAN
    }


# ── ruff ─────────────────────────────────────────────────────────────────────


def test_ruff_findings_are_parsed(monkeypatch, tmp_path):
    issues = [{"filename": "/workspace/a.py", "location": {"row": 3}, "message": "unused"}]
    report = _report(monkeypatch, tmp_path, ruff=(json.dumps(issues), 1))

    assert report.ruff["status"] == FINDINGS
    assert report.ruff["issues"] == issues
    assert report.passed is False


def test_ruff_warnings_before_the_json_do_not_hide_it(monkeypatch, tmp_path):
    """stderr is merged into the output now, so a deprecation warning precedes the
    array. It must not turn a clean run into an unreadable one."""
    report = _report(monkeypatch, tmp_path, ruff=("warning: some setting is deprecated\n[]", 0))

    assert report.ruff["status"] == CLEAN


def test_ruff_that_crashed_is_an_error_not_a_pass(monkeypatch, tmp_path):
    """The original defect: ruff could not write its cache to the read-only
    mount, printed an error, and the empty JSON parse read as zero issues."""
    crash = "error: Failed to create cache directory '/workspace/.ruff_cache'"
    report = _report(monkeypatch, tmp_path, ruff=(crash, 2))

    assert report.ruff["status"] == ERROR
    assert report.ruff["issues"] == []
    assert report.passed is False


def test_ruff_that_printed_nothing_is_an_error(monkeypatch, tmp_path):
    report = _report(monkeypatch, tmp_path, ruff=("", 0))

    assert report.ruff["status"] == ERROR
    assert "printed nothing" in report.ruff["error"]


def test_ruff_exiting_one_with_no_findings_is_an_error(monkeypatch, tmp_path):
    """Exit 1 means violations; an empty array says there were none. A tool that
    contradicts itself has not delivered a verdict."""
    report = _report(monkeypatch, tmp_path, ruff=("[]", 1))

    assert report.ruff["status"] == ERROR


# ── mypy ─────────────────────────────────────────────────────────────────────


def test_mypy_findings_are_parsed(monkeypatch, tmp_path):
    output = (
        "a.py:3: error: Incompatible return value type  [return-value]\nFound 1 error in 1 file"
    )
    report = _report(monkeypatch, tmp_path, mypy=(output, 1))

    assert report.mypy["status"] == FINDINGS
    assert len(report.mypy["errors"]) == 1
    assert report.passed is False


def test_mypy_that_crashed_is_an_error(monkeypatch, tmp_path):
    crash = "mypy: error: Cannot find config file\n"
    report = _report(monkeypatch, tmp_path, mypy=(crash, 2))

    assert report.mypy["status"] == ERROR
    assert report.mypy["errors"] == []
    assert report.passed is False


def test_mypy_that_printed_nothing_is_an_error(monkeypatch, tmp_path):
    """Exit 0 with no 'Success:' line is not a clean run — it is a run that said
    nothing, which is what a mypy unable to write its cache looked like."""
    report = _report(monkeypatch, tmp_path, mypy=("", 0))

    assert report.mypy["status"] == ERROR


# ── bandit ───────────────────────────────────────────────────────────────────


def test_bandit_findings_and_top_severity_are_parsed(monkeypatch, tmp_path):
    data = {"results": [{"issue_severity": "LOW"}, {"issue_severity": "MEDIUM"}]}
    report = _report(monkeypatch, tmp_path, bandit=(json.dumps(data), 1))

    assert report.bandit["status"] == FINDINGS
    assert report.bandit["severity"] == "MEDIUM"
    assert report.passed is False


def test_bandit_low_findings_do_not_fail_the_report(monkeypatch, tmp_path):
    data = {"results": [{"issue_severity": "LOW"}]}
    report = _report(monkeypatch, tmp_path, bandit=(json.dumps(data), 1))

    assert report.bandit["status"] == FINDINGS
    assert report.passed is True


def test_bandit_that_crashed_is_an_error(monkeypatch, tmp_path):
    report = _report(monkeypatch, tmp_path, bandit=("Traceback (most recent call last): ...", 2))

    assert report.bandit["status"] == ERROR
    assert report.bandit["severity"] == "none"
    assert report.passed is False


def test_bandit_that_printed_nothing_is_an_error(monkeypatch, tmp_path):
    report = _report(monkeypatch, tmp_path, bandit=("", 0))

    assert report.bandit["status"] == ERROR


# ── pytest ───────────────────────────────────────────────────────────────────


def test_pytest_failures_are_parsed(monkeypatch, tmp_path):
    output = "..F.\nFAILED tests/test_a.py::test_x - AssertionError\n1 failed, 3 passed in 0.20s"
    report = _report(monkeypatch, tmp_path, pytest=(output, 1))

    assert report.pytest["status"] == FINDINGS
    assert report.pytest["passed"] == 3
    assert report.pytest["failed"] == 1
    assert report.pytest["errors"] == ["FAILED tests/test_a.py::test_x - AssertionError"]
    assert report.passed is False


def test_pytest_collection_errors_are_findings(monkeypatch, tmp_path):
    output = "ERROR tests/test_a.py - ImportError: cannot import name\n1 error in 0.05s"
    report = _report(monkeypatch, tmp_path, pytest=(output, 1))

    assert report.pytest["status"] == FINDINGS
    assert report.passed is False


def test_pytest_with_nothing_collected_is_clean_and_says_so(monkeypatch, tmp_path):
    """No tests is not a failure of the code under test — but the summary must
    not read like a suite that passed."""
    report = _report(monkeypatch, tmp_path, pytest=("no tests ran in 0.01s", 5))

    assert report.pytest["status"] == CLEAN
    assert report.pytest["passed"] == 0
    assert "no tests collected" in report.summary


def test_pytest_internal_error_is_an_error(monkeypatch, tmp_path):
    report = _report(monkeypatch, tmp_path, pytest=("INTERNALERROR> ...", 3))

    assert report.pytest["status"] == ERROR
    assert report.passed is False


def test_pytest_that_printed_nothing_is_an_error(monkeypatch, tmp_path):
    report = _report(monkeypatch, tmp_path, pytest=("", 0))

    assert report.pytest["status"] == ERROR


def test_a_project_that_cannot_be_installed_is_an_error_named_as_such(monkeypatch, tmp_path):
    output = "ERROR: Could not find a version that satisfies the requirement nonexistent>=9"
    report = _report(monkeypatch, tmp_path, pytest=(output, 90))

    assert report.pytest["status"] == ERROR
    assert "pip install" in report.pytest["error"]
    assert report.passed is False


def test_a_timed_out_container_is_an_error_not_a_crash(monkeypatch, tmp_path):
    """The tests not finishing is a verification result the developer should
    hear about, not an exception that ends the run."""
    import subprocess

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=300, output=b"...")

    monkeypatch.setattr(subprocess, "run", fake_run)

    output, code = VerificationRunner(image="test-image")._docker_run(tmp_path, "pytest", 300)

    assert code == -1
    assert "timed out after 300s" in output


# ── The summary ──────────────────────────────────────────────────────────────


def test_the_summary_names_the_tool_that_did_not_run_and_shows_its_output(monkeypatch, tmp_path):
    """The developer cannot fix a finding nobody made, but it can often fix what
    stopped the tool — if it is told what that was."""
    crash = "error: Failed to create cache directory '/workspace/.ruff_cache'"
    report = _report(monkeypatch, tmp_path, ruff=(crash, 2))

    assert "✗ FAILED" in report.summary
    assert "Tools that did not run" in report.summary
    assert "**ruff**" in report.summary
    assert "Failed to create cache directory" in report.summary
    assert "**Ruff (lint):** did not run" in report.summary


def test_the_summary_of_a_clean_run_is_unchanged(monkeypatch, tmp_path):
    report = _report(monkeypatch, tmp_path)

    assert "✓ PASSED" in report.summary
    assert "did not run" not in report.summary
    assert "4 passed, 0 failed" in report.summary


@pytest.mark.parametrize("tool", ["ruff", "mypy", "bandit", "pytest"])
def test_any_single_tool_in_error_fails_the_report(monkeypatch, tmp_path, tool):
    report = _report(monkeypatch, tmp_path, **{tool: ("", 0)})

    assert report.passed is False
