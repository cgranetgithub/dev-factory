"""
Tests for the developer → verification → review loop.

All agents and git operations are stubbed: these assert the control flow — which
gate sends the change back, how the shared budget is consumed, and what happens
when the reviewer is still unsatisfied at the end.
"""

from __future__ import annotations

import pytest

from devfactory.context import (
    GitHubIssue,
    PipelineContext,
    ReviewResult,
    TaskSpec,
    VerificationReport,
)
from devfactory.orchestrator import Pipeline, VerificationFailedError


def _report(passed: bool) -> VerificationReport:
    return VerificationReport(
        passed=passed,
        ruff={"issues": []},
        mypy={"errors": []},
        bandit={"severity": "none"},
        pytest={"passed": 1, "failed": 0, "errors": []},
        summary="PASSED" if passed else "FAILED",
        raw_output="{}",
    )


def _review(verdict: str) -> ReviewResult:
    return ReviewResult(
        model="glm-4.7-flash:latest",
        verdict=verdict,
        summary="s",
        inline_comments=[],
        score=0.8,
    )


class _Recorder:
    """Stands in for an agent: records each call and applies a scripted outcome."""

    def __init__(self, outcomes, apply):
        self.outcomes = list(outcomes)
        self.apply = apply
        self.calls = 0

    def execute(self, ctx):
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        self.apply(ctx, outcome)
        return ctx


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """A Pipeline whose git operations and autofix are inert."""
    from devfactory.config import settings
    from devfactory.github import git_ops
    from devfactory.verification import autofix as autofix_module

    monkeypatch.setattr(settings, "workspace", tmp_path)
    monkeypatch.setattr(git_ops, "commit_changes", lambda ctx, attempt=1: "sha")
    monkeypatch.setattr(git_ops, "changed_python_files", lambda ctx: [])
    monkeypatch.setattr(git_ops, "get_diff", lambda ctx: "diff")
    monkeypatch.setattr(autofix_module, "autofix", lambda *a, **k: 0)

    from devfactory.kb import database

    monkeypatch.setattr(database.db, "update_task", lambda *a, **k: None)

    p = Pipeline()
    p.developer = _Recorder([None], lambda ctx, _: None)
    return p


def _ctx() -> PipelineContext:
    issue = GitHubIssue(number=1, title="t", body="b", repo="o/r", labels=[], url="https://x/1")
    ctx = PipelineContext(issue=issue)
    ctx.task_spec = TaskSpec(
        summary="s",
        acceptance_criteria=["c"],
        files_to_create=[],
        files_to_modify=[],
        test_strategy="",
        tech_notes="",
        raw="{}",
    )
    return ctx


def _set_report(ctx, passed):
    ctx.verification_report = _report(passed)


def _set_review(ctx, verdict):
    ctx.review_results.append(_review(verdict))


def test_approved_review_ends_the_loop(pipeline):
    pipeline.verification = _Recorder([True], _set_report)
    pipeline.reviewer = _Recorder(["approved"], _set_review)

    ctx = pipeline._build_loop(_ctx(), task_id=1)

    assert pipeline.developer.calls == 1
    assert ctx.review_rejections == 0
    assert ctx.review_unresolved is False


def test_a_comment_is_not_a_blocker(pipeline):
    """Only changes_requested sends the change back; a suggestion does not."""
    pipeline.verification = _Recorder([True], _set_report)
    pipeline.reviewer = _Recorder(["commented"], _set_review)

    ctx = pipeline._build_loop(_ctx(), task_id=1)

    assert pipeline.developer.calls == 1
    assert ctx.review_rejections == 0


def test_review_sends_the_change_back_to_the_developer(pipeline):
    pipeline.verification = _Recorder([True], _set_report)
    pipeline.reviewer = _Recorder(["changes_requested", "approved"], _set_review)

    ctx = pipeline._build_loop(_ctx(), task_id=1)

    assert pipeline.developer.calls == 2
    assert ctx.review_rejections == 1
    assert ctx.review_unresolved is False


def test_the_reviewer_never_sees_code_that_failed_verification(pipeline):
    """The expensive gate must not run on code the cheap one already rejected."""
    pipeline.verification = _Recorder([False, True], _set_report)
    pipeline.reviewer = _Recorder(["approved"], _set_review)

    pipeline._build_loop(_ctx(), task_id=1)

    assert pipeline.verification.calls == 2
    assert pipeline.reviewer.calls == 1


def test_both_gates_share_one_budget(pipeline, monkeypatch):
    """A change alternating between the two gates must still terminate."""
    from devfactory.config import settings

    monkeypatch.setattr(settings, "max_verification_retries", 3)
    pipeline.verification = _Recorder([True, False, True], _set_report)
    pipeline.reviewer = _Recorder(["changes_requested"], _set_review)

    ctx = pipeline._build_loop(_ctx(), task_id=1)

    # One rejection, one verification failure, one rejection = budget spent.
    assert ctx.iterations_used == 3
    assert ctx.review_unresolved is True


def test_exhausted_budget_on_verification_raises(pipeline, monkeypatch):
    from devfactory.config import settings

    monkeypatch.setattr(settings, "max_verification_retries", 2)
    pipeline.verification = _Recorder([False], _set_report)
    pipeline.reviewer = _Recorder(["approved"], _set_review)

    with pytest.raises(VerificationFailedError):
        pipeline._build_loop(_ctx(), task_id=1)

    assert pipeline.reviewer.calls == 0


def test_exhausted_budget_on_review_opens_the_pr_and_flags_it(pipeline, monkeypatch):
    """Verification passes but the reviewer is never satisfied: the change goes to
    the human rather than being dropped, with the unsatisfied gate recorded."""
    from devfactory.config import settings

    monkeypatch.setattr(settings, "max_verification_retries", 2)
    pipeline.verification = _Recorder([True], _set_report)
    pipeline.reviewer = _Recorder(["changes_requested"], _set_review)

    ctx = pipeline._build_loop(_ctx(), task_id=1)

    assert ctx.review_unresolved is True
    assert ctx.review_rejections == 2
