"""
Tests for the gate dry run — ``devfactory gate check`` (issue #92).

Onboarding a repository has to answer one question before an issue is ever
processed: does this repository pass its own gate as it stands? The command's
value is entirely in how it reports the answer, so these tests drive the CLI
itself and assert the exit code and what the owner reads.

Two failures must never be conflated, and the exit code is where that shows:
1 the target's own suite fails (theirs to fix), 2 the gate could not run (ours).
Docker is stubbed at ``VerificationRunner.run``; nothing here needs a container
or a network.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from devfactory.cli import app
from devfactory.context import VerificationReport
from devfactory.verification.runner import CLEAN, ERROR, FINDINGS, VerificationRunner

runner = CliRunner()


def _report(**overrides: dict) -> VerificationReport:
    """A report with every tool clean, minus whatever a test replaces."""
    results: dict[str, dict] = {
        "ruff": {"status": CLEAN, "issues": []},
        "mypy": {"status": CLEAN, "errors": []},
        "bandit": {"status": CLEAN, "findings": [], "severity": "none"},
        "pytest": {"status": CLEAN, "passed": 12, "failed": 0, "errors": []},
    }
    results.update(overrides)
    passed = all(
        r["status"] != ERROR
        for r in (results["ruff"], results["mypy"], results["bandit"], results["pytest"])
    ) and not (
        results["ruff"]["issues"]
        or results["mypy"]["errors"]
        or results["bandit"]["severity"] in ("HIGH", "MEDIUM")
        or results["pytest"]["failed"]
        or results["pytest"]["errors"]
    )
    return VerificationReport(
        passed=passed,
        ruff=results["ruff"],
        mypy=results["mypy"],
        bandit=results["bandit"],
        pytest=results["pytest"],
        summary="stub",
        raw_output=json.dumps(results),
    )


def _check(monkeypatch, tmp_path, report: VerificationReport, repo="owner/target"):
    """Run `gate check` against a local checkout with a scripted gate result."""
    monkeypatch.setattr(VerificationRunner, "run", lambda self, path, repo=None: report)
    return runner.invoke(app, ["gate", "check", "--repo", repo, "--path", str(tmp_path)])


def test_a_repository_that_passes_its_own_gate_exits_zero(monkeypatch, tmp_path):
    result = _check(monkeypatch, tmp_path, _report())

    assert result.exit_code == 0
    assert "passes its own gate" in result.stdout


def test_the_targets_own_failures_exit_one_and_are_named(monkeypatch, tmp_path):
    """The factory cannot open a pull request on a branch whose base already
    fails, and the owner needs to know which tool to answer to."""
    result = _check(
        monkeypatch,
        tmp_path,
        _report(
            mypy={"status": FINDINGS, "errors": ["a.py:1: error: bad type"]},
            pytest={"status": FINDINGS, "passed": 3, "failed": 2, "errors": ["FAILED a::b"]},
        ),
    )

    assert result.exit_code == 1
    assert "does not pass its own gate" in result.stdout
    assert "mypy" in result.stdout
    assert "pytest" in result.stdout


def test_a_tool_that_did_not_run_exits_two_as_our_problem(monkeypatch, tmp_path):
    """ "We could not look" is never reported as "nothing wrong" — the same
    convention as `devfactory controls check`."""
    result = _check(
        monkeypatch,
        tmp_path,
        _report(
            mypy={"status": ERROR, "error": "the target's environment could not be built"},
            pytest={
                "status": ERROR,
                "error": "the target's environment could not be built",
                "passed": 0,
                "failed": 0,
                "errors": [],
            },
        ),
    )

    assert result.exit_code == 2
    assert "The gate could not run" in result.stdout
    assert "environment could not be built" in result.stdout


def test_low_bandit_findings_do_not_block_onboarding(monkeypatch, tmp_path):
    """The gate fails only on MEDIUM and above, and the dry run must not present
    a finding the gate tolerates as a blocker."""
    result = _check(
        monkeypatch,
        tmp_path,
        _report(
            bandit={
                "status": FINDINGS,
                "findings": [{"issue_severity": "LOW"}],
                "severity": "LOW",
            }
        ),
    )

    assert result.exit_code == 0
    assert "passes its own gate" in result.stdout


def test_a_medium_bandit_finding_blocks_and_says_so(monkeypatch, tmp_path):
    result = _check(
        monkeypatch,
        tmp_path,
        _report(
            bandit={
                "status": FINDINGS,
                "findings": [{"issue_severity": "MEDIUM"}] * 20,
                "severity": "MEDIUM",
            }
        ),
    )

    assert result.exit_code == 1
    assert "bandit" in result.stdout
    assert "20 finding(s)" in result.stdout


def test_a_skipped_tool_is_shown_as_skipped_and_blocks_nothing(monkeypatch, tmp_path):
    result = _check(
        monkeypatch,
        tmp_path,
        _report(
            mypy={
                "status": "skipped",
                "skipped": "not part of the gate for this repository",
                "errors": [],
            }
        ),
    )

    assert result.exit_code == 0
    assert "skipped" in result.stdout


def test_the_tool_table_names_every_tool_and_the_environment(monkeypatch, tmp_path):
    """The reader has to be able to tell which gate produced this verdict."""
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.13"\n')

    result = _check(monkeypatch, tmp_path, _report())

    for tool in ("ruff", "mypy", "bandit", "pytest"):
        assert tool in result.stdout
    assert "python 3.13" in result.stdout


def test_a_slug_that_is_not_owner_repo_is_our_problem_not_a_verdict(monkeypatch, tmp_path):
    """No --path, so the checkout would have to be cloned; a malformed slug cannot
    be, and that is not a statement about anyone's code."""
    monkeypatch.setattr(VerificationRunner, "run", lambda self, path, repo=None: _report())

    result = runner.invoke(app, ["gate", "check", "--repo", "not-a-slug"])

    assert result.exit_code == 2
    assert "could not run" in result.stdout


def test_a_clone_that_fails_is_reported_without_the_token(monkeypatch, tmp_path):
    """The clone URL carries the GitHub token and git quotes it back in errors.
    A dry run that cannot obtain the code says so — and says it once, without
    printing the credential into a terminal or a log."""
    import git

    from devfactory.github import git_ops

    def fake_clone(url, path, **kwargs):
        raise git.GitCommandError(["git", "clone", url], 128, stderr="fatal: repository not found")

    monkeypatch.setattr(git.Repo, "clone_from", fake_clone)
    monkeypatch.setattr(git_ops.settings, "workspace", tmp_path)
    monkeypatch.setattr(git_ops.settings, "github_token", "ghp_secret_value")
    monkeypatch.setattr(VerificationRunner, "run", lambda self, path, repo=None: _report())

    result = runner.invoke(app, ["gate", "check", "--repo", "owner/absent"])

    assert result.exit_code == 2
    assert "The gate could not run" in result.stdout
    assert "ghp_secret_value" not in result.stdout
