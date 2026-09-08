"""
Publish the analyst's specification as a GitHub issue of its own.

The specification used to live in a Python object that existed until the process
exited. Publishing it makes it something a human can read, amend and cite — and
something the next stage can fetch on its own rather than be handed.

An issue rather than a comment, because GitHub versions the edits, it is a
first-class object with its own labels and discussion, and it can be corrected
before development starts. It costs a second issue per task, which the
``devfactory:spec`` label and a filter keep out of the way.
"""

from __future__ import annotations

import logging

from devfactory.context import TaskSpec
from devfactory.github.client import gh
from devfactory.github.issues import LABEL_SPEC, _ensure_labels

logger = logging.getLogger(__name__)

# Written into the body so a spec issue can be found again without relying on its
# title, which a human may well rewrite.
_MARKER = "<!-- devfactory:spec-for:{number} -->"


def publish_spec(repo: str, issue_number: int, issue_title: str, spec: TaskSpec) -> int:
    """Create or update the spec issue for ``issue_number``. Returns its number.

    Idempotent: a second run on the same issue rewrites the existing spec rather
    than opening another one. A pipeline that opened a new spec issue per attempt
    would bury the original under its own retries.
    """
    _ensure_labels(repo)
    body = _build_body(issue_number, spec)

    existing = find_spec_issue(repo, issue_number)
    if existing is not None:
        existing.edit(body=body)
        logger.info(f"[spec] updated spec issue #{existing.number} for #{issue_number}")
        return int(existing.number)

    repository = gh.get_repo(repo)
    created = repository.create_issue(
        title=f"[Spec] {issue_title}",
        body=body,
        labels=[LABEL_SPEC],
    )
    # Link from the original, so a human reading it finds the specification
    # without searching. The reverse link is in the body above.
    repository.get_issue(issue_number).create_comment(
        f"**DevFactory** — specification published as #{created.number}.\n\n"
        f"Read it, and amend it if it is wrong: the developer works from it."
    )
    logger.info(f"[spec] created spec issue #{created.number} for #{issue_number}")
    return int(created.number)


def find_spec_issue(repo: str, issue_number: int):
    """Return the existing spec issue for ``issue_number``, or None.

    Matches on the marker in the body rather than on the title, because a human is
    expected to edit these and the title is the first thing they will change.
    """
    marker = _MARKER.format(number=issue_number)
    for candidate in gh.get_repo(repo).get_issues(state="all", labels=[LABEL_SPEC]):
        if candidate.body and marker in candidate.body:
            return candidate
    return None


def _build_body(issue_number: int, spec: TaskSpec) -> str:
    lines = [
        _MARKER.format(number=issue_number),
        f"Implements #{issue_number}",
        "",
        "> Written by the DevFactory analyst, which read the codebase to produce it.",
        "> Amend it if it is wrong — the developer works from this, not from a guess.",
        "",
        "## Summary",
        "",
        spec.summary,
        "",
    ]

    if spec.acceptance_criteria:
        lines += ["## Acceptance criteria", ""]
        lines += [f"- [ ] {c}" for c in spec.acceptance_criteria]
        lines.append("")

    if spec.files_to_create:
        lines += ["## Files to create", ""]
        lines += [f"- `{f}`" for f in spec.files_to_create]
        lines.append("")

    if spec.files_to_modify:
        lines += ["## Files to modify", ""]
        lines += [f"- `{f}`" for f in spec.files_to_modify]
        lines.append("")

    if spec.test_strategy:
        lines += ["## Test strategy", "", spec.test_strategy, ""]

    if spec.tech_notes:
        lines += ["## Technical notes", "", spec.tech_notes, ""]

    return "\n".join(lines)
