"""
Tests for the deterministic ruff pass that runs between the developer and QA.

Docker is mocked throughout — these assert the command that would be run, not the
result of running it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from devfactory.qa import autofix as autofix_module
from devfactory.qa.autofix import autofix
from devfactory.qa.runner import CONTAINER_WORKDIR


def _capture(monkeypatch, returncode: int = 0) -> dict:
    """Replace subprocess.run with a recorder; return the dict it writes to."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=cmd, returncode=returncode, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


def test_autofix_runs_ruff_on_the_given_files(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)

    assert autofix(tmp_path, ["devfactory/foo.py", "tests/test_foo.py"], image="test-image")

    cmd = captured["cmd"]
    assert cmd[0] == "docker"
    assert "test-image" in cmd
    script = cmd[-1]
    # Both ruff steps, restricted to the files handed in.
    assert "ruff check --fix" in script
    assert "ruff format" in script
    assert "'devfactory/foo.py'" in script
    assert "'tests/test_foo.py'" in script
    # Never the whole tree: that would bury the change under unrelated churn.
    assert " ." not in script


def test_autofix_mounts_the_checkout_writable(monkeypatch, tmp_path):
    """Unlike the QA runner, this step must be able to modify the files."""
    captured = _capture(monkeypatch)

    autofix(tmp_path, ["a.py"], image="test-image")

    mount = f"{tmp_path.absolute()}:{CONTAINER_WORKDIR}"
    assert mount in captured["cmd"]
    assert f"{mount}:ro" not in captured["cmd"]


def test_autofix_skips_when_no_python_files_changed(monkeypatch, tmp_path):
    """A run that touched no Python file must not start a container."""
    captured = _capture(monkeypatch)

    assert autofix(tmp_path, []) is False
    assert captured == {}


def test_autofix_survives_a_missing_docker(monkeypatch, tmp_path):
    """Autofix is best-effort: QA remains the gate, so failure here is not fatal."""

    def boom(cmd, **kwargs):
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(subprocess, "run", boom)

    assert autofix(tmp_path, ["a.py"]) is False


def test_autofix_survives_a_timeout(monkeypatch, tmp_path):
    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=autofix_module._TIMEOUT_S)

    monkeypatch.setattr(subprocess, "run", hang)

    assert autofix(Path(tmp_path), ["a.py"]) is False
