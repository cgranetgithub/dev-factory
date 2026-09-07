"""
Tests for host readiness checks.

Ollama is mocked throughout — nothing here reaches the network or pulls weights.
"""

from __future__ import annotations

import httpx
import pytest

from devfactory.config import settings
from devfactory.models import provisioning
from devfactory.models.provisioning import (
    _parse_version,
    check_ollama_version,
    ensure_models_available,
)


class _FakeOllama:
    def __init__(self, version="0.33.3", models=None, pull_fails=()):
        self._version = version
        self._models = list(models if models is not None else [])
        self._pull_fails = set(pull_fails)
        self.pulled: list[str] = []

    def version(self):
        return self._version

    def list_models(self):
        return list(self._models)

    def pull_model(self, name):
        if name in self._pull_fails:
            raise RuntimeError("pull failed")
        self.pulled.append(name)
        self._models.append(name)


@pytest.fixture
def fake(monkeypatch):
    def _install(**kwargs):
        f = _FakeOllama(**kwargs)
        monkeypatch.setattr(provisioning, "ollama", f)
        return f

    return _install


# ── Version parsing ──────────────────────────────────────────────────────────


def test_parse_version_handles_plain_and_prerelease():
    assert _parse_version("0.33.3") == (0, 33, 3)
    assert _parse_version("v0.33.3") == (0, 33, 3)
    assert _parse_version("0.33.3-rc1") == (0, 33, 3)


def test_unparseable_version_sorts_below_everything():
    """An unreadable version must trigger the warning, not slip past it."""
    assert _parse_version("unknown") < _parse_version("0.1.0")


def test_version_comparison_is_numeric_not_lexical():
    """The trap this exists to avoid: "0.9.0" > "0.33.0" as strings."""
    assert _parse_version("0.33.0") > _parse_version("0.9.0")


# ── Version check ────────────────────────────────────────────────────────────


def test_current_version_passes(fake, monkeypatch):
    monkeypatch.setattr(settings, "min_ollama_version", "0.33.0")
    fake(version="0.33.3")

    assert check_ollama_version() is True


def test_older_version_warns(fake, monkeypatch, caplog):
    monkeypatch.setattr(settings, "min_ollama_version", "0.33.0")
    fake(version="0.30.1")

    assert check_ollama_version() is False
    assert "older than the validated minimum" in caplog.text


def test_unreachable_ollama_does_not_block_the_run(fake, monkeypatch):
    """A readiness check must never be the reason a run cannot start."""

    class _Down(_FakeOllama):
        def version(self):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(provisioning, "ollama", _Down())

    assert check_ollama_version() is True


# ── Model provisioning ───────────────────────────────────────────────────────


def test_missing_models_are_pulled(fake):
    from devfactory.models.registry import MODELS

    fake(models=[MODELS[0].name])

    pulled = ensure_models_available()

    assert set(pulled) == {m.name for m in MODELS[1:]}
    assert MODELS[0].name not in pulled


def test_nothing_is_pulled_when_everything_is_present(fake):
    from devfactory.models.registry import MODELS

    f = fake(models=[m.name for m in MODELS])

    assert ensure_models_available() == []
    assert f.pulled == []


def test_a_failed_pull_does_not_stop_the_others(fake):
    """One unavailable model must not take down a run the others can serve."""
    from devfactory.models.registry import MODELS

    failing = MODELS[0].name
    fake(models=[], pull_fails=[failing])

    pulled = ensure_models_available()

    assert failing not in pulled
    assert len(pulled) == len(MODELS) - 1


def test_auto_pull_can_be_switched_off(fake, monkeypatch):
    """Metered or offline hosts must be able to opt out."""
    monkeypatch.setattr(settings, "auto_pull_models", False)
    f = fake(models=[])

    assert ensure_models_available() == []
    assert f.pulled == []


def test_cli_sync_delegates_to_provisioning(monkeypatch):
    """`devfactory models --sync` and the pipeline must pull through the same code.

    Two implementations of "pull what the registry declares" would drift, and the
    one that drifted would be the one nobody ran that day.
    """
    from devfactory import cli

    calls = []
    monkeypatch.setattr(
        "devfactory.models.provisioning.ensure_models_available",
        lambda: calls.append("called") or ["some-model"],
    )
    monkeypatch.setattr("devfactory.models.client.ollama.list_models", lambda: ["some-model"])

    result = cli._sync_models(set())

    assert calls == ["called"]
    assert "some-model" in result
