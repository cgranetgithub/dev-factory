"""
Tests for pinning a role to a model.

A comparison run that quietly used a different model than the one requested is
worse than no run at all, so every rejection path is checked.
"""

from __future__ import annotations

import pytest

from devfactory.orchestrator import Pipeline


def test_no_overrides_leaves_every_role_free():
    assert Pipeline._resolve_overrides({}) == {}


def test_pinning_a_role_resolves_the_registry_entry():
    resolved = Pipeline._resolve_overrides({"developer": "qwen3-coder:30b"})

    assert resolved["developer"].name == "qwen3-coder:30b"
    assert "developer" in resolved["developer"].roles


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="Unknown role"):
        Pipeline._resolve_overrides({"tester": "qwen3-coder:30b"})


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match="not in the registry"):
        Pipeline._resolve_overrides({"developer": "gpt-9:1t"})


def test_model_that_does_not_declare_the_role_is_rejected():
    """qwen3.6:27b is analyst/reviewer only — too slow to sit in the developer
    loop. Pinning it there must fail loudly rather than produce a run nobody can
    interpret."""
    with pytest.raises(ValueError, match="does not declare"):
        Pipeline._resolve_overrides({"developer": "qwen3.6:27b"})


def test_every_qualified_driver_is_pinnable_for_its_roles():
    """The measurement is only useful if the qualified models can be selected."""
    from devfactory.models.registry import MODELS

    for model in MODELS:
        if not model.drives_agentic_loop:
            continue
        for role in model.roles:
            resolved = Pipeline._resolve_overrides({role: model.name})
            assert resolved[role].name == model.name
