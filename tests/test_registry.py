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


def test_router_only_ever_selects_agentic_drivers():
    """Every agent reaches its model through the harness, so a model that answers
    in prose must never be selected — for any role, without the caller asking.
    That filter used to be opt-in, and the developer lost the flag in a refactor:
    it could then draw a prose-only model one time in three."""
    router = ModelRouter(verify_availability=False)
    for role in ("analyst", "developer", "reviewer"):
        for _ in range(30):
            model = router.select(role)
            assert model.drives_agentic_loop, f"{model.name} selected for {role}, drives no loop"


def test_router_never_selects_a_registered_prose_only_model():
    """The prose-only models stay in the registry as recorded measurements."""
    prose_only = [m for m in get_models_for_role("developer") if not m.drives_agentic_loop]
    if not prose_only:
        pytest.skip("No prose-only developer model in registry to exercise the filter")

    router = ModelRouter(verify_availability=False)
    selected = {router.select("developer").name for _ in range(30)}
    for m in prose_only:
        assert m.name not in selected


def test_excluding_every_driver_starves_the_pool():
    """Exclusion still has to be able to empty the pool — that is what made the
    single-driver configuration fatal, and the router must say so rather than
    silently returning a model that cannot drive the loop."""
    drivers = [m.name for m in get_models_for_role("developer") if m.drives_agentic_loop]
    router = ModelRouter(verify_availability=False)

    with pytest.raises(RuntimeError, match="No available models"):
        router.select("developer", exclude=drivers)


def test_excluding_one_driver_still_leaves_another():
    """With redundancy restored, losing one driver is survivable."""
    drivers = [m.name for m in get_models_for_role("developer") if m.drives_agentic_loop]
    router = ModelRouter(verify_availability=False)

    picked = router.select("developer", exclude=[drivers[0]])

    assert picked.name != drivers[0]
    assert picked.drives_agentic_loop


def test_opencode_developer_pool_has_redundancy():
    """The agentic developer pool must not depend on a single model.

    It did — one unavailable model took the whole factory offline, which is what
    issue #23 was about. That fragility came from a measurement error, not from
    the model landscape.
    """
    drivers = [m for m in get_models_for_role("developer") if m.drives_agentic_loop]

    assert len(drivers) >= 2, f"single point of failure: {[m.name for m in drivers]}"


def test_a_reviewer_can_always_differ_from_the_developer():
    """Separation of duties needs at least one agentic driver outside the models
    that can be the developer — otherwise an exploring reviewer would end up
    reviewing its own work."""
    dev_drivers = {m.name for m in get_models_for_role("developer") if m.drives_agentic_loop}
    rev_drivers = {m.name for m in get_models_for_role("reviewer") if m.drives_agentic_loop}

    assert rev_drivers - dev_drivers or len(dev_drivers) >= 2
