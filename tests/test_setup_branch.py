"""
Tests for the checkout a run starts from.

Written from a real failure. A run on the sandbox died while the developer was
editing, leaving its changes uncommitted in the shared workspace. Every run after
it — on any issue — then died at its first step with:

    git checkout main
    error: Your local changes to the following files would be overwritten by
    checkout: textkit/slugify.py

One crash made the workspace unusable until a human cleaned it by hand. The same
leftovers, when a checkout did succeed, became the code the analyst read: it wrote
a specification citing an implementation that existed in no commit.
"""

from __future__ import annotations

import git

from devfactory.context import GitHubIssue, PipelineContext
from devfactory.github import git_ops


def _ctx(number: int = 4, title: str = "cut long titles") -> PipelineContext:
    issue = GitHubIssue(
        number=number,
        title=title,
        body="b",
        repo="owner/repo",
        labels=[],
        url="https://x/1",
    )
    return PipelineContext(issue=issue)


def _workspace(tmp_path, monkeypatch) -> tuple[git.Repo, git.Repo]:
    """A bare origin and a clone of it, wired up as the pipeline's workspace.

    Two real repositories rather than mocks: the failure was git refusing to move,
    and a fake that always agrees to move cannot reproduce it.
    """
    from devfactory.config import settings

    origin = tmp_path / "origin.git"
    git.Repo.init(origin, bare=True, initial_branch="main")

    monkeypatch.setattr(settings, "workspace", tmp_path)
    monkeypatch.setattr(git_ops, "_repo_url", lambda owner, name: str(origin))

    work = tmp_path / "repo"
    clone = git.Repo.init(work, initial_branch="main")
    (work / "slugify.py").write_text("committed = True\n")
    clone.index.add(["slugify.py"])
    author = git.Actor("t", "t@example.com")
    clone.index.commit("init", author=author, committer=author)
    clone.create_remote("origin", str(origin))
    clone.git.push("--set-upstream", "origin", "main")

    return git.Repo(origin), clone


def test_uncommitted_leftovers_do_not_block_the_next_run(tmp_path, monkeypatch):
    """The crash itself: a dead run's edits made `git checkout main` refuse."""
    _, clone = _workspace(tmp_path, monkeypatch)
    work = tmp_path / "repo"

    # A developer edited the file and the run died before commit_changes.
    (work / "slugify.py").write_text("half a truncate() implementation\n")
    clone.git.checkout("-b", "feature/issue-4-cut-long-titles")

    git_ops.setup_branch(_ctx())  # used to raise GitCommandError

    assert not clone.is_dirty(), "the run must start from a clean tree"


def test_the_run_starts_from_what_the_repository_contains(tmp_path, monkeypatch):
    """The quieter half. Leftovers that survive into the run become the code the
    analyst reads and the base the diff is measured against — a specification
    written against work that is in no commit."""
    _, clone = _workspace(tmp_path, monkeypatch)
    work = tmp_path / "repo"

    (work / "slugify.py").write_text("leftover = True\n")
    (work / "stray_test.py").write_text("# untracked leftover\n")

    git_ops.setup_branch(_ctx())

    assert (work / "slugify.py").read_text() == "committed = True\n"
    assert not (work / "stray_test.py").exists(), "untracked leftovers must go too"


def test_the_feature_branch_is_created_from_the_default_branch(tmp_path, monkeypatch):
    """The behaviour the reset must not have broken."""
    _, clone = _workspace(tmp_path, monkeypatch)
    ctx = _ctx()

    git_ops.setup_branch(ctx)

    assert clone.active_branch.name == ctx.branch_name
    assert ctx.branch_name.startswith("feature/issue-4-")
