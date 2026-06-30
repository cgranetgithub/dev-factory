"""
GitHub issue fetcher and label management.
"""

from __future__ import annotations

import logging

from github.Issue import Issue as GHIssue

from devfactory.context import GitHubIssue
from devfactory.github.client import gh

logger = logging.getLogger(__name__)

# Labels managed by devfactory
LABEL_READY = "ready-for-dev"
LABEL_PROGRESS = "devfactory:in-progress"
LABEL_QA_FAILED = "devfactory:qa-failed"
LABEL_REVIEW = "devfactory:ready-for-review"
LABEL_ERROR = "devfactory:error"

# Colors for auto-created labels
LABEL_COLORS = {
    LABEL_PROGRESS: "0075ca",
    LABEL_QA_FAILED: "e4e669",
    LABEL_REVIEW: "0e8a16",
    LABEL_ERROR: "d73a4a",
}


def fetch_issue(repo: str, number: int) -> GitHubIssue:
    """Fetch a GitHub issue and return a GitHubIssue dataclass."""
    raw = gh.get_issue(repo, number)
    return _to_dataclass(raw, repo)


def fetch_ready_issues(repo: str) -> list[GitHubIssue]:
    """Fetch all open issues labeled 'ready-for-dev', ordered oldest first."""
    r = gh.get_repo(repo)
    issues = r.get_issues(state="open", labels=[LABEL_READY], sort="created", direction="asc")
    return [_to_dataclass(i, repo) for i in issues]


def mark_in_progress(repo: str, issue_number: int):
    """Swap label ready-for-dev → devfactory:in-progress."""
    raw = gh.get_issue(repo, issue_number)
    _ensure_labels(repo)
    try:
        raw.remove_from_labels(LABEL_READY)
    except Exception:
        pass
    raw.add_to_labels(LABEL_PROGRESS)
    logger.info(f"[issues] #{issue_number} marked in-progress")


def mark_ready_for_review(repo: str, issue_number: int, pr_url: str):
    """Swap in-progress → ready-for-review and post PR link comment."""
    raw = gh.get_issue(repo, issue_number)
    _ensure_labels(repo)
    try:
        raw.remove_from_labels(LABEL_PROGRESS)
    except Exception:
        pass
    raw.add_to_labels(LABEL_REVIEW)
    raw.create_comment(
        f"**DevFactory** — PR ready for your review:\n{pr_url}\n\nPlease test and merge when ready."
    )
    logger.info(f"[issues] #{issue_number} marked ready-for-review")


def mark_qa_failed(repo: str, issue_number: int, report: str):
    """Swap in-progress → qa-failed and post the last QA report as a comment.

    Called when the Dev↔QA loop has exhausted all its attempts: this case is
    kept distinct from a generic crash (devfactory:error) so the human knows the
    code was produced but does not pass QA.
    """
    raw = gh.get_issue(repo, issue_number)
    _ensure_labels(repo)
    try:
        raw.remove_from_labels(LABEL_PROGRESS)
    except Exception:
        pass
    raw.add_to_labels(LABEL_QA_FAILED)
    raw.create_comment(f"**DevFactory — QA failed (retries exhausted):**\n\n{report[:2000]}")
    logger.warning(f"[issues] #{issue_number} marked qa-failed")


def mark_error(repo: str, issue_number: int, error: str):
    """Mark issue as errored and post comment."""
    raw = gh.get_issue(repo, issue_number)
    _ensure_labels(repo)
    for lbl in (LABEL_PROGRESS, LABEL_QA_FAILED):
        try:
            raw.remove_from_labels(lbl)
        except Exception:
            pass
    raw.add_to_labels(LABEL_ERROR)
    raw.create_comment(f"**DevFactory error:**\n```\n{error[:2000]}\n```")
    logger.error(f"[issues] #{issue_number} marked error")


def _to_dataclass(raw: GHIssue, repo: str) -> GitHubIssue:
    return GitHubIssue(
        number=raw.number,
        title=raw.title,
        body=raw.body or "",
        repo=repo,
        labels=[l.name for l in raw.labels],
        url=raw.html_url,
    )


def _ensure_labels(repo: str):
    """Create devfactory labels in the repo if they don't exist."""
    r = gh.get_repo(repo)
    existing = {l.name for l in r.get_labels()}
    for name, color in LABEL_COLORS.items():
        if name not in existing:
            try:
                r.create_label(name=name, color=color)
                logger.debug(f"[issues] created label '{name}'")
            except Exception:
                pass  # already exists (race condition)
