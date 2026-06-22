"""
Git operations — clone, branch, commit, push using GitPython.
All operations work on the local workspace directory.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import git  # gitpython

from devfactory.config import settings
from devfactory.context import PipelineContext

logger = logging.getLogger(__name__)


def _repo_url(owner: str, repo_name: str) -> str:
    """Build authenticated HTTPS clone URL."""
    token = settings.github_token
    return f"https://{token}@github.com/{owner}/{repo_name}.git"


def _workspace_path(ctx: PipelineContext) -> Path:
    return settings.workspace / ctx.repo_name


def _branch_slug(title: str) -> str:
    """Convert issue title to a safe branch slug."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:40]


def setup_branch(ctx: PipelineContext) -> git.Repo:
    """
    Clone the repo (or update it) and create a fresh feature branch.
    Sets ctx.branch_name.
    Returns the GitPython Repo object.
    """
    workspace = _workspace_path(ctx)
    url = _repo_url(ctx.repo_owner, ctx.repo_name)

    # Clone or update
    if workspace.exists():
        logger.info(f"[git] updating existing repo at {workspace}")
        repo = git.Repo(workspace)
        origin = repo.remotes.origin
        origin.set_url(url)
        # Reset to clean state on default branch
        default_branch = _default_branch(repo)
        repo.git.checkout(default_branch)
        origin.pull()
    else:
        logger.info(f"[git] cloning {ctx.issue.repo} → {workspace}")
        settings.workspace.mkdir(parents=True, exist_ok=True)
        repo = git.Repo.clone_from(url, workspace)

    # Create feature branch
    branch_name = f"feature/issue-{ctx.issue.number}-{_branch_slug(ctx.issue.title)}"
    ctx.branch_name = branch_name

    # Delete branch if it already exists (re-run case)
    if branch_name in [b.name for b in repo.branches]:
        repo.git.branch("-D", branch_name)

    repo.git.checkout("-b", branch_name)
    logger.info(f"[git] on branch {branch_name}")
    return repo


def commit_changes(ctx: PipelineContext, attempt: int = 1) -> str:
    """
    Stage all changes and commit them.
    Returns the commit SHA.
    """
    workspace = _workspace_path(ctx)
    repo = git.Repo(workspace)

    # Stage everything
    repo.git.add("-A")

    # Check if there is anything to commit
    has_staged = bool(repo.index.diff("HEAD")) if repo.head.is_valid() else bool(repo.index.entries)
    has_untracked = bool(repo.untracked_files)
    if not has_staged and not has_untracked:
        logger.warning("[git] nothing to commit — developer produced no file changes")
        if repo.head.is_valid():
            return repo.head.commit.hexsha
        return ""

    message = (
        f"feat: implement issue #{ctx.issue.number} (attempt {attempt})\n\n"
        f"{ctx.issue.title}\n\n"
        f"Closes #{ctx.issue.number}"
    )
    commit = repo.index.commit(
        message,
        author=git.Actor("DevFactory", "devfactory@localhost"),
        committer=git.Actor("DevFactory", "devfactory@localhost"),
    )
    sha = commit.hexsha[:8]
    logger.info(f"[git] committed {sha}")
    ctx.commits.append(sha)
    return commit.hexsha


def push_branch(ctx: PipelineContext):
    """Push the feature branch to origin."""
    workspace = _workspace_path(ctx)
    repo = git.Repo(workspace)
    url = _repo_url(ctx.repo_owner, ctx.repo_name)
    repo.remotes.origin.set_url(url)
    repo.git.push("--set-upstream", "origin", ctx.branch_name, "--force-with-lease")
    logger.info(f"[git] pushed {ctx.branch_name}")


def get_diff(ctx: PipelineContext) -> str:
    """
    Return the diff between the feature branch and the default branch.
    Used to inject into reviewer prompts.
    """
    workspace = _workspace_path(ctx)
    repo = git.Repo(workspace)
    default = _default_branch(repo)
    try:
        diff = repo.git.diff(f"{default}...HEAD", "--stat", "-p", "--no-color")
        # Truncate to ~20K chars to fit in context
        if len(diff) > 20_000:
            diff = diff[:20_000] + "\n\n[... diff truncated for context limit ...]"
        return diff
    except git.GitCommandError as e:
        logger.warning(f"[git] could not get diff: {e}")
        return "[diff unavailable]"


def _default_branch(repo: git.Repo) -> str:
    """Detect the default branch name (main or master)."""
    try:
        branches = [b.name for b in repo.branches]
        if "main" in branches:
            return "main"
        if "master" in branches:
            return "master"
        # Fall back to remote HEAD
        ref = repo.remotes.origin.refs.HEAD.reference.name
        return ref.replace("origin/", "")
    except Exception:
        return "main"
