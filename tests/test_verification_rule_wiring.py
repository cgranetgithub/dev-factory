"""
Tests for where the differential verdict is applied and who reads it (issue #104).

The diffing logic is tested in ``test_differential_gate``; this is about the wiring
around it — that the gate agent actually applies the rule, that turning it off
restores the absolute one, and that the developer's send-back carries only what
the change introduced. That last one is the reason the whole thing exists, so it
is asserted at the prompt, not at the report.

Docker, git and GitHub are stubbed; nothing here needs a container or a network.
"""

from __future__ import annotations

from pathlib import Path

from devfactory.agents.verification import VerificationAgent
from devfactory.context import GitHubIssue, PipelineContext, VerificationReport
from devfactory.verification.differential import ABSOLUTE, DIFFERENTIAL, BaselineRef, fingerprints
from devfactory.verification.runner import CLEAN, FINDINGS

BASE_SHA = "a5ac825e1f2b3c4d5e6f708192a3b4c5d6e7f809"


def _ruff(*messages: str) -> dict:
    return {
        "status": FINDINGS if messages else CLEAN,
        "issues": [
            {
                "filename": "/workspace/a.py",
                "location": {"row": i},
                "code": "F401",
                "message": message,
            }
            for i, message in enumerate(messages)
        ],
    }


def _report(ruff: dict) -> VerificationReport:
    return VerificationReport(
        passed=False,
        ruff=ruff,
        mypy={"status": CLEAN, "errors": []},
        bandit={"status": CLEAN, "findings": [], "severity": "none"},
        pytest={"status": CLEAN, "passed": 3, "failed": 0, "errors": [], "raw": ""},
        summary="absolute summary",
        raw_output="{}",
        environment="python 3.12 (from the documented default), install from uv.lock",
    )


def _ctx() -> PipelineContext:
    ctx = PipelineContext(
        issue=GitHubIssue(
            number=7, title="t", body="b", repo="cgranetgithub/news-watch", labels=[], url=""
        )
    )
    ctx.branch_name = "feature/issue-7-t"
    return ctx


def _agent(monkeypatch, head: VerificationReport, base: VerificationReport | None):
    """A verification agent whose gate and baseline store are both scripted."""
    from devfactory.github import git_ops
    from devfactory.verification import baseline as baseline_store

    agent = VerificationAgent()
    monkeypatch.setattr(agent._runner, "run", lambda path, repo=None: head)
    monkeypatch.setattr(git_ops, "base_sha", lambda ctx: BASE_SHA)
    monkeypatch.setattr(git_ops, "workspace_path", lambda ctx: Path("/nowhere"))

    if base is None:
        monkeypatch.setattr(
            baseline_store,
            "baseline_for",
            lambda **kwargs: (None, "the gate could not run on the base commit"),
        )
    else:
        found = baseline_store.Baseline(
            ref=BaselineRef(
                repo="cgranetgithub/news-watch",
                base_sha=BASE_SHA,
                recorded_at="2026-09-13T10:00:00",
                cached=True,
            ),
            tools=fingerprints(base),
        )
        monkeypatch.setattr(baseline_store, "baseline_for", lambda **kwargs: (found, ""))
    return agent


def test_the_gate_passes_a_change_that_introduced_nothing_on_a_dirty_repository(monkeypatch):
    """The verdict issue #104 is about: both targets fail their own main today."""
    dirty = _ruff("`os` imported but unused")
    agent = _agent(monkeypatch, _report(dirty), _report(dirty))

    ctx = agent.run(_ctx())

    assert ctx.verification_report is not None
    assert ctx.verification_report.passed
    assert ctx.verification_report.rule == DIFFERENTIAL


def test_the_gate_still_fails_a_change_that_introduced_a_finding(monkeypatch):
    agent = _agent(
        monkeypatch,
        _report(_ruff("`os` imported but unused", "`sys` imported but unused")),
        _report(_ruff("`os` imported but unused")),
    )

    ctx = agent.run(_ctx())

    assert ctx.verification_report is not None
    assert not ctx.verification_report.passed


def test_turning_the_setting_off_restores_the_absolute_rule(monkeypatch):
    """The strict reading stays one environment variable away, and says so."""
    from devfactory.config import settings

    monkeypatch.setattr(settings, "differential_gate", False)
    dirty = _ruff("`os` imported but unused")
    agent = _agent(monkeypatch, _report(dirty), _report(dirty))

    ctx = agent.run(_ctx())

    assert ctx.verification_report is not None
    assert not ctx.verification_report.passed
    assert ctx.verification_report.rule == ABSOLUTE
    assert "the differential gate is turned off" in ctx.verification_report.summary


def test_a_baseline_that_could_not_be_measured_falls_back_and_names_the_reason(monkeypatch):
    dirty = _ruff("`os` imported but unused")
    agent = _agent(monkeypatch, _report(dirty), None)

    ctx = agent.run(_ctx())

    assert ctx.verification_report is not None
    assert not ctx.verification_report.passed
    assert ctx.verification_report.rule == ABSOLUTE
    assert "could not run on the base commit" in ctx.verification_report.summary


def test_a_branch_with_no_findable_base_commit_falls_back(monkeypatch):
    from devfactory.github import git_ops

    dirty = _ruff("`os` imported but unused")
    agent = _agent(monkeypatch, _report(dirty), _report(dirty))
    monkeypatch.setattr(git_ops, "base_sha", lambda ctx: None)

    ctx = agent.run(_ctx())

    assert ctx.verification_report is not None
    assert ctx.verification_report.rule == ABSOLUTE
    assert "base commit could not be determined" in ctx.verification_report.summary


def test_the_developer_prompt_carries_only_what_the_change_introduced(monkeypatch):
    """The send-back is where an absolute gate burned the iteration budget."""
    from devfactory.agents.developer import DeveloperAgent
    from devfactory.context import TaskSpec
    from devfactory.github import spec_issue

    monkeypatch.setattr(
        spec_issue,
        "spec_for",
        lambda ctx: TaskSpec(
            summary="s",
            acceptance_criteria=["a"],
            files_to_create=[],
            files_to_modify=["new.py"],
            test_strategy="pytest",
            tech_notes="",
        ),
    )
    agent = _agent(
        monkeypatch,
        _report(_ruff("inherited noise", "introduced defect")),
        _report(_ruff("inherited noise")),
    )
    ctx = agent.run(_ctx())
    ctx.verification_attempts = 1

    prompt = DeveloperAgent.__new__(DeveloperAgent)._build_prompt(ctx)

    assert "introduced defect" in prompt
    assert "inherited noise" not in prompt
