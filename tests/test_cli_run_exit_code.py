"""
Tests for what `devfactory run` reports to its caller.

A run that died — the harness hung, a gate raised, GitHub was unreachable — used
to exit 0. The failure was in the log and on the issue's labels, but the shell saw
success, so nothing built on top of the command could tell the two apart.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from devfactory.cli import app


@pytest.fixture(autouse=True)
def _quiet_logging(monkeypatch):
    """The command configures file logging for the issue; a test needs none of it."""
    monkeypatch.setattr("devfactory.logging_setup.setup_logging", lambda **kwargs: None)


@pytest.fixture
def _issue(monkeypatch):
    monkeypatch.setattr("devfactory.github.issues.fetch_issue", lambda repo, number: object())


def _invoke(monkeypatch, pipeline_run):
    """Run the command with a Pipeline whose `run` is whatever the test supplies."""

    class _Pipeline:
        def __init__(self, **kwargs):
            pass

        run = staticmethod(pipeline_run)

    monkeypatch.setattr("devfactory.orchestrator.Pipeline", _Pipeline)
    return CliRunner().invoke(app, ["run", "--issue", "4", "--repo", "owner/repo"])


def test_a_failed_run_exits_non_zero(monkeypatch, _issue):
    def boom(_issue_arg):
        raise RuntimeError("opencode produced no output in 120s")

    result = _invoke(monkeypatch, boom)

    assert result.exit_code == 1
    assert "Pipeline failed" in result.output
    assert "produced no output" in result.output


def test_a_successful_run_exits_zero(monkeypatch, _issue):
    class _Ctx:
        pr_url = "https://github.com/owner/repo/pull/1"

    result = _invoke(monkeypatch, lambda _issue_arg: _Ctx())

    assert result.exit_code == 0
    assert "pull/1" in result.output
