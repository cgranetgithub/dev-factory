"""
Tests for the reviewer running in the repository rather than on a diff alone.

Written from three real failures. In each, the reviewer approved a defect that
was invisible in the diff and obvious one file away:

- #39  a fallback wrapped around an exception raised where it could never catch it
- #41  a module wired into nothing
- #60  a truncation that dropped the summary it existed to preserve
"""

from __future__ import annotations

import pytest

from devfactory.agents.reviewer import ReviewerAgent
from devfactory.context import GitHubIssue, PipelineContext, TaskSpec
from devfactory.models.registry import ModelMeta
from devfactory.opencode import OpenCodeResult

_APPROVED = '{"verdict": "approved", "summary": "s", "score": 0.9, "inline_comments": []}'


_SPEC = TaskSpec(
    summary="s",
    acceptance_criteria=["the accented password authenticates"],
    files_to_create=[],
    files_to_modify=["a.py"],
    test_strategy="",
    tech_notes="",
)


def _ctx() -> PipelineContext:
    ctx = PipelineContext(
        issue=GitHubIssue(number=1, title="t", body="b", repo="o/r", labels=[], url="https://x/1")
    )
    ctx.spec_issue_number = 42
    ctx.diff = "diff --git a/a.py b/a.py"
    return ctx


def _agent(monkeypatch, tmp_path, output=_APPROVED, dirty=False):
    from devfactory.agents import reviewer as reviewer_module
    from devfactory.config import settings

    monkeypatch.setattr(settings, "workspace", tmp_path)
    monkeypatch.setattr(reviewer_module.spec_issue, "spec_for", lambda ctx: _SPEC)
    (tmp_path / "r").mkdir(exist_ok=True)

    agent = ReviewerAgent()
    agent._model = ModelMeta(
        name="glm-4.7-flash:latest", parameters_b=32, context_k=32, roles=["reviewer"]
    )
    monkeypatch.setattr(agent, "load_prompt", lambda *a, **k: "system")

    seen: dict = {}

    def fake_run(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return OpenCodeResult(output=output, duration_ms=1000)

    monkeypatch.setattr(reviewer_module.opencode, "run", fake_run)
    monkeypatch.setattr(reviewer_module.git_ops, "working_tree_has_changes", lambda ctx: dirty)
    return agent, seen


def test_the_reviewer_runs_in_the_repository_read_only(monkeypatch, tmp_path):
    agent, seen = _agent(monkeypatch, tmp_path)

    agent.run(_ctx())

    assert seen["kwargs"]["read_only"] is True
    assert seen["kwargs"]["repo_path"] == tmp_path / "r"


def test_a_reviewer_that_edited_the_code_fails_the_run(monkeypatch, tmp_path):
    """OpenCode's plan agent forbids it. This checks rather than trusts, because
    the check is one call and the failure would otherwise be silent."""
    agent, _ = _agent(monkeypatch, tmp_path, dirty=True)

    with pytest.raises(RuntimeError, match="must not change the code it is judging"):
        agent.run(_ctx())


def test_the_prompt_tells_it_to_look_outside_the_diff(monkeypatch, tmp_path):
    agent, seen = _agent(monkeypatch, tmp_path)

    agent.run(_ctx())

    prompt = seen["prompt"]
    assert "Open the files the change calls into" in prompt
    assert "new code is actually reached" in prompt


def test_the_prompt_cites_the_specification_issue(monkeypatch, tmp_path):
    """The reviewer judges against the specification, so it must know where it is
    — and a human reading the review can follow the same link."""
    agent, seen = _agent(monkeypatch, tmp_path)

    agent.run(_ctx())

    assert "#42" in seen["prompt"]
    assert "the accented password authenticates" in seen["prompt"]


def test_the_verdict_still_drives_the_loop(monkeypatch, tmp_path):
    """The loop branches on this, so the structured parse must survive the move."""
    agent, _ = _agent(monkeypatch, tmp_path, output=_APPROVED)

    ctx = agent.run(_ctx())

    assert ctx.review_results[-1].verdict == "approved"


def test_the_reviewer_avoids_the_developers_model():
    """An agent reviewing its own work is not a review."""
    assert ReviewerAgent.avoid_models_from_roles == ["developer"]
