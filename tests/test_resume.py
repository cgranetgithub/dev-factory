"""
Tests for resuming a run: what the pipeline does before the graph picks up.

Everything around the analyst is stubbed — git, the knowledge base, the graph,
GitHub — so these assert one decision: whether the analyst runs again.
"""

from __future__ import annotations

import pytest

from devfactory import orchestrator
from devfactory.context import GitHubIssue
from devfactory.github import spec_issue
from devfactory.orchestrator import Pipeline


class _Analyst:
    def __init__(self):
        self.calls = 0

    def execute(self, ctx):
        self.calls += 1
        ctx.spec_issue_number = 99
        return ctx


class _PublishedSpec:
    number = 55


@pytest.fixture
def quiet_pipeline(monkeypatch):
    """A Pipeline whose every step but the analyst decision is inert."""
    monkeypatch.setattr(orchestrator.db, "create_task", lambda *a, **k: 1)
    monkeypatch.setattr(orchestrator.db, "update_task", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.scorer, "flush", lambda *a, **k: None)
    monkeypatch.setattr(Pipeline, "_mark", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(Pipeline, "_setup_git", lambda self, ctx: None)
    monkeypatch.setattr(Pipeline, "_build_loop", lambda self, ctx, task_id: ctx)
    monkeypatch.setattr(Pipeline, "_push_branch", lambda self, ctx: None)
    monkeypatch.setattr(Pipeline, "_create_pr", lambda self, ctx, task_id: ctx)
    monkeypatch.setattr(Pipeline, "_publish_review", lambda self, ctx: None)

    from devfactory.models import provisioning

    monkeypatch.setattr(provisioning, "prepare_host", lambda: None)

    def build(resume_thread=None):
        p = Pipeline(resume_thread=resume_thread)
        p.analyst = _Analyst()
        return p

    return build


def _issue() -> GitHubIssue:
    return GitHubIssue(number=7, title="t", body="b", repo="o/r", labels=[], url="https://x/7")


def test_a_fresh_run_runs_the_analyst(quiet_pipeline, monkeypatch):
    monkeypatch.setattr(spec_issue, "find_spec_issue", lambda repo, n: _PublishedSpec())
    pipeline = quiet_pipeline()

    ctx = pipeline.run(_issue())

    assert pipeline.analyst.calls == 1, "a spec from an earlier run is not this run's"
    assert ctx.spec_issue_number == 99


def test_a_resumed_run_reads_the_spec_it_already_published(quiet_pipeline, monkeypatch):
    """Running the analyst again would spend a model call to overwrite the issue —
    and with it whatever a human amended while the run was down."""
    monkeypatch.setattr(spec_issue, "find_spec_issue", lambda repo, n: _PublishedSpec())
    pipeline = quiet_pipeline(resume_thread="issue-7-20260908-101010")

    ctx = pipeline.run(_issue())

    assert pipeline.analyst.calls == 0
    assert ctx.spec_issue_number == 55


def test_a_resumed_run_without_a_spec_falls_back_to_the_analyst(quiet_pipeline, monkeypatch):
    monkeypatch.setattr(spec_issue, "find_spec_issue", lambda repo, n: None)
    pipeline = quiet_pipeline(resume_thread="issue-7-20260908-101010")

    pipeline.run(_issue())

    assert pipeline.analyst.calls == 1
