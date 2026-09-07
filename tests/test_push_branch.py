"""
Tests for pushing the feature branch.

Written from a real failure: a run produced a complete, verified, approved change
and then died at the push with `! [rejected] ... (stale info)`, because the remote
branch had been deleted since this workspace last looked at it.
"""

from __future__ import annotations

import git
import pytest

from devfactory.context import GitHubIssue, PipelineContext
from devfactory.github import git_ops


class _FakeRemote:
    def __init__(self, fetch_error=None):
        self.fetch_error = fetch_error
        self.fetched_with = None

    def set_url(self, url):
        pass

    def fetch(self, **kwargs):
        self.fetched_with = kwargs
        if self.fetch_error:
            raise self.fetch_error


class _FakeGit:
    def __init__(self, push_error=None):
        self.push_error = push_error
        self.pushed = None

    def push(self, *args):
        self.pushed = args
        if self.push_error:
            raise self.push_error


class _FakeRepo:
    def __init__(self, push_error=None, fetch_error=None):
        self.remotes = type("R", (), {"origin": _FakeRemote(fetch_error)})()
        self.git = _FakeGit(push_error)


def _ctx() -> PipelineContext:
    issue = GitHubIssue(
        number=1, title="t", body="b", repo="owner/repo", labels=[], url="https://x/1"
    )
    ctx = PipelineContext(issue=issue)
    ctx.branch_name = "feature/issue-1-t"
    return ctx


def _install(monkeypatch, repo):
    monkeypatch.setattr(git.Repo, "__new__", lambda cls, *a, **k: repo)


def test_fetches_before_pushing(monkeypatch):
    """The lease is evaluated against the local remote-tracking ref, so it has to
    be fresh or the push is refused for the wrong reason."""
    repo = _FakeRepo()
    _install(monkeypatch, repo)

    git_ops.push_branch(_ctx())

    assert repo.remotes.origin.fetched_with == {"prune": True}
    assert "--force-with-lease" in repo.git.pushed


def test_a_failed_fetch_does_not_prevent_the_push(monkeypatch):
    """The push may well succeed anyway, and its error is the informative one."""
    repo = _FakeRepo(fetch_error=git.GitCommandError("fetch", 1))
    _install(monkeypatch, repo)

    git_ops.push_branch(_ctx())

    assert repo.git.pushed is not None


def test_a_stale_lease_is_reported_as_what_it_is(monkeypatch):
    """`stale info` reads like a permissions problem and is not one."""
    repo = _FakeRepo(push_error=git.GitCommandError("push", 1, stderr=b"! [rejected] (stale info)"))
    _install(monkeypatch, repo)

    with pytest.raises(RuntimeError, match="stale even after fetching"):
        git_ops.push_branch(_ctx())


def test_other_push_errors_are_not_disguised(monkeypatch):
    """A genuine permissions or network failure must keep its own message."""
    repo = _FakeRepo(push_error=git.GitCommandError("push", 1, stderr=b"permission denied"))
    _install(monkeypatch, repo)

    with pytest.raises(git.GitCommandError):
        git_ops.push_branch(_ctx())
