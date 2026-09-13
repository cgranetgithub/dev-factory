"""
Tests for the specification as its own issue: publishing it, and reading it back.

PyGithub is stubbed; nothing reaches the network.
"""

from __future__ import annotations

import pytest

from devfactory.context import GitHubIssue, PipelineContext, TaskSpec
from devfactory.github import spec_issue


class _Issue:
    def __init__(self, number: int, body: str = ""):
        self.number = number
        self.body = body
        self.edited_to: str | None = None
        self.comments: list[str] = []

    def edit(self, body: str):
        self.edited_to = body
        self.body = body

    def create_comment(self, body: str):
        self.comments.append(body)


class _Repo:
    def __init__(self, issues=None):
        self._issues = issues or []
        self.created: list[dict] = []
        self.original = _Issue(7)

    def get_issues(self, **kwargs):
        return list(self._issues)

    def create_issue(self, title, body, labels):
        issue = _Issue(99, body)
        self.created.append({"title": title, "body": body, "labels": labels})
        return issue

    def get_issue(self, number):
        return self.original


def _install(monkeypatch, repo):
    monkeypatch.setattr(spec_issue.gh, "get_repo", lambda _r: repo)
    monkeypatch.setattr(spec_issue, "_ensure_labels", lambda _r: None)


def _spec() -> TaskSpec:
    return TaskSpec(
        summary="Fix the accent handling",
        acceptance_criteria=["passwords with accents authenticate"],
        files_to_create=["tests/test_login.py"],
        files_to_modify=["auth/login.py"],
        test_strategy="pytest",
        tech_notes="normalise to NFC",
    )


def test_a_spec_issue_is_created_and_linked_both_ways(monkeypatch):
    repo = _Repo()
    _install(monkeypatch, repo)

    number = spec_issue.publish_spec("o/r", 7, "login breaks", _spec())

    assert number == 99
    created = repo.created[0]
    assert created["title"] == "[Spec] login breaks"
    assert "Implements #7" in created["body"]
    assert created["labels"] == ["devfactory:spec"]
    # And the original issue points at it, so a human finds it without searching.
    assert "#99" in repo.original.comments[0]


def test_the_body_carries_the_fields_the_next_stage_needs(monkeypatch):
    repo = _Repo()
    _install(monkeypatch, repo)

    spec_issue.publish_spec("o/r", 7, "t", _spec())

    body = repo.created[0]["body"]
    assert "passwords with accents authenticate" in body
    assert "`auth/login.py`" in body
    assert "`tests/test_login.py`" in body
    assert "normalise to NFC" in body


def test_a_second_run_updates_the_spec_instead_of_duplicating_it(monkeypatch):
    """A pipeline that opened a new spec issue per attempt would bury the original
    under its own retries."""
    existing = _Issue(55, body="<!-- devfactory:spec-for:7 -->\nold text")
    repo = _Repo(issues=[existing])
    _install(monkeypatch, repo)

    number = spec_issue.publish_spec("o/r", 7, "t", _spec())

    assert number == 55
    assert repo.created == []
    assert "Fix the accent handling" in (existing.edited_to or "")


def test_a_spec_for_another_issue_is_not_mistaken_for_this_one(monkeypatch):
    repo = _Repo(issues=[_Issue(55, body="<!-- devfactory:spec-for:8 -->\nother")])
    _install(monkeypatch, repo)

    number = spec_issue.publish_spec("o/r", 7, "t", _spec())

    assert number == 99, "should have created a new one"


def test_the_marker_survives_a_human_retitling_the_issue(monkeypatch):
    """Matching on the title would break the moment someone improves it — which is
    the point of publishing something a human can edit."""
    existing = _Issue(55, body="<!-- devfactory:spec-for:7 -->\nrewritten by a human")
    repo = _Repo(issues=[existing])
    _install(monkeypatch, repo)

    assert spec_issue.publish_spec("o/r", 7, "a completely different title", _spec()) == 55


# ── Reading it back ──────────────────────────────────────────────────────────


def test_what_is_published_is_what_is_read_back():
    """The body is the wire format between the analyst and everyone else, so the
    round trip must be lossless for every field."""
    spec = _spec()

    assert spec_issue.parse_body(spec_issue._build_body(7, spec)) == spec


def test_a_human_amendment_is_read_as_written():
    body = "\n".join(
        [
            "<!-- devfactory:spec-for:7 -->",
            "Implements #7",
            "",
            "## Summary",
            "",
            "Rewritten by a human, over two",
            "lines.",
            "",
            "## Acceptance criteria",
            "",
            "- [x] one already ticked",
            "- [ ] a criterion with `inline code` in it",
            "* a star bullet",
            "",
            "## Files to modify",
            "",
            "- `auth/login.py`",
            "- auth/session.py",
            "",
            "## Notes from the reviewer",
            "",
            "- not a criterion",
        ]
    )

    spec = spec_issue.parse_body(body)

    assert spec.summary == "Rewritten by a human, over two\nlines."
    assert spec.acceptance_criteria == [
        "one already ticked",
        "a criterion with `inline code` in it",
        "a star bullet",
    ]
    assert spec.files_to_modify == ["auth/login.py", "auth/session.py"]
    # An unknown heading is ignored, not misfiled into the section before it.
    assert spec.files_to_create == []


