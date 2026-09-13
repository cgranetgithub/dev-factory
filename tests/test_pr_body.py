"""
Tests for the pull request body — what a merge closes, and what it cites.

PyGithub is stubbed; nothing reaches the network.
"""

from __future__ import annotations

import pytest

from devfactory.context import GitHubIssue, PipelineContext, TaskSpec, VerificationReport
from devfactory.github import pr, spec_issue


class _SpecIssue:
    def __init__(self, body: str):
        self.body = body


class _Repo:
    def __init__(self, body: str):
        self._body = body

    def get_issue(self, number):
        return _SpecIssue(self._body)


def _spec() -> TaskSpec:
    return TaskSpec(
        summary="Normalise the password before comparing it",
        acceptance_criteria=["passwords with accents authenticate"],
        files_to_create=[],
        files_to_modify=["auth/login.py"],
        test_strategy="pytest",
        tech_notes="",
    )


def _ctx(spec_issue_number: int | None = 3) -> PipelineContext:
    ctx = PipelineContext(
        issue=GitHubIssue(number=1, title="login breaks", body="b", repo="o/r", labels=[], url="")
    )
    ctx.spec_issue_number = spec_issue_number
    return ctx


@pytest.fixture
def published_spec(monkeypatch):
    """The spec issue every stage reads, served from a stub."""
    repo = _Repo(spec_issue._build_body(1, _spec()))
    monkeypatch.setattr(spec_issue.gh, "get_repo", lambda _r: repo)


def test_the_merge_closes_the_issue_and_its_specification(published_spec):
    """The spec issue has nothing left to do once the implementation lands, and an
    open issue with no remaining work is a wrong record as much as it is noise."""
    body = pr._build_pr_body(_ctx(spec_issue_number=3))

    assert "Closes #1" in body
    assert "Closes #3" in body


def test_the_closing_reference_still_says_what_the_spec_issue_is(published_spec):
    """A bare "Closes #3" tells a reader nothing about what #3 is, and the link back
    to the specification is the half of the traceability chain the PR carries."""
    body = pr._build_pr_body(_ctx(spec_issue_number=3))

    assert "Closes #3 — the specification this was built from." in body


def test_a_pr_body_cannot_be_built_without_a_published_spec():
    """Nothing can reach the closing reference with no spec issue to close: the body
    is built from the specification, which is read from that issue."""
    with pytest.raises(spec_issue.SpecNotPublishedError, match="issue #1"):
        pr._build_pr_body(_ctx(spec_issue_number=None))


def _differential_report(inherited: int, fixed: int, introduced: list[str]) -> VerificationReport:
    """A verification report as the differential verdict leaves it (issue #104)."""
    return VerificationReport(
        passed=not introduced,
        ruff={"status": "findings", "issues": []},
        mypy={"status": "clean", "errors": []},
        bandit={"status": "clean", "findings": [], "severity": "none"},
        pytest={"status": "clean", "passed": 10, "failed": 0, "errors": []},
        summary="## Verification Report\n\n*Pass rule: differential*",
        raw_output="{}",
        rule="differential",
        differential={
            "rule": "differential",
            "reason": "",
            "baseline": {
                "repo": "o/r",
                "base_sha": "a5ac825e1f2b3c4d",
                "recorded_at": "2026-09-13T10:00:00",
                "cached": True,
            },
            "tools": {
                "ruff": {
                    "rule": "differential",
                    "reason": "",
                    "status": "findings",
                    "introduced": introduced,
                    "inherited_count": inherited,
                    "inherited_sample": [],
                    "fixed_count": fixed,
                    "fixed_sample": [],
                }
            },
        },
    )


def test_the_pr_body_shows_the_standing_debt_the_change_inherited(published_spec):
    """A differential gate passes a change on a repository that is not clean, so the
    body says what was already there and which commit that was measured on — the
    evidence rule in CLAUDE.md: reported, never dropped."""
    ctx = _ctx()
    ctx.verification_report = _differential_report(inherited=67, fixed=2, introduced=[])

    body = pr._build_pr_body(ctx)

    assert "Inherited findings — already present before this change" in body
    assert "`o/r@a5ac825e`" in body
    assert "| ruff | 67 | 2 |" in body


def test_an_absolute_verdict_claims_nothing_about_inherited_findings(published_spec):
    """Nothing is attributed under the absolute rule, so nothing is tabulated."""
    ctx = _ctx()
    report = _differential_report(inherited=0, fixed=0, introduced=[])
    report.rule = "absolute"
    report.differential = {"rule": "absolute", "reason": "no baseline", "tools": {}}
    ctx.verification_report = report

    body = pr._build_pr_body(ctx)

    assert "Inherited findings" not in body
