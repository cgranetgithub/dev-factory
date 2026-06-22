"""Tests for KB scorer and database."""

import tempfile
from pathlib import Path

from devfactory.context import GitHubIssue, PipelineContext, QAReport, ReviewResult
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
    ctx.log_execution("qa", "qwen2.5:7b", 3000, 500, 200)
    ctx.qa_report = QAReport(
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
    assert any(r["role"] == "qa" for r in stats)


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


def test_model_stats_empty():
    db = make_db()
    assert db.model_stats() == []
