"""Tests for PipelineContext."""

from devfactory.context import GitHubIssue, PipelineContext, QAReport, TaskSpec


def make_issue(**kwargs) -> GitHubIssue:
    defaults = {
        "number": 1,
        "title": "Test issue",
        "body": "Do something",
        "repo": "owner/repo",
        "labels": [],
        "url": "",
    }
    return GitHubIssue(**{**defaults, **kwargs})


def test_context_defaults():
    ctx = PipelineContext(issue=make_issue())
    assert ctx.branch_name == ""
    assert ctx.commits == []
    assert ctx.diff == ""
    assert ctx.qa_attempts == 0
    assert ctx.model_assignments == {}
    assert ctx.execution_log == []


def test_repo_owner_name():
    ctx = PipelineContext(issue=make_issue(repo="alice/myproject"))
    assert ctx.repo_owner == "alice"
    assert ctx.repo_name == "myproject"


def test_log_execution():
    ctx = PipelineContext(issue=make_issue())
    ctx.log_execution("analyst", "qwen2.5:7b", 1200, 500, 800)
    assert len(ctx.execution_log) == 1
    entry = ctx.execution_log[0]
    assert entry["agent"] == "analyst"
    assert entry["model"] == "qwen2.5:7b"
    assert entry["duration_ms"] == 1200


def test_task_spec_fields():
    spec = TaskSpec(
        summary="Build X",
        acceptance_criteria=["X works", "X has tests"],
        files_to_create=["x.py"],
        files_to_modify=[],
        test_strategy="pytest",
        tech_notes="use stdlib only",
        raw="...",
    )
    assert len(spec.acceptance_criteria) == 2
    assert spec.files_to_create == ["x.py"]


def test_qa_report_passed():
    report = QAReport(
        passed=True,
        ruff={"issues": []},
        mypy={"errors": []},
        bandit={"severity": "none"},
        pytest={"passed": 5, "failed": 0, "errors": []},
        summary="All good",
        raw_output="{}",
    )
    assert report.passed is True
