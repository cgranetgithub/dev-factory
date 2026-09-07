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


def workspace_path(ctx: PipelineContext) -> Path:
    """Local checkout for this run's repository.

    Public because three modules outside this one need it. It was private, and
    they imported it anyway — a leading underscore that everyone ignores documents
    nothing and misleads the next reader.
    """
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
    workspace = workspace_path(ctx)
    url = _repo_url(ctx.repo_owner, ctx.repo_name)

    # Clone or update
    if workspace.exists():
        logger.info(f"[git] updating existing repo at {workspace}")
        repo = git.Repo(workspace)
        origin = repo.remotes.origin
        origin.set_url(url)
        # Reset to clean state on default branch
        base = default_branch(repo)
        repo.git.checkout(base)
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
    workspace = workspace_path(ctx)
    repo = git.Repo(workspace)

    # Stage everything
    repo.git.add("-A")

    # Check if there is anything to commit
    has_staged = bool(repo.index.diff("HEAD")) if repo.head.is_valid() else bool(repo.index.entries)
    has_untracked = bool(repo.untracked_files)
    if not has_staged and not has_untracked:
        logger.warning("[git] nothing to commit — developer produced no file changes")
        if repo.head.is_valid():
            return str(repo.head.commit.hexsha)
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
    return str(commit.hexsha)


def changed_python_files(ctx: PipelineContext) -> list[str]:
    """
    Return the Python files the developer just touched, relative to the repo root.

    Covers both tracked modifications and new files, since an agent typically does
    both in one pass. Deleted files are excluded — there is nothing left to format.
    """
    workspace = workspace_path(ctx)
    repo = git.Repo(workspace)

    # Tracked files modified in the working tree (diff against the index target).
    modified = [item.a_path for item in repo.index.diff(None) if item.change_type != "D"]
    # Files created by the agent are untracked until commit_changes stages them.
    untracked = list(repo.untracked_files)

    paths = {p for p in modified + untracked if p and p.endswith(".py")}
    # Keep only files that still exist: a rename shows up as a modification of a
    # path that is already gone.
    return sorted(p for p in paths if (workspace / p).is_file())


def working_tree_has_changes(ctx: PipelineContext) -> bool:
    """Whether the checkout holds uncommitted work.

    Called between the developer and the commit, so it answers "did *this*
    iteration produce anything" rather than "does the branch differ from main".
    Earlier iterations are already committed by then, which is what makes the
    distinction reliable — and what two attempts at this check got wrong, one by
    comparing against the base branch (always true from iteration two onwards) and
    one by inspecting the index before anything was staged (never true for a plain
    file edit).
    """
    repo = git.Repo(workspace_path(ctx))
    # bool(): GitPython's stubs type is_dirty loosely, and mypy is the gate here.
    return bool(repo.is_dirty(untracked_files=True))


def files_changed_on_branch(ctx: PipelineContext) -> list[str]:
    """Return every file the branch changed relative to the default branch.

    Cumulative on purpose, unlike :func:`changed_python_files`: the scope gate asks
    what the finished work touched, and a file created on the first iteration is
    still part of the change on the third.
    """
    workspace = workspace_path(ctx)
    repo = git.Repo(workspace)
    default = default_branch(repo)
    try:
        out: str = repo.git.diff(f"{default}...HEAD", "--name-only", "--no-color")
    except git.GitCommandError as e:
        logger.warning(f"[git] could not list changed files: {e}")
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def push_branch(ctx: PipelineContext):
    """Push the feature branch to origin.

    Fetches first, because the push uses ``--force-with-lease``. The lease is
    evaluated against the local remote-tracking ref, so if that ref is stale — the
    branch was deleted or moved on the remote since this workspace last looked, as
    happens whenever a pull request is merged with "delete branch on merge", or
    closed by hand — git refuses with::

        ! [rejected] ... (stale info)

    which reads like a permissions problem and is not one. Fetching first makes the
    lease mean what it is meant to mean: refuse if someone else pushed to this
    branch since we looked, rather than refuse because we never looked.
    """
    workspace = workspace_path(ctx)
    repo = git.Repo(workspace)
    url = _repo_url(ctx.repo_owner, ctx.repo_name)
    repo.remotes.origin.set_url(url)

    try:
        # prune: a branch deleted on the remote must disappear locally too, or its
        # stale tracking ref is exactly what breaks the lease.
        repo.remotes.origin.fetch(prune=True)
    except git.GitCommandError as e:
        # Not fatal on its own — the push below may still succeed, and its error
        # will be the more informative one.
        logger.warning(f"[git] could not refresh remote state before pushing ({e})")

    try:
        repo.git.push("--set-upstream", "origin", ctx.branch_name, "--force-with-lease")
    except git.GitCommandError as e:
        if "stale info" in str(e):
            raise RuntimeError(
                f"Refused to push {ctx.branch_name}: the local view of the remote "
                f"branch is stale even after fetching. Someone changed it during "
                f"this run — inspect it before retrying."
            ) from e
        raise
    logger.info(f"[git] pushed {ctx.branch_name}")


def get_diff(ctx: PipelineContext) -> str:
    """
    Return the diff between the feature branch and the default branch.
    Used to inject into reviewer prompts.
    """
    workspace = workspace_path(ctx)
    repo = git.Repo(workspace)
    default = default_branch(repo)
    try:
        diff: str = repo.git.diff(f"{default}...HEAD", "--stat", "-p", "--no-color")
        # Truncate to ~20K chars to fit in context
        if len(diff) > 20_000:
            diff = diff[:20_000] + "\n\n[... diff truncated for context limit ...]"
        return diff
    except git.GitCommandError as e:
        logger.warning(f"[git] could not get diff: {e}")
        return "[diff unavailable]"


def default_branch(repo: git.Repo) -> str:
    """Detect the default branch name (main or master).

    Public for the same reason as workspace_path: pr.py already imports it.
    """
    try:
        branches = [b.name for b in repo.branches]
        if "main" in branches:
            return "main"
        if "master" in branches:
            return "master"
        # Fall back to remote HEAD
        ref: str = repo.remotes.origin.refs.HEAD.reference.name
        return ref.replace("origin/", "")
    except Exception:
        return "main"
