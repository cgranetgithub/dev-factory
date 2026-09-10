"""
GitHub PR review posting — inline comments via GitHub Reviews API.
"""

from __future__ import annotations

import logging
from typing import Any

from github import GithubException
from github.PullRequest import PullRequest
from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

from devfactory.context import PipelineContext, ReviewResult
from devfactory.github.client import gh

logger = logging.getLogger(__name__)

# Map ReviewResult.verdict → GitHub review event
VERDICT_MAP = {
    "approved": "APPROVE",
    "changes_requested": "REQUEST_CHANGES",
    "commented": "COMMENT",
}


def post_review(ctx: PipelineContext, result: ReviewResult):
    """
    Post an inline GitHub PR review.
    Inline comments are posted on the diff; summary goes in the review body.
    """
    if ctx.pr_number is None:
        raise ValueError("Cannot post review — pr_number not set in context")

    repo = gh.get_repo(ctx.issue.repo)
    pr: PullRequest = repo.get_pull(ctx.pr_number)

    event = VERDICT_MAP.get(result.verdict, "COMMENT")

    # Build inline comments in GitHub's format
    # Each comment needs: path, position (line in diff), body
    # Typed as Any: PyGithub's stubs want ReviewComment objects, but create_review
    # accepts these position-mapped dicts at runtime (stub versions disagree).
    comments: Any = _build_review_comments(pr, result.inline_comments)

    body = _build_review_body(result)

    try:
        pr.create_review(
            body=body,
            event=event,
            comments=comments,
        )
        logger.info(
            f"[review] posted {event} review from {result.model} "
            f"with {len(comments)} inline comment(s)"
        )
    except GithubException as e:
        # Fallback: post as a regular comment if review API fails
        logger.warning(f"[review] Review API failed ({e}), falling back to PR comment")
        pr.create_issue_comment(f"**Review by `{result.model}`**\n\n{body}")


def _build_review_body(result: ReviewResult) -> str:
    lines = [
        f"**Model:** `{result.model}`",
        f"**Quality score:** {result.score:.1f}/1.0",
        f"**Verdict:** {result.verdict}",
        "",
        result.summary,
    ]
    return "\n".join(lines)


def _build_review_comments(pr: PullRequest, inline_comments: list[dict]) -> list[dict]:
    """
    Convert agent inline comments to GitHub Review comments format.
    GitHub requires `path` and `position` (position in the unified diff).

    We map line numbers to diff positions using the PR's file list.
    Comments on lines not in the diff are dropped with a warning.
    """
    if not inline_comments:
        return []

    # Build a map: {path: {line_number: diff_position}}
    diff_map = _build_diff_position_map(pr)
    result = []

    for comment in inline_comments:
        path = comment.get("path", "")
        line = comment.get("line")
        body = comment.get("body", "")

        if not path or not line or not body:
            continue

        position = diff_map.get(path, {}).get(int(line))
        if position is None:
            # Line not in diff — post as a file-level comment (position=1)
            logger.debug(f"[review] line {line} not in diff for {path}, using file-level")
            position = 1

        result.append(
            {
                "path": path,
                "position": position,
                "body": f"<!-- model: {comment.get('model', 'unknown')} -->\n{body}",
            }
        )

    return result


def _build_diff_position_map(pr: PullRequest) -> dict[str, dict[int, int]]:
    """
    Build a mapping of {file_path: {line_number: diff_position}} from the PR files.
    diff_position is 1-indexed position in the unified diff hunk.
    """
    result: dict[str, dict[int, int]] = {}

    for f in pr.get_files():
        patch = f.patch
        if not patch:
            # Binary files and pure renames carry no patch, so they have no
            # positions to comment on.
            continue

        line_map = _positions_in_patch(f.filename, patch)
        if line_map is not None:
            result[f.filename] = line_map

    return result


def _positions_in_patch(path: str, patch: str) -> dict[int, int] | None:
    """
    Map new-file line numbers onto review positions for one file's patch.

    A review `position` counts every line of that file's patch — hunk headers,
    context, additions and removals alike — starting at 1 on the file's first `@@`
    and not resetting between its hunks.

    Args:
        path: The file path, used only for logging.
        patch: The per-file patch as GitHub returns it, starting at the first `@@`.

    Returns:
        {new_file_line_number: review_position}, or None if the patch has no hunks
        or cannot be parsed.
    """
    # GitHub hands out the patch body alone: no `diff --git`, no `---`/`+++` lines.
    # unidiff needs a file header to know a file has begun, so put a synthetic pair
    # back. The names in it are never read — only the hunk geometry below them is.
    try:
        patched_file = PatchSet("--- a/f\n+++ b/f\n" + patch)[0]
    except (UnidiffParseError, IndexError) as e:
        logger.warning(f"[review] could not parse the patch for {path} ({e})")
        return None

    if not patched_file or not patched_file[0] or patched_file[0][0].diff_line_no is None:
        return None

    # unidiff numbers lines from the start of what it parsed — the synthetic header
    # included — so rebase onto the first `@@`. Hunk itself carries no line number,
    # so take it from the line just below the header.
    first_header = patched_file[0][0].diff_line_no - 1

    line_map: dict[int, int] = {}
    for hunk in patched_file:
        for line in hunk:
            # Removed lines and the `\ No newline at end of file` marker have no
            # line in the new file; the marker is why this used to be wrong.
            if line.target_line_no is None or line.diff_line_no is None:
                continue
            line_map[line.target_line_no] = line.diff_line_no - first_header + 1

    return line_map
