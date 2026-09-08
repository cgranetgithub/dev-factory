"""
Tests for telling a cut-off answer apart from a bad one.

From a real run: the analyst spent its entire 4096-token budget on reasoning,
returned an empty answer, and the pipeline reported "the summary is empty" — true,
but not the reason. A retry told the model its JSON was unusable, when its JSON had
simply never been written.
"""

from __future__ import annotations

import httpx

from devfactory.models.client import OllamaClient


def _reply(monkeypatch, payload: dict) -> None:
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)


def _call() -> object:
    return OllamaClient(base_url="http://x").chat("m", [{"role": "user", "content": "hi"}])


def test_a_complete_answer_is_not_flagged(monkeypatch):
    _reply(monkeypatch, {"message": {"content": '{"a": 1}'}, "done_reason": "stop"})

    r = _call()

    assert r.content == '{"a": 1}'
    assert r.truncated is False
    assert r.thinking_tokens_only is False


def test_hitting_the_token_limit_is_reported(monkeypatch):
    """`done_reason: length` is the difference between "wrong" and "unfinished"."""
    _reply(monkeypatch, {"message": {"content": '{"a"'}, "done_reason": "length"})

    assert _call().truncated is True


def test_reasoning_with_no_answer_is_reported(monkeypatch):
    """The failure that produced an empty spec: a reasoning model spends the whole
    budget in `thinking`, and `content` comes back empty."""
    _reply(
        monkeypatch,
        {"message": {"content": "", "thinking": "let me consider..."}, "done_reason": "length"},
    )

    r = _call()

    assert r.thinking_tokens_only is True
    assert r.truncated is True


def test_an_empty_answer_without_reasoning_is_not_confused_with_it(monkeypatch):
    """A model that genuinely said nothing is a different problem, and must not be
    reported as one that thought too long."""
    _reply(monkeypatch, {"message": {"content": ""}, "done_reason": "stop"})

    assert _call().thinking_tokens_only is False


def test_a_missing_message_does_not_raise(monkeypatch):
    """Ollama's shape has changed before; a malformed reply must degrade, not crash."""
    _reply(monkeypatch, {})

    assert _call().content == ""
