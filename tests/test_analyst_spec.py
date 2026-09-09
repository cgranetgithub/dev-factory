"""
Tests for the analyst: read the codebase, write a specification, publish it.

OpenCode and GitHub are both stubbed — no subprocess, no network, no model.
"""

from __future__ import annotations

import pytest

from devfactory.agents.analyst import AnalystAgent, AnalystFailedError, _unusable_because
from devfactory.context import GitHubIssue, PipelineContext, TaskSpec
from devfactory.models.registry import ModelMeta
from devfactory.opencode import OpenCodeResult

_GOOD = '{"summary": "s", "acceptance_criteria": ["c"], "files_to_modify": ["a.py"]}'


def _spec(summary="do the thing", criteria=("it works",)) -> TaskSpec:
    return TaskSpec(
        summary=summary,
        acceptance_criteria=list(criteria),
        files_to_create=[],
        files_to_modify=["a.py"],
        test_strategy="pytest",
        tech_notes="",
    )


def _ctx() -> PipelineContext:
    return PipelineContext(
        issue=GitHubIssue(
            number=1,
            title="login breaks",
            body="it breaks",
            repo="o/r",
            labels=[],
            url="https://x/1",
        )
    )


def _agent(monkeypatch, tmp_path, outputs: list[str]) -> tuple[AnalystAgent, dict]:
    """An analyst whose OpenCode runs and GitHub publishing are recorded."""
    from devfactory.agents import analyst as analyst_module
    from devfactory.config import settings

    monkeypatch.setattr(settings, "workspace", tmp_path)
    (tmp_path / "r").mkdir(exist_ok=True)

    agent = AnalystAgent()
    agent._model = ModelMeta(name="gemma4:26b", parameters_b=26, context_k=32, roles=["analyst"])
    monkeypatch.setattr(agent, "load_prompt", lambda *a, **k: "system")

    seen: dict = {"prompts": [], "published": []}
    it = iter(outputs)

    def fake_run(prompt, **kwargs):
        seen["prompts"].append(prompt)
        seen["kwargs"] = kwargs
        return OpenCodeResult(output=next(it), duration_ms=1000)

    monkeypatch.setattr(analyst_module.opencode, "run", fake_run)
    monkeypatch.setattr(
        analyst_module,
        "publish_spec",
        lambda repo, number, title, spec: seen["published"].append((repo, number, title)) or 42,
    )
    return agent, seen


# ── What makes a specification usable ────────────────────────────────────────


def test_a_complete_spec_is_usable():
    assert _unusable_because(_spec()) is None


def test_an_empty_summary_is_not_usable():
    assert _unusable_because(_spec(summary="  ")) == "the summary is empty"


def test_no_acceptance_criteria_is_not_usable():
    assert _unusable_because(_spec(criteria=())) == "there are no acceptance criteria"


# ── Reading the codebase ─────────────────────────────────────────────────────


def test_the_analyst_reads_the_codebase_without_touching_it(monkeypatch, tmp_path):
    """The whole point of the change: it works in the checkout, read-only."""
    agent, seen = _agent(monkeypatch, tmp_path, [_GOOD])

    agent.run(_ctx())

    assert seen["kwargs"]["read_only"] is True
    assert seen["kwargs"]["repo_path"] == tmp_path / "r"


def test_the_prompt_tells_it_to_read_before_answering(monkeypatch, tmp_path):
    """A model that answers from the issue text alone invents filenames — which is
    what it did before it had the code."""
    agent, seen = _agent(monkeypatch, tmp_path, [_GOOD])

    agent.run(_ctx())

    prompt = seen["prompts"][0]
    assert "Read the code before" in prompt
    assert "never ones you have not" in prompt


# ── Publishing ───────────────────────────────────────────────────────────────


def test_the_spec_is_published_and_the_context_points_at_it(monkeypatch, tmp_path):
    agent, seen = _agent(monkeypatch, tmp_path, [_GOOD])

    ctx = agent.run(_ctx())

    assert seen["published"] == [("o/r", 1, "login breaks")]
    assert ctx.spec_issue_number == 42


def test_nothing_is_published_when_no_usable_spec_was_produced(monkeypatch, tmp_path):
    """Publishing an empty specification would be worse than publishing none: the
    developer would work from it."""
    agent, seen = _agent(monkeypatch, tmp_path, ["nope", "still nope", "nope again"])

    with pytest.raises(AnalystFailedError):
        agent.run(_ctx())

    assert seen["published"] == []


# ── Retrying ─────────────────────────────────────────────────────────────────


def test_an_unusable_answer_is_retried_with_the_reason(monkeypatch, tmp_path):
    agent, seen = _agent(monkeypatch, tmp_path, ["I think we should refactor.", _GOOD])

    ctx = agent.run(_ctx())

    assert ctx.spec_issue_number == 42
    assert "no acceptance criteria" in seen["prompts"][1]


def test_a_run_stops_rather_than_proceeding_on_an_empty_plan(monkeypatch, tmp_path):
    agent, _ = _agent(monkeypatch, tmp_path, ["nope", "still nope", "nope again"])

    with pytest.raises(AnalystFailedError, match="could not produce a usable specification"):
        agent.run(_ctx())
