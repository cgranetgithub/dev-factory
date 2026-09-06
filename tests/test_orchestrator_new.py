from unittest.mock import MagicMock, patch

import pytest

from devfactory.context import GitHubIssue, PipelineContext
from devfactory.orchestrator import EmptyChangesError, Pipeline


@pytest.fixture
def issue():
    return GitHubIssue(
        number=123,
        title="Test issue",
        repo="owner/repo",
        labels=[],
        url="https://github.com/owner/regex/issues/123",
        body="Test body",
    )


@pytest.fixture
def ctx(issue):
    return PipelineContext(issue=issue)


@pytest.fixture
def pipeline():
    with (
        patch("devfactory.agents.analyst.AnalystAgent", MagicMock()),
        patch("devfactory.agents.developer.DeveloperAgent", MagicMock()),
        patch("devfactory.agents.reviewer.ReviewerAgent", MagicMock()),
        patch("devfactory.agents.verification.VerificationAgent", MagicMock()),
    ):
        return Pipeline()


def test_pipeline_dev_produces_changes(pipeline, ctx, issue):
    pipeline.developer = MagicMock()
    pipeline.developer.execute.return_value = ctx
    pipeline.verification = MagicMock()
    pipeline.verification.execute.return_value = ctx
    ctx.verification_report = MagicMock(passed=True)
    pipeline.reviewer = MagicMock()
    pipeline.reviewer.execute.return_value = ctx
    ctx.review_results = [MagicMock(verdict="approved")]

    with (
        patch("devfactory.github.git_ops.setup_branch"),
        patch("devfactory.github.git_ops.has_changes", return_value=True),
        patch("devfactory.github.git_ops.changed_python_files", return_value=[]),
        patch("devfactory.github.git_ops.commit_changes"),
        patch("devfactory.github.git_ops.get_diff", return_value="diff"),
        patch("devfactory.github.git_ops.push_branch"),
        patch("devfactory.github.pr.create_or_update_pr", return_value=("url", 1)),
        patch("devfactory.github.review.post_review"),
        patch("devfactory.models.provisioning.prepare_host"),
        patch("devfactory.kb.database.db.create_task", return_value=1),
        patch("devfactory.kb.database.db.update_task"),
        patch("devfactory.kb.scorer.scorer.flush"),
    ):
        result = pipeline.run(issue)
        assert result == ctx
        assert pipeline.developer.execute.call_count == 1


def test_pipeline_dev_produces_no_changes_retry(pipeline, ctx, issue):
    pipeline.developer = MagicMock()
    pipeline.developer.execute.return_value = ctx
    pipeline.verification = MagicMock()
    pipeline.verification.execute.return_value = ctx
    ctx.verification_report = MagicMock(passed=True)
    pipeline.reviewer = MagicMock()
    pipeline.reviewer.execute.return_value = ctx
    ctx.review_results = [MagicMock(verdict="approved")]

    with (
        patch("devfactory.github.git_ops.setup_branch"),
        patch("devfactory.github.git_ops.has_changes", side_effect=[False, True]),
        patch("devfactory.github.git_ops.changed_python_files", return_value=[]),
        patch("devfactory.github.git_ops.commit_changes"),
        patch("devfactory.github.git_ops.get_diff", return_value="diff"),
        patch("devfactory.github.git_ops.push_branch"),
        patch("devfactory.github.pr.create_or_update_pr", return_value=("url", 1)),
        patch("devfactory.github.review.post_review"),
        patch("devfactory.models.provisioning.prepare_host"),
        patch("devfactory.kb.database.db.create_task", return_value=1),
        patch("devfactory.kb.database.db.update_task"),
        patch("devfactory.kb.scorer.scorer.flush"),
    ):
        result = pipeline.run(issue)
        assert result == ctx
        assert pipeline.developer.execute.call_count == 2


def test_pipeline_dev_produces_no_changes_exhausted(pipeline, ctx, issue):
    pipeline.developer = MagicMock()
    pipeline.developer.execute.return_value = ctx

    with (
        patch("devfactory.github.git_ops.setup_branch"),
        patch("devfactory.github.git_ops.has_changes", return_value=False),
        patch("devfactory.github.git_ops.changed_python_files", return_value=[]),
        patch("devfactory.github.git_ops.commit_changes"),
        patch("devfactory.models.provisioning.prepare_host"),
        patch("devfactory.kb.database.db.create_task", return_value=1),
        patch("devfactory.kb.database.db.update_task") as mock_update,
        patch("devfactory.kb.scorer.scorer.flush"),
    ):
        with pytest.raises(EmptyChangesError, match="Developer produced no changes"):
            pipeline.run(issue)

        mock_update.assert_any_call(1, status="error")
