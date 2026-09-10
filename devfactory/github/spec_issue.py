"""
The analyst's specification, as a GitHub issue of its own.

The specification used to live in a Python object that existed until the process
exited. Publishing it makes it something a human can read, amend and cite — and
something the next stage fetches on its own rather than is handed. Both directions
live here: :func:`publish_spec` writes the issue, :func:`spec_for` reads it back.
The issue is the specification; nothing in memory is.

An issue rather than a comment, because GitHub versions the edits, it is a
first-class object with its own labels and discussion, and it can be corrected
before development starts. It costs a second issue per task, which the
``devfactory:spec`` label and a filter keep out of the way.
"""

from __future__ import annotations

import logging
import re

from devfactory.context import PipelineContext, TaskSpec
from devfactory.github.client import gh
from devfactory.github.issues import LABEL_SPEC, _ensure_labels

logger = logging.getLogger(__name__)

# Written into the body so a spec issue can be found again without relying on its
# title, which a human may well rewrite.
_MARKER = "<!-- devfactory:spec-for:{number} -->"

# The body is read back by these headings, so they are the contract between the
# writer and the readers. A human amending the issue keeps them; anything under a
# heading not listed here is ignored rather than misfiled.
_SECTIONS = {
    "summary": "summary",
    "acceptance criteria": "acceptance_criteria",
    "files to create": "files_to_create",
    "files to modify": "files_to_modify",
    "test strategy": "test_strategy",
    "technical notes": "tech_notes",
}
_HEADING = re.compile(r"^##\s+(.+?)\s*$")
# A list item, with or without a checkbox: "- [ ] text", "- [x] text", "* text".
_ITEM = re.compile(r"^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.*?)\s*$")


class SpecNotPublishedError(RuntimeError):
    """A stage needs the specification and none has been published for the issue."""


class AmbiguousSpecError(RuntimeError):
    """Several issues claim to be the specification for the same original issue.

    Nothing in GitHub enforces one specification per issue, so the factory has to.
    Picking one of them — whichever the API listed first — degrades silently: the
    developer can build from one spec while a human amends the other, and the
    evidence chain issue → spec → code → PR is then broken without anyone seeing
    it. Louder is safer: the run stops and names them.
    """


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
    """Return the single spec issue for ``issue_number``, or None.

    Matches on the marker in the body rather than on the title, because a human is
    expected to edit these and the title is the first thing they will change.

    Raises:
        AmbiguousSpecError: More than one issue carries the marker. There is no
            defensible way to choose between them here, so the caller is told
            which ones exist instead of being handed one of them.
    """
    matches = _spec_issues_for(repo, issue_number)
    if not matches:
        return None
    if len(matches) > 1:
        listed = ", ".join(f"#{m.number}" for m in matches)
        raise AmbiguousSpecError(
            f"issue #{issue_number} has {len(matches)} specification issues: {listed}. "
            f"The factory writes one, and cannot tell which of these is now the "
            f"specification. Keep one — #{matches[0].number} is the oldest, so most "
            f"likely the one that has been read and amended — and take the "
            f"'{LABEL_SPEC}' label and the '{_MARKER.format(number=issue_number)}' "
            f"marker off the others, then run the issue again."
        )
    return matches[0]


def _spec_issues_for(repo: str, issue_number: int) -> list:
    """Every issue carrying the marker for ``issue_number``, lowest number first.

    The whole listing is walked instead of stopping at the first hit: a duplicate
    is only detectable by looking past the spec that would have been returned.
    ``get_issues`` answers with a ``PaginatedList``, which fetches the next page
    when the iteration reaches the end of the current one, so a plain ``for``
    consumes the listing in full — every page, not just the first.
    """
    marker = _MARKER.format(number=issue_number)
    matches = [
        candidate
        for candidate in gh.get_repo(repo).get_issues(state="all", labels=[LABEL_SPEC])
        if candidate.body and marker in candidate.body
    ]
    return sorted(matches, key=lambda candidate: int(candidate.number))


def spec_for(ctx: PipelineContext) -> TaskSpec:
    """Fetch the specification a run works from — the issue, read now.

    Called by every stage that needs it, each time it needs it. That is the point:
    an amendment made to the issue between two iterations is what the next
    iteration builds against, because nothing was kept from the previous one.

    Raises:
        SpecNotPublishedError: The analyst has not published one. A stage that
            went on without a specification would work from the issue title.
    """
    if ctx.spec_issue_number is None:
        raise SpecNotPublishedError(
            f"no specification has been published for issue #{ctx.issue.number}"
        )
    return read_spec(ctx.issue.repo, ctx.spec_issue_number)


def read_spec(repo: str, spec_issue_number: int) -> TaskSpec:
    """Read spec issue ``spec_issue_number`` back into a :class:`TaskSpec`."""
    issue = gh.get_repo(repo).get_issue(spec_issue_number)
    return parse_body(issue.body or "")


def parse_body(body: str) -> TaskSpec:
    """The inverse of :func:`_build_body`, tolerant of a human's edits.

    Sections are found by heading; prose is kept as written, lists are read item
    by item with any checkbox and backticks stripped. A section that is missing
    is empty, not an error — the analyst's own output can leave one out.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        heading = _HEADING.match(line)
        if heading:
            current = _SECTIONS.get(heading.group(1).lower())
            if current is not None:
                sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)

    return TaskSpec(
        summary=_text(sections.get("summary", [])),
        acceptance_criteria=_items(sections.get("acceptance_criteria", [])),
        files_to_create=_items(sections.get("files_to_create", [])),
        files_to_modify=_items(sections.get("files_to_modify", [])),
        test_strategy=_text(sections.get("test_strategy", [])),
        tech_notes=_text(sections.get("tech_notes", [])),
    )


def _text(lines: list[str]) -> str:
    return "\n".join(lines).strip()


def _items(lines: list[str]) -> list[str]:
    items = []
    for line in lines:
        match = _ITEM.match(line)
        if match and match.group(1):
            items.append(match.group(1).strip("`"))
    return items


def _build_body(issue_number: int, spec: TaskSpec) -> str:
    lines = [
        _MARKER.format(number=issue_number),
        f"Implements #{issue_number}",
        "",
        "> Written by the DevFactory analyst, which read the codebase to produce it.",
        "> Amend it if it is wrong — the developer and the reviewer read it from here,",
        "> each time they need it. Keep the section headings: that is how they read it.",
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
