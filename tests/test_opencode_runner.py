"""
Tests for the shared OpenCode runner.

Two agents drive OpenCode and a third is coming, so the invocation lives in one
place. These assert the parts that would silently break a run if they drifted: the
provider config, the ambient environment, and which agent is used.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devfactory import opencode
from devfactory.config import settings


class _FakePopen:
    """Stands in for the CLI process, with its output ready immediately."""

    def __init__(self, args, stdout="", stderr="", returncode=0):
        self.args = args
        self.returncode = returncode
        self._out = stdout
        self._err = stderr
        self.killed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def communicate(self, timeout=None):
        return self._out, self._err

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.returncode


def _capture(monkeypatch, returncode: int = 0, stdout: str = "ok") -> dict:
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakePopen(cmd, stdout=stdout, returncode=returncode)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return captured


def _run(tmp_path, **kwargs):
    return opencode.run(
        "do the thing",
        repo_path=tmp_path,
        model_name="qwen3-coder:30b",
        role="developer",
        **{"read_only": False, **kwargs},
    )


# ── The permission model ─────────────────────────────────────────────────────


def test_a_reading_agent_gets_the_read_only_agent(monkeypatch, tmp_path):
    """Read-only is enforced by OpenCode's own permissions, not by our restraint:
    the `plan` agent denies `edit` everywhere but its plans directory."""
    captured = _capture(monkeypatch)

    _run(tmp_path, read_only=True)

    cmd = captured["cmd"]
    assert "--agent" in cmd
    assert cmd[cmd.index("--agent") + 1] == opencode.READ_ONLY_AGENT


def test_a_writing_agent_gets_the_writing_agent(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)

    _run(tmp_path, read_only=False)

    cmd = captured["cmd"]
    assert cmd[cmd.index("--agent") + 1] == opencode.WRITING_AGENT


# ── The provider config ──────────────────────────────────────────────────────


def test_the_selected_model_is_declared(monkeypatch, tmp_path):
    """A model the registry allows but OpenCode cannot resolve fails at run time,
    after the analyst has already spent its time."""
    monkeypatch.setattr(settings, "ollama_base_url", "http://localhost:11434")
    captured = _capture(monkeypatch)

    _run(tmp_path)

    config = json.loads(captured["kwargs"]["env"]["OPENCODE_CONFIG_CONTENT"])
    assert "qwen3-coder:30b" in config["provider"]["ollama"]["models"]
    assert config["provider"]["ollama"]["options"]["baseURL"] == "http://localhost:11434/v1"


def test_the_v1_suffix_is_not_doubled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.local:11434/")
    captured = _capture(monkeypatch)

    _run(tmp_path)

    config = json.loads(captured["kwargs"]["env"]["OPENCODE_CONFIG_CONTENT"])
    assert config["provider"]["ollama"]["options"]["baseURL"] == "http://ollama.local:11434/v1"


def test_the_ambient_environment_survives(monkeypatch, tmp_path):
    """OpenCode runs pytest and ruff through PATH; dropping it would disable the
    developer's definition of done without any visible failure."""
    monkeypatch.setenv("DEVFACTORY_CANARY", "present")
    captured = _capture(monkeypatch)

    _run(tmp_path)

    env = captured["kwargs"]["env"]
    assert env["DEVFACTORY_CANARY"] == "present"
    assert "PATH" in env


# ── Failures are never silent ────────────────────────────────────────────────


def test_a_nonzero_exit_raises(monkeypatch, tmp_path):
    _capture(monkeypatch, returncode=1)

    with pytest.raises(RuntimeError, match="opencode run failed"):
        _run(tmp_path)


def test_a_missing_binary_says_what_to_do(monkeypatch, tmp_path):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("no such binary")

    monkeypatch.setattr(subprocess, "Popen", boom)

    with pytest.raises(RuntimeError, match="install it or set OPENCODE_BIN"):
        _run(tmp_path)


def test_a_missing_checkout_raises_before_starting(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)

    with pytest.raises(RuntimeError, match="workspace path not found"):
        _run(tmp_path / "absent")

    assert captured == {}, "no process should start without a checkout"


def test_stdout_is_returned_for_the_caller_to_parse(monkeypatch, tmp_path):
    """Callers expecting JSON parse this directly, so it must not be swallowed."""
    _capture(monkeypatch, stdout='```json\n{"a": 1}\n```')

    assert '"a": 1' in _run(tmp_path).output


# ── The startup watchdog ─────────────────────────────────────────────────────


class _HangingPopen(_FakePopen):
    """A process that never produces anything, like the hang seen in two runs."""

    def __init__(self, args, **kwargs):
        super().__init__(args, **kwargs)
        self.stdout = None
        self.stderr = None
        self.communicate_calls = 0

    def communicate(self, timeout=None):
        self.communicate_calls += 1
        raise subprocess.TimeoutExpired(cmd=self.args, timeout=timeout or 0)


def test_a_hung_run_is_abandoned_at_the_startup_deadline(monkeypatch, tmp_path):
    """Twice, OpenCode initialised and then sat there — Ollama idle, nothing
    written. Under the long timeout alone that costs half an hour to learn
    nothing."""
    monkeypatch.setattr(settings, "opencode_startup_timeout_s", 1)
    hung = _HangingPopen(["opencode"])
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: hung)

    with pytest.raises(RuntimeError, match="produced no output"):
        _run(tmp_path)

    assert hung.killed, "a hung CLI must not be left holding the model"


def test_a_slow_but_working_run_is_allowed_to_finish(monkeypatch, tmp_path):
    """Real work takes minutes. The startup deadline asks a narrower question —
    has it written anything — so it must not cut off a run that has."""
    monkeypatch.setattr(settings, "opencode_startup_timeout_s", 1)

    class _Slow(_HangingPopen):
        def communicate(self, timeout=None):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired(cmd=self.args, timeout=timeout or 0)
            return "the answer", ""

    slow = _Slow(["opencode"])
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: slow)
    # It has written something, so it is working rather than hung.
    monkeypatch.setattr("devfactory.opencode._has_written_anything", lambda p: True)

    assert _run(tmp_path).output == "the answer"
    assert not slow.killed


def test_the_long_timeout_still_bounds_real_work(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "opencode_startup_timeout_s", 1)
    monkeypatch.setattr(settings, "opencode_timeout_s", 2)
    hung = _HangingPopen(["opencode"])
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: hung)
    monkeypatch.setattr("devfactory.opencode._has_written_anything", lambda p: True)

    with pytest.raises(RuntimeError, match="timed out after"):
        _run(tmp_path)

    assert hung.killed
