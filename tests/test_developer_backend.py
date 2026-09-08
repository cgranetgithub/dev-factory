"""
Tests for the developer agent.

The harness is exercised with a mocked subprocess, so no real CLI or model is
invoked — these assert the command line and the knowledge-base record. GitHub is
stubbed at the spec issue: the developer reads its specification from there.
"""

from __future__ import annotations

import subprocess

import pytest

from devfactory.agents.developer import DeveloperAgent
from devfactory.config import settings
from devfactory.context import GitHubIssue, PipelineContext, TaskSpec
from devfactory.github import spec_issue
from devfactory.models.registry import ModelMeta
from tests.test_opencode_runner import _FakePopen

_SPEC = TaskSpec(
    summary="Implement subtract(a, b).",
    acceptance_criteria=["subtract(5, 3) == 2"],
    files_to_create=["tests/test_calc.py"],
    files_to_modify=["calc.py"],
    test_strategy="pytest for add and subtract",
    tech_notes="keep it minimal",
)


class _SpecIssueOnGitHub:
    """The spec issue as GitHub holds it — a body a human can edit at any time."""

    def __init__(self, spec: TaskSpec):
        self.body = spec_issue._build_body(42, spec)

    def get_repo(self, _name):
        return self

    def get_issue(self, number):
        assert number == 7
        return self


@pytest.fixture(autouse=True)
def spec_on_github(monkeypatch) -> _SpecIssueOnGitHub:
    published = _SpecIssueOnGitHub(_SPEC)
    monkeypatch.setattr(spec_issue, "gh", published)
    return published


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
    ctx.spec_issue_number = 7
    return ctx


def _agent_with_model() -> DeveloperAgent:
    agent = DeveloperAgent()
    # Bypass router selection: set the model directly, as execute() would.
    agent._model = ModelMeta(
        name="qwen3-coder:30b", parameters_b=30, context_k=32, roles=["developer"]
    )
    return agent


def test_opencode_backend_invokes_cli_and_logs_execution(monkeypatch, tmp_path):
    """run() builds the expected harness command and records a developer execution."""
    # The workspace repo must exist (repo_name == "repo").
    monkeypatch.setattr(settings, "workspace", tmp_path)
    (tmp_path / "repo").mkdir()
    monkeypatch.setattr(settings, "opencode_bin", "/fake/opencode")
    monkeypatch.setattr(settings, "opencode_timeout_s", 123)

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakePopen(cmd, stdout="done")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

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
    # The task prompt (last arg) carries the issue title and the published spec.
    assert "Add a subtract function" in cmd[-1]
    assert "subtract(5, 3) == 2" in cmd[-1]
    # The timeouts are enforced by the watchdog around the process, not by a
    # keyword on the call — see devfactory.opencode._run_with_watchdog.
    assert captured["kwargs"]["env"]["OPENCODE_CONFIG_CONTENT"]

    # Exactly one developer execution recorded for the KB.
    dev_execs = [e for e in ctx.execution_log if e["agent"] == "developer"]
    assert len(dev_execs) == 1
    assert dev_execs[0]["model"] == "qwen3-coder:30b"


def test_opencode_backend_raises_on_nonzero_exit(monkeypatch, tmp_path):
    """A failed opencode run raises RuntimeError so the pipeline can react."""
    monkeypatch.setattr(settings, "workspace", tmp_path)
    (tmp_path / "repo").mkdir()

    def fake_popen(cmd, **kwargs):
        return _FakePopen(cmd, stderr="boom", returncode=1)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    agent = _agent_with_model()
    with pytest.raises(RuntimeError, match="opencode run failed"):
        agent.run(_make_ctx())


def test_amending_the_spec_issue_changes_what_the_developer_builds(
    monkeypatch, tmp_path, spec_on_github
):
    """The issue is the specification. Nothing is copied from it when the analyst
    runs, so an amendment made between two iterations reaches the next one."""
    monkeypatch.setattr(settings, "workspace", tmp_path)
    (tmp_path / "repo").mkdir()
    prompts: list[str] = []

    def fake_popen(cmd, **kwargs):
        prompts.append(cmd[-1])
        return _FakePopen(cmd, stdout="done")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    agent = _agent_with_model()

    agent.run(_make_ctx())
    # A human edits the issue on GitHub between the two runs.
    spec_on_github.body = spec_on_github.body.replace(
        "subtract(5, 3) == 2", "subtract(5, 3) == 2 and subtract(0, 0) == 0"
    )
    agent.run(_make_ctx())

    assert "subtract(0, 0) == 0" not in prompts[0]
    assert "subtract(0, 0) == 0" in prompts[1]


def test_opencode_run_passes_the_generated_config(monkeypatch, tmp_path):
    """The config must actually reach the subprocess, not just be built."""
    monkeypatch.setattr(settings, "workspace", tmp_path)
    (tmp_path / "repo").mkdir()
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakePopen(cmd)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    _agent_with_model().run(_make_ctx())

    assert "OPENCODE_CONFIG_CONTENT" in captured["env"]


def test_an_agent_can_be_kept_off_another_roles_model():
    """Separation of duties: the reviewer must not be handed the developer's model."""
    from devfactory.agents.reviewer import ReviewerAgent

    ctx = _make_ctx()
    ctx.model_assignments["developer"] = "qwen3-coder:30b"

    assert ReviewerAgent()._models_to_avoid(ctx) == ["qwen3-coder:30b"]


def test_no_exclusion_when_the_other_role_has_not_run_yet():
    """The reviewer can run before the developer in a re-review; nothing to avoid."""
    from devfactory.agents.reviewer import ReviewerAgent

    assert ReviewerAgent()._models_to_avoid(_make_ctx()) is None
