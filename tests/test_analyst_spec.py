"""
Tests for the analyst refusing to hand the pipeline an empty plan.

Written from a real run: the analyst's output did not parse, the fallback
produced a spec with no summary and no criteria, and the pipeline carried on. The
developer received a task consisting of an issue title, and the scope gate — which
compares the change against the declared files — checked nothing, because nothing
was declared.
"""

from __future__ import annotations

import pytest

from devfactory.agents.analyst import AnalystAgent, AnalystFailedError, _unusable_because
from devfactory.context import GitHubIssue, PipelineContext, TaskSpec
from devfactory.models.client import LLMResponse


def _spec(summary="do the thing", criteria=("it works",)) -> TaskSpec:
    return TaskSpec(
        summary=summary,
        acceptance_criteria=list(criteria),
        files_to_create=[],
        files_to_modify=["a.py"],
        test_strategy="pytest",
        tech_notes="",
        raw="{}",
    )


def test_a_complete_spec_is_usable():
    assert _unusable_because(_spec()) is None


def test_an_empty_summary_is_not_usable():
    assert _unusable_because(_spec(summary="  ")) == "the summary is empty"


def test_no_acceptance_criteria_is_not_usable():
    assert _unusable_because(_spec(criteria=())) == "there are no acceptance criteria"


def _agent(monkeypatch, replies: list[str]) -> AnalystAgent:
    agent = AnalystAgent()
    monkeypatch.setattr(agent, "load_prompt", lambda *a, **k: "system")
    it = iter(replies)
    monkeypatch.setattr(
        agent,
        "chat",
        lambda *a, **k: LLMResponse(
            content=next(it), model="m", prompt_tokens=0, completion_tokens=0, duration_ms=1
        ),
    )
    return agent


def _ctx() -> PipelineContext:
    return PipelineContext(
        issue=GitHubIssue(number=1, title="t", body="b", repo="o/r", labels=[], url="https://x/1")
    )


_GOOD = '{"summary": "s", "acceptance_criteria": ["c"], "files_to_modify": ["a.py"]}'


def test_a_usable_spec_is_accepted_on_the_first_try(monkeypatch):
    agent = _agent(monkeypatch, [_GOOD])

    ctx = agent.run(_ctx())

    assert ctx.task_spec is not None
    assert ctx.task_spec.acceptance_criteria == ["c"]


def test_the_analyst_is_asked_again_after_an_unusable_answer(monkeypatch):
    """The usual failure is a model wrapping its JSON in prose; one corrective
    turn fixes it, and it costs a single cheap call."""
    agent = _agent(monkeypatch, ["I think we should refactor the module.", _GOOD])

    ctx = agent.run(_ctx())

    assert ctx.task_spec is not None
    assert ctx.task_spec.summary == "s"


def test_a_run_stops_rather_than_proceeding_on_an_empty_plan(monkeypatch):
    """Silently continuing wastes a full GPU run and disables the scope gate."""
    agent = _agent(monkeypatch, ["nope", "still nope", "nope again"])

    with pytest.raises(AnalystFailedError, match="could not produce a usable TaskSpec"):
        agent.run(_ctx())
