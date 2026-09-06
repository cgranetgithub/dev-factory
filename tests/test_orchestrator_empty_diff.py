"""
Tests for empty diff handling in the orchestrator.
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


def test_empty_diff_triggers_retry(pipeline, monkeypatch):
    """Empty diff should trigger a retry instead of pushing to GitHub."""
    from devfactory.github import git_ops

    # Mock git_ops.get_diff to return empty string
    monkeypatch.setattr(git_ops, "get_diff", lambda ctx: "")

    pipeline.verification = _Recorder([True], _set_report)
    pipeline.reviewer = _Recorder(["approved"], _set_review)

    # Mock push_branch to ensure it's not called
    original_push_branch = pipeline._push_branch
    push_branch_called = []

    def mock_push_branch(ctx):
        push_branch_called.append(True)
        return original_push_branch(ctx)

    monkeypatch.setattr(pipeline, "_push_branch", mock_push_branch)
    monkeypatch.setattr(git_ops, "get_diff", lambda ctx: "")

    # The test should still succeed because we retry and get to the end
    with pytest.raises(VerificationFailedError):
        pipeline._build_loop(_ctx(), task_id=1)

    # Check that get_diff was called
    assert pipeline.developer.calls == 1
    assert len(push_branch_called) == 0  # Push should not be called due to empty diff


def test_empty_diff_on_last_attempt_fails_cleanly(pipeline, monkeypatch):
    """Empty diff on the last attempt should fail with proper message."""
    from devfactory.config import settings
    from devfactory.github import git_ops

    monkeypatch.setattr(settings, "max_verification_retries", 1)
    monkeypatch.setattr(git_ops, "get_diff", lambda ctx: "")

    pipeline.verification = _Recorder([True], _set_report)
    pipeline.reviewer = _Recorder(["approved"], _set_review)

    # Mock push_branch to ensure it's not called
    original_push_branch = pipeline._push_branch
    push_branch_called = []

    def mock_push_branch(ctx):
        push_branch_called.append(True)
        return original_push_branch(ctx)

    monkeypatch.setattr(pipeline, "_push_branch", mock_push_branch)

    with pytest.raises(VerificationFailedError) as exc_info:
        pipeline._build_loop(_ctx(), task_id=1)

    # Check that it failed with the right message
    assert "Developer produced no changes" in str(exc_info.value)
    assert len(push_branch_called) == 0  # Push should not be called due to empty diff


def test_non_empty_diff_proceeds_normally(pipeline, monkeypatch):
    """Non-empty diff should proceed normally without retry."""
    from devfactory.github import git_ops

    monkeypatch.setattr(git_ops, "get_diff", lambda ctx: "some diff content")

    # Mock commit_changes to return a commit
    def mock_commit_changes(ctx, attempt=1):
        ctx.commits.append("sha123")
        return "sha123"

    monkeypatch.setattr(git_ops, "commit_changes", mock_commit_changes)
    monkeypatch.setattr(git_ops, "get_diff", lambda ctx: "some diff content")

    pipeline.verification = _Recorder([True], _set_report)
    pipeline.reviewer = _Recorder(["approved"], _set_review)

    # Mock push_branch to ensure it is called
    original_push_branch = pipeline._push_branch
    push_branch_called = []

    def mock_push_branch(ctx):
        push_branch_called.append(True)
        return original_push_branch(ctx)

    monkeypatch.setattr(pipeline, "_push_branch", mock_push_branch)

    pipeline._build_loop(_ctx(), task_id=1)

    assert pipeline.developer.calls == 1
    assert len(push_branch_called) == 1  # Push should be called in this case
