"""
Tests for how the verification runner reads each tool's result.

Issue #25. Every tool used to be parsed as "no output means no findings", so a
tool that crashed passed the gate. Docker is stubbed at ``_docker_run``: each test
scripts what a tool printed and how it exited, and asserts the classification.

Since issue #77 mypy and pytest share one container over one installed copy of
the repo, so scripting them means scripting one marker-framed stream — see
:func:`_combined`. Tests that need to script the shared step as a whole (a failed
install, a container that died mid-step) pass ``combined=`` instead.
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


def _combined(mypy: tuple[str, int], pytest_: tuple[str, int]) -> tuple[str, int]:
    """What the shared mypy+pytest container prints, framed as the runner frames it.

    The step's own exit code is the last command's, so pytest's.
    """
    mypy_out, mypy_code = mypy
    pytest_out, pytest_code = pytest_
    return (
        f"##DEVFACTORY:MYPY##\n{mypy_out}\n##DEVFACTORY:MYPY_EXIT:{mypy_code}##\n"
        f"##DEVFACTORY:PYTEST##\n{pytest_out}\n##DEVFACTORY:PYTEST_EXIT:{pytest_code}##",
        pytest_code,
    )


def _runner(
    monkeypatch, combined: tuple[str, int] | None = None, **scripted: tuple[str, int]
) -> VerificationRunner:
    """A runner whose container answers are scripted per tool, others clean."""
    answers = {
        "ruff": _RUFF_CLEAN,
        "mypy": _MYPY_CLEAN,
        "bandit": _BANDIT_CLEAN,
        "pytest": _PYTEST_CLEAN,
        **scripted,
    }

    def fake_docker_run(self, repo_path, cmd, timeout=120):
        # Ruff and bandit each get their own container; the shared mypy+pytest
        # step is the one left, and its command names both tools, so it cannot be
        # matched by tool name any more.
        if "ruff" in cmd:
            return answers["ruff"]
        if "bandit" in cmd:
            return answers["bandit"]
        return combined if combined is not None else _combined(answers["mypy"], answers["pytest"])

    monkeypatch.setattr(VerificationRunner, "_docker_run", fake_docker_run)
    return VerificationRunner(image="test-image")


def _report(monkeypatch, tmp_path, combined=None, **scripted):
    return _runner(monkeypatch, combined, **scripted).run(tmp_path)


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


def test_a_project_that_cannot_be_installed_puts_both_tools_in_error(monkeypatch, tmp_path):
    """The install is the shared step's first act. If it fails, neither mypy nor
    pytest ran, and neither may be reported as anything but an error."""
    output = "ERROR: Could not find a version that satisfies the requirement nonexistent>=9"
    report = _report(monkeypatch, tmp_path, combined=(output, 90))

    assert report.mypy["status"] == ERROR
    assert report.pytest["status"] == ERROR
    assert "pip install" in report.mypy["error"]
    assert "pip install" in report.pytest["error"]
    assert report.passed is False


def test_a_step_that_died_before_pytest_leaves_pytest_in_error(monkeypatch, tmp_path):
    """A container killed mid-step prints mypy's section and never closes pytest's.
    Mypy's verdict stands; pytest's absence is an error, not a silent pass."""
    partial, _ = _combined(_MYPY_CLEAN, ("", 0))
    truncated = partial.split("##DEVFACTORY:PYTEST##")[0] + "\n[timed out after 300s]"
    report = _report(monkeypatch, tmp_path, combined=(truncated, -1))

    assert report.mypy["status"] == CLEAN
    assert report.pytest["status"] == ERROR
    assert "did not finish in time" in report.pytest["error"]
    assert report.passed is False


# ── The shared mypy + pytest step ────────────────────────────────────────────


def test_mypy_and_pytest_exit_codes_are_read_independently(monkeypatch, tmp_path):
    """One container, two verdicts: mypy's findings must not colour pytest's
    result, and the step's own exit code is pytest's alone."""
    mypy_out = "a.py:3: error: Incompatible return value type  [return-value]\nFound 1 error"
    report = _report(monkeypatch, tmp_path, mypy=(mypy_out, 1), pytest=_PYTEST_CLEAN)

    assert report.mypy["status"] == FINDINGS
    assert report.mypy["returncode"] == 1
    assert report.pytest["status"] == CLEAN
    assert report.pytest["returncode"] == 0
    assert report.pytest["passed"] == 4
    assert report.passed is False


def test_neither_tools_output_leaks_into_the_others_section(monkeypatch, tmp_path):
    """The markers, not proximity, decide what belongs to whom — a pytest failure
    line must not be counted as a mypy error, nor the reverse."""
    pytest_out = "FAILED tests/test_a.py::test_x - error: boom\n1 failed, 3 passed in 0.20s"
    report = _report(monkeypatch, tmp_path, pytest=(pytest_out, 1))

    assert report.mypy["status"] == CLEAN
    assert report.mypy["errors"] == []
    assert report.pytest["failed"] == 1
    assert "FAILED" not in report.mypy["raw"]


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
