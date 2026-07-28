"""Tests for model registry and router."""

import pytest

from devfactory.models.registry import MODELS, get_model, get_models_for_role
from devfactory.models.router import ModelRouter


def test_all_models_have_roles():
    for m in MODELS:
        assert m.roles, f"Model {m.name} has no roles"


def test_get_models_for_role_developer():
    devs = get_models_for_role("developer")
    assert len(devs) > 0
    for m in devs:
        assert "developer" in m.roles


def test_get_models_for_role_unknown():
    result = get_models_for_role("nonexistent_role")
    assert result == []


def test_get_model_by_name():
    first = MODELS[0]
    found = get_model(first.name)
    assert found is not None
    assert found.name == first.name


def test_get_model_missing():
    assert get_model("does-not-exist:99b") is None


def test_router_selects_without_ollama():
    """Router with verify_availability=False should select from registry."""
    router = ModelRouter(verify_availability=False)
    model = router.select("developer")
    assert model is not None
    assert "developer" in model.roles


def test_router_excludes():
    """Router should not select an excluded model."""
    router = ModelRouter(verify_availability=False)
    devs = get_models_for_role("developer")
    if len(devs) < 2:
        pytest.skip("Need at least 2 developer models for exclusion test")

    first = router.select("developer")
    second = router.select("developer", exclude=[first.name])
    assert second.name != first.name


def test_router_no_candidates_raises():
    router = ModelRouter(verify_availability=False)
    with pytest.raises(RuntimeError, match="No models registered"):
        router.select("nonexistent_role")


def test_router_require_tools_only_selects_tool_capable():
    """With require_tools=True, every selection supports tool calling."""
    router = ModelRouter(verify_availability=False)
    # Sample repeatedly since selection is random — a non-tool model must never
    # slip through the filter.
    for _ in range(30):
        model = router.select("developer", require_tools=True)
        assert model.supports_tools, f"{model.name} was selected but has no tool support"


def test_router_require_tools_excludes_non_tool_model():
    """A developer model without tool support is filtered out by require_tools."""
    devs = get_models_for_role("developer")
    non_tool = [m for m in devs if not m.supports_tools]
    if not non_tool:
        pytest.skip("No non-tool developer model in registry to exercise the filter")

    router = ModelRouter(verify_availability=False)
    selected_names = {router.select("developer", require_tools=True).name for _ in range(30)}
    for m in non_tool:
        assert m.name not in selected_names