def test_a_missing_section_is_empty_not_an_error():
    spec = spec_issue.parse_body("## Summary\n\njust this")

    assert spec.summary == "just this"
    assert spec.acceptance_criteria == []
    assert spec.tech_notes == ""


def _ctx(spec_issue_number: int | None) -> PipelineContext:
    ctx = PipelineContext(
        issue=GitHubIssue(number=7, title="t", body="b", repo="o/r", labels=[], url="https://x/7")
    )
    ctx.spec_issue_number = spec_issue_number
    return ctx


def test_spec_for_reads_the_issue_the_context_points_at(monkeypatch):
    class _Repo:
        def get_issue(self, number):
            assert number == 99
            return _Issue(99, body=spec_issue._build_body(7, _spec()))

    monkeypatch.setattr(spec_issue.gh, "get_repo", lambda _r: _Repo())

    assert spec_issue.spec_for(_ctx(99)) == _spec()


def test_spec_for_refuses_to_proceed_without_a_published_spec():
    """A stage that went on without one would work from the issue title."""
    with pytest.raises(spec_issue.SpecNotPublishedError, match="issue #7"):
        spec_issue.spec_for(_ctx(None))


# ── One specification per issue ──────────────────────────────────────────────


class _PaginatedIssues:
    """A stand-in for PyGithub's ``PaginatedList``.

    Pages are fetched as the iteration crosses them, which is exactly what makes a
    reader that stops early miss what is on the later ones.
    """

    def __init__(self, pages: list[list[_Issue]]):
        self._pages = pages
        self.pages_read = 0

    def __iter__(self):
        for page in self._pages:
            self.pages_read += 1
            yield from page


class _PagedRepo(_Repo):
    """A repository whose issue listing arrives one page at a time."""

    def __init__(self, pages: list[list[_Issue]]):
        super().__init__()
        self.listing = _PaginatedIssues(pages)

    def get_issues(self, **kwargs):
        return self.listing


def test_two_spec_issues_for_one_issue_stop_the_run(monkeypatch):
    """Picking one of them is the silent failure: the developer can build from one
    while a human amends the other."""
    repo = _Repo(
        issues=[
            _Issue(56, body="<!-- devfactory:spec-for:7 -->\nthe duplicate"),
            _Issue(55, body="<!-- devfactory:spec-for:7 -->\nthe first one"),
        ]
    )
    _install(monkeypatch, repo)

    with pytest.raises(spec_issue.AmbiguousSpecError) as raised:
        spec_issue.publish_spec("o/r", 7, "t", _spec())

    message = str(raised.value)
    # Both numbers, or the human cannot act on it.
    assert "#55" in message
    assert "#56" in message
    assert "#7" in message
    assert repo.created == [], "an ambiguity must not be resolved by adding a third"


def test_the_oldest_spec_issue_is_the_one_the_message_points_at(monkeypatch):
    repo = _Repo(
        issues=[
            _Issue(56, body="<!-- devfactory:spec-for:7 -->"),
            _Issue(55, body="<!-- devfactory:spec-for:7 -->"),
        ]
    )
    _install(monkeypatch, repo)

    with pytest.raises(spec_issue.AmbiguousSpecError, match=r"Keep one — #55"):
        spec_issue.find_spec_issue("o/r", 7)


def test_one_spec_issue_is_returned(monkeypatch):
    existing = _Issue(55, body="<!-- devfactory:spec-for:7 -->")
    _install(monkeypatch, _Repo(issues=[existing]))

    assert spec_issue.find_spec_issue("o/r", 7) is existing


def test_no_spec_issue_yet_is_not_an_error(monkeypatch):
    _install(monkeypatch, _Repo(issues=[_Issue(55, body="<!-- devfactory:spec-for:8 -->")]))

    assert spec_issue.find_spec_issue("o/r", 7) is None


def test_the_lookup_reads_the_whole_listing_not_the_first_page(monkeypatch):
    """A spec issue on a later page is the one a run would duplicate."""
    repo = _PagedRepo(
        pages=[
            [_Issue(51, body="<!-- devfactory:spec-for:1 -->")],
            [_Issue(52, body="<!-- devfactory:spec-for:2 -->")],
            [_Issue(53, body="<!-- devfactory:spec-for:7 -->")],
        ]
    )
    _install(monkeypatch, repo)

    found = spec_issue.find_spec_issue("o/r", 7)

    assert found is not None
    assert found.number == 53
    assert repo.listing.pages_read == 3, "every page must be read, not just the first"


def test_a_duplicate_on_a_later_page_is_still_seen(monkeypatch):
    """Stopping at the first match is what makes a duplicate invisible."""
    repo = _PagedRepo(
        pages=[
            [_Issue(55, body="<!-- devfactory:spec-for:7 -->")],
            [_Issue(56, body="<!-- devfactory:spec-for:7 -->")],
        ]
    )
    _install(monkeypatch, repo)

    with pytest.raises(spec_issue.AmbiguousSpecError, match="#56"):
        spec_issue.find_spec_issue("o/r", 7)
