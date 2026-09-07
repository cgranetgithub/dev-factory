"""
Tests for the status labels the pipeline applies to its issue.

They used to live in the poller, so a run started from the CLI left the issue
labelled `ready-for-dev` — and the poller would pick it up again. These assert
that the pipeline owns them, and that GitHub being unreachable cannot end a run
that has already done its work.
"""

from __future__ import annotations

from github import GithubException

from devfactory.orchestrator import Pipeline


def test_mark_applies_the_label():
    calls = []

    Pipeline._mark(lambda *a: calls.append(a), "owner/repo", 42)

    assert calls == [("owner/repo", 42)]


def test_a_github_failure_does_not_end_the_run(caplog):
    """Losing a label is worth a warning. Losing the run that produced a pull
    request because a label call timed out is not."""

    def boom(*_args):
        raise GithubException(502, "bad gateway", None)

    Pipeline._mark(boom, "owner/repo", 42)  # must not raise

    assert "could not update the issue's labels" in caplog.text


def test_a_network_failure_does_not_end_the_run():
    def boom(*_args):
        raise OSError("connection reset")

    Pipeline._mark(boom, "owner/repo", 42)  # must not raise
