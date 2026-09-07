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
    """Swap the Repo factory, not its __new__.

    Patching a dunder on the class leaves the descriptor broken for the rest of
    the module — git.Repo.init() then fails with a TypeError from object.__new__,
    which is a confusing way to learn this.
    """
    monkeypatch.setattr(git_ops.git, "Repo", lambda *a, **k: repo)


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


def test_a_deleted_remote_branch_no_longer_breaks_the_push(tmp_path, monkeypatch):
    """The real failure, reproduced against two local repositories — no mocks, no
    network. A branch is pushed, deleted on the remote behind our back, and pushed
    again: without the fetch, --force-with-lease refuses with "stale info"."""
    from devfactory.config import settings

    origin = tmp_path / "origin.git"
    git.Repo.init(origin, bare=True, initial_branch="main")

    monkeypatch.setattr(settings, "workspace", tmp_path)
    work = tmp_path / "repo"
    clone = git.Repo.init(work, initial_branch="main")
    (work / "README.md").write_text("x\n")
    clone.index.add(["README.md"])
    author = git.Actor("t", "t@example.com")
    clone.index.commit("init", author=author, committer=author)
    clone.create_remote("origin", str(origin))
    clone.git.push("--set-upstream", "origin", "main")

    ctx = _ctx()
    ctx.branch_name = "feature/issue-1-t"
    clone.git.checkout("-b", ctx.branch_name)
    (work / "a.py").write_text("a = 1\n")
    clone.index.add(["a.py"])
    clone.index.commit("work", author=author, committer=author)

    # The URL is rewritten from the GitHub token, which this test has no use for.
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, name: str(origin))

    git_ops.push_branch(ctx)

    # Someone deletes the branch on the remote — "delete branch on merge", or by
    # hand on a closed PR. The local tracking ref still points at the old commit.
    git.Repo(origin).delete_head(ctx.branch_name, force=True)
    (work / "b.py").write_text("b = 2\n")
    clone.index.add(["b.py"])
    clone.index.commit("more work", author=author, committer=author)

    git_ops.push_branch(ctx)  # used to raise: ! [rejected] (stale info)

    assert ctx.branch_name in [h.name for h in git.Repo(origin).heads]
