"""
Tests for the developer backend dispatch (ollama vs opencode).

The opencode path is exercised with a mocked subprocess so no real CLI or model
is invoked — we assert the command line and the KB execution record.
"""

from __future__ import annotations

import subprocess

import pytest

from devfactory.agents.developer import DeveloperAgent
from devfactory.config import settings
from devfactory.context import GitHubIssue, PipelineContext, TaskSpec
from devfactory.models.registry import ModelMeta


def _make_ctx() -> PipelineContext:
    issue = GitHubIssue(
        number=42,
        title="Add a subtract function",
        body="body",
        repo="owner/repo",
        labels=["ready-for-dev"],
        url="https://github.com/owner/repo/issues/42",
    )
    ctx = PipelineContext(issue=issue)
    ctx.task_spec = TaskSpec(
        summary="Implement subtract(a, b).",
        acceptance_criteria=["subtract(5, 3) == 2"],
        files_to_create=["tests/test_calc.py"],
        files_to_modify=["calc.py"],
        test_strategy="pytest for add and subtract",
        tech_notes="keep it minimal",
        raw="{}",
    )
    return ctx


def _agent_with_model() -> DeveloperAgent:
    agent = DeveloperAgent()
    # Bypass router selection: set the model directly, as execute() would.
    agent._model = ModelMeta(
        name="qwen3-coder:30b", parameters_b=30, context_k=32, roles=["developer"]
    )
    return agent


def _patch_backends(monkeypatch, agent) -> dict:
    """Replace both backend methods with recorders; return the dict they write to."""
    called: dict = {}

    def record_opencode(ctx):
        called["opencode"] = True
        return ctx

    def record_ollama(ctx):
        called["ollama"] = True
        return ctx

    monkeypatch.setattr(agent, "_run_opencode", record_opencode)
    monkeypatch.setattr(agent, "_run_ollama", record_ollama)
    return called


def test_run_dispatches_to_opencode_backend(monkeypatch):
    """With dev_backend='opencode', run() calls the opencode path, not the ollama one."""
    monkeypatch.setattr(settings, "dev_backend", "opencode")
    agent = _agent_with_model()
    called = _patch_backends(monkeypatch, agent)

    agent.run(_make_ctx())

    assert called == {"opencode": True}


def test_run_dispatches_to_ollama_backend_by_default(monkeypatch):
    """With the default dev_backend='ollama', run() calls the single-shot path."""
    monkeypatch.setattr(settings, "dev_backend", "ollama")
    agent = _agent_with_model()
    called = _patch_backends(monkeypatch, agent)

    agent.run(_make_ctx())

    assert called == {"ollama": True}


def test_opencode_backend_invokes_cli_and_logs_execution(monkeypatch, tmp_path):
    """_run_opencode builds the expected command and records a developer execution."""
    # The workspace repo must exist (repo_name == "repo").
    monkeypatch.setattr(settings, "workspace", tmp_path)
    (tmp_path / "repo").mkdir()
    monkeypatch.setattr(settings, "dev_backend", "opencode")
    monkeypatch.setattr(settings, "opencode_bin", "/fake/opencode")
    monkeypatch.setattr(settings, "opencode_timeout_s", 123)

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    agent = _agent_with_model()
    ctx = agent.run(_make_ctx())

    cmd = captured["cmd"]
    assert cmd[0] == "/fake/opencode"
    assert cmd[1] == "run"
    assert "--auto" in cmd
    # Model reference is provider-prefixed for opencode.
    assert "-m" in cmd and "ollama/qwen3-coder:30b" in cmd
    # Runs in the workspace repo directory.
    assert "--dir" in cmd and str(tmp_path / "repo") in cmd
    # The task prompt (last arg) carries the issue title.
    assert "Add a subtract function" in cmd[-1]
    assert captured["kwargs"]["timeout"] == 123

    # Exactly one developer execution recorded for the KB.
    dev_execs = [e for e in ctx.execution_log if e["agent"] == "developer"]
    assert len(dev_execs) == 1
    assert dev_execs[0]["model"] == "qwen3-coder:30b"


def test_requires_agentic_loop_tracks_backend(monkeypatch):
    """The developer demands an agentic-loop driver only for the opencode backend."""
    agent = DeveloperAgent()

    monkeypatch.setattr(settings, "dev_backend", "opencode")
    assert agent.requires_agentic_loop() is True

    monkeypatch.setattr(settings, "dev_backend", "ollama")
    assert agent.requires_agentic_loop() is False


def test_opencode_backend_raises_on_nonzero_exit(monkeypatch, tmp_path):
    """A failed opencode run raises RuntimeError so the pipeline can react."""
    monkeypatch.setattr(settings, "workspace", tmp_path)
    (tmp_path / "repo").mkdir()
    monkeypatch.setattr(settings, "dev_backend", "opencode")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    agent = _agent_with_model()
    with pytest.raises(RuntimeError, match="opencode run failed"):
        agent.run(_make_ctx())
