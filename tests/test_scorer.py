"""Tests for KB scorer and database."""

import tempfile
from pathlib import Path

from devfactory.context import GitHubIssue, PipelineContext, ReviewResult, VerificationReport
from devfactory.kb.database import Database
from devfactory.kb.scorer import Scorer


def make_db() -> Database:
    """Create an in-memory (temp file) database for tests."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Database(path=Path(tmp.name))


def make_issue():
    return GitHubIssue(number=42, title="Test", body="", repo="owner/repo", labels=[], url="")


def test_create_and_update_task():
    db = make_db()
    task_id = db.create_task(github_issue_id=42, repo="owner/repo")
    assert task_id > 0
    db.update_task(task_id, status="in_progress", branch_name="feature/issue-42-test")
    counts = db.task_counts()
    assert counts.get("in_progress") == 1


def test_upsert_model_idempotent():
    db = make_db()
    id1 = db.upsert_model("qwen2.5:7b", parameters_b=7)
    id2 = db.upsert_model("qwen2.5:7b", parameters_b=7)
    assert id1 == id2


def test_record_execution_and_score():
    db = make_db()
    task_id = db.create_task(42, "owner/repo")
    exec_id = db.record_execution(
        task_id=task_id,
        model_name="qwen2.5:7b",
        agent_type="developer",
        prompt_tokens=1000,
        completion_tokens=2000,
        duration_ms=5000,
    )
    assert exec_id > 0
    db.record_score(exec_id, "lint_score", 0.9, "clean")


def test_scorer_flush_qa():
    db = make_db()
    scorer = Scorer(database=db)
    task_id = db.create_task(42, "owner/repo")

    ctx = PipelineContext(issue=make_issue())
    ctx.log_execution("verification", "qwen2.5:7b", 3000, 500, 200)
    ctx.verification_report = VerificationReport(
        passed=True,
        ruff={"issues": []},
        mypy={"errors": []},
        bandit={"severity": "none"},
        pytest={"passed": 10, "failed": 0, "errors": []},
        summary="All good",
        raw_output="{}",
    )

    scorer.flush(ctx, task_id)
    stats = db.model_stats()
    assert any(r["role"] == "verification" for r in stats)


def test_scorer_flush_reviewer():
    db = make_db()
    scorer = Scorer(database=db)
    task_id = db.create_task(42, "owner/repo")

    ctx = PipelineContext(issue=make_issue())
    ctx.log_execution("reviewer", "mistral:7b", 8000, 2000, 1500)
    ctx.review_results.append(
        ReviewResult(
            model="mistral:7b",
            verdict="approved",
            summary="LGTM",
            inline_comments=[],
            score=0.9,
        )
    )

    scorer.flush(ctx, task_id)
    stats = db.model_stats()
    reviewer_stats = [r for r in stats if r["role"] == "reviewer"]
    assert len(reviewer_stats) > 0


def test_scorer_flush_with_verification_report():
    """Quality scores are attributed to the developer execution, from the
    verification report."""
    db = make_db()
    scorer = Scorer(database=db)
    task_id = db.create_task(42, "owner/repo")

    ctx = PipelineContext(issue=make_issue())
    ctx.log_execution("developer", "qwen2.5:7b", 3000, 500, 200)
    ctx.verification_report = VerificationReport(
        passed=True,
        ruff={"issues": [{"code": "F401", "location": "test.py:1"}]},
        mypy={"errors": []},
        bandit={"severity": "LOW"},
        pytest={"passed": 5, "failed": 0, "errors": []},
        summary="All good",
        raw_output="{}",
    )
    ctx.verification_attempts = 2

    scorer.flush(ctx, task_id)

    # Check that scores were recorded for the developer execution
    stats = db.model_stats()
    developer_stats = [r for r in stats if r["role"] == "developer"]
    assert len(developer_stats) > 0

    # Check the quality scores are present
    score_metrics = [r["metric"] for r in developer_stats]
    assert "tests_pass_rate" in score_metrics
    assert "lint_score" in score_metrics
    assert "security_score" in score_metrics
    assert "retry_count" in score_metrics


def test_scorer_flush_without_qa_report():
    """Test that only retry_count is recorded when no verification report is present."""
    db = make_db()
    scorer = Scorer(database=db)
    task_id = db.create_task(42, "owner/repo")

    ctx = PipelineContext(issue=make_issue())
    ctx.log_execution("developer", "qwen2.5:7b", 3000, 500, 200)
    ctx.verification_report = None
    ctx.verification_attempts = 3

    scorer.flush(ctx, task_id)

    # Check that only retry_count was recorded for the developer execution
    stats = db.model_stats()
    developer_stats = [r for r in stats if r["role"] == "developer"]
    assert len(developer_stats) > 0

    # Check that only retry_count is present (the quality scores are skipped)
    score_metrics = [r["metric"] for r in developer_stats]
    # Only retry_count should be present, not quality scores
    assert "retry_count" in score_metrics
    assert "tests_pass_rate" not in score_metrics
    assert "lint_score" not in score_metrics
    assert "security_score" not in score_metrics


def test_model_stats_empty():
    db = make_db()
    assert db.model_stats() == []


def _developer_ctx() -> PipelineContext:
    """A minimal finished run with one developer execution and a passing report."""
    ctx = PipelineContext(issue=make_issue())
    ctx.log_execution("developer", "qwen3-coder:30b", 3000, 500, 200)
    ctx.verification_report = VerificationReport(
        passed=True,
        ruff={"issues": []},
        mypy={"errors": []},
        bandit={"severity": "none"},
        pytest={"passed": 5, "failed": 0, "errors": []},
        summary="ok",
        raw_output="{}",
    )
    return ctx


def _scores_for(db, metric: str) -> list:
    return [r for r in db.model_stats() if r["role"] == "developer" and r["metric"] == metric]


def test_lint_left_behind_is_recorded_for_the_developer():
    """What the developer failed to clean up is scored separately from the
    post-autofix state, otherwise the pipeline flatters the model."""
    db = make_db()
    task_id = db.create_task(42, "owner/repo")
    ctx = _developer_ctx()
    ctx.lint_left_behind = [7, 2]

    Scorer(database=db).flush(ctx, task_id)

    recorded = _scores_for(db, "lint_left_behind")
    assert recorded, "lint_left_behind was not recorded"


def test_unmeasured_lint_is_not_recorded_as_zero():
    """An unmeasurable run must record nothing rather than a perfect score."""
    db = make_db()
    task_id = db.create_task(42, "owner/repo")
    ctx = _developer_ctx()
    ctx.lint_left_behind = [None, None]

    Scorer(database=db).flush(ctx, task_id)

    assert _scores_for(db, "lint_left_behind") == []
