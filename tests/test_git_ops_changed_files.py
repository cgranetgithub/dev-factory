"""
Tests for the changed-file listing that feeds the autofix step.

Uses a real git repository in a temp directory — GitPython only, no network.
"""

from __future__ import annotations

import git

from devfactory.config import settings
from devfactory.context import GitHubIssue, PipelineContext
from devfactory.github.git_ops import changed_python_files


def _repo_with_ctx(tmp_path, monkeypatch) -> tuple[git.Repo, PipelineContext]:
    """Create workspace/repo as a git repo with one committed file."""
    monkeypatch.setattr(settings, "workspace", tmp_path)
    workspace = tmp_path / "repo"
    workspace.mkdir()

    repo = git.Repo.init(workspace)
    (workspace / "existing.py").write_text("x = 1\n")
    repo.index.add(["existing.py"])
    repo.index.commit(
        "initial",
        author=git.Actor("t", "t@example.com"),
        committer=git.Actor("t", "t@example.com"),
    )

    issue = GitHubIssue(
        number=1,
        title="t",
        body="b",
        repo="owner/repo",
        labels=[],
        url="https://github.com/owner/repo/issues/1",
    )
    return repo, PipelineContext(issue=issue)


def test_lists_modified_and_new_python_files(tmp_path, monkeypatch):
    repo, ctx = _repo_with_ctx(tmp_path, monkeypatch)
    workspace = tmp_path / "repo"

    (workspace / "existing.py").write_text("x = 2\n")  # modified, tracked
    (workspace / "brand_new.py").write_text("y = 1\n")  # created, untracked

    assert changed_python_files(ctx) == ["brand_new.py", "existing.py"]


def test_ignores_non_python_files(tmp_path, monkeypatch):
    repo, ctx = _repo_with_ctx(tmp_path, monkeypatch)
    workspace = tmp_path / "repo"

    (workspace / "README.md").write_text("# hi\n")
    (workspace / "data.json").write_text("{}\n")

    assert changed_python_files(ctx) == []


def test_ignores_deleted_files(tmp_path, monkeypatch):
    """A deleted file has nothing left to format and must not be passed to ruff."""
    repo, ctx = _repo_with_ctx(tmp_path, monkeypatch)
    (tmp_path / "repo" / "existing.py").unlink()

    assert changed_python_files(ctx) == []


def test_clean_tree_yields_nothing(tmp_path, monkeypatch):
    _repo, ctx = _repo_with_ctx(tmp_path, monkeypatch)

    assert changed_python_files(ctx) == []
