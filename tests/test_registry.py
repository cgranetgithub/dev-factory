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


def test_router_require_agentic_loop_only_selects_drivers():
    """With require_agentic_loop=True, every selection drives the agentic loop."""
    router = ModelRouter(verify_availability=False)
    # Sample repeatedly since selection is random — a non-driver must never slip
    # through the filter.
    for _ in range(30):
        model = router.select("developer", require_agentic_loop=True)
        assert model.drives_agentic_loop, f"{model.name} selected but drives no loop"


def test_router_require_agentic_loop_excludes_prose_only_model():
    """A developer model that only replies in prose is filtered out."""
    devs = get_models_for_role("developer")
    prose_only = [m for m in devs if not m.drives_agentic_loop]
    if not prose_only:
        pytest.skip("No prose-only developer model in registry to exercise the filter")

    router = ModelRouter(verify_availability=False)
    selected_names = {router.select("developer", require_agentic_loop=True).name for _ in range(30)}
    for m in prose_only:
        assert m.name not in selected_names


def test_router_reuses_single_driver_when_not_excluding():
    """A verification retry re-selects the developer without excluding it — the single
    agentic-loop driver must remain selectable (no starvation)."""
    drivers = [m for m in get_models_for_role("developer") if m.drives_agentic_loop]
    if len(drivers) != 1:
        pytest.skip("Test targets the single-driver opencode pool")
    only = drivers[0].name

    router = ModelRouter(verify_availability=False)
    # No exclude (developer behaviour): picks the same driver again, no error.
    assert router.select("developer", require_agentic_loop=True).name == only

    # Excluding it (the old, buggy behaviour) would starve the pool.
    with pytest.raises(RuntimeError, match="No available models"):
        router.select("developer", exclude=[only], require_agentic_loop=True)
