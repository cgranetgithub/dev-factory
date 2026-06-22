"""
GitHub PR review posting — inline comments via GitHub Reviews API.
"""

from __future__ import annotations

import logging

from github import GithubException
from github.PullRequest import PullRequest

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
    comments = _build_review_comments(pr, result.inline_comments)

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
        path = f.filename
        patch = f.patch
        if not patch:
            continue

        line_map: dict[int, int] = {}
        diff_position = 0
        current_line = 0

        for diff_line in patch.splitlines():
            diff_position += 1
            if diff_line.startswith("@@"):
                # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
                import re

                m = re.search(r"\+(\d+)", diff_line)
                if m:
                    current_line = int(m.group(1)) - 1
            elif diff_line.startswith("-"):
                pass  # removed line, don't increment new line counter
            elif diff_line.startswith("+"):
                current_line += 1
                line_map[current_line] = diff_position
            else:
                current_line += 1  # context line
                line_map[current_line] = diff_position

        result[path] = line_map

    return result
