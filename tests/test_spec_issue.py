"""
Tests for publishing the specification as its own issue.

PyGithub is stubbed; nothing reaches the network.
"""

from __future__ import annotations

from devfactory.context import TaskSpec
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
        raw="{}",
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
