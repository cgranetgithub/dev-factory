"""
Model registry — defines which local models are available and their roles.
Edit this file to add/remove models as you pull them into Ollama.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelMeta:
    name: str  # Ollama model name (e.g. "qwen2.5-coder:14b")
    parameters_b: float  # Billion parameters (approx)
    context_k: int  # Context window in K tokens
    roles: list[str]  # Which agent roles this model can play
    # Whether the model exposes tool/function calling in Ollama. Required by the
    # "opencode" developer backend (its agentic loop drives Edit/Write/run tools).
    # A model without it still works for plain-chat roles (analyst, reviewer) and
    # the single-shot "ollama" developer backend, but is filtered out of the
    # opencode developer pool. Check with `ollama show <model>` — the
    # "Capabilities" section must list "tools".
    supports_tools: bool = True
    notes: str = ""


# ── Registry ──────────────────────────────────────────────────────────────────
# Add your models here as you pull them. This registry is the source of truth:
# `devfactory models --sync` pulls every model listed here that is missing from
# Ollama, and the router ignores (with an info log) any model not yet pulled.
#
# Policy: 20B parameters minimum, and every model must fit on the 24 GB card
# (RTX 3090 Ti). Split into two families of three:
#   * 3 models on the "developer"/coding side → this role writes code, where a
#     model that hallucinates APIs on precise, schema-bound edits is useless.
#     Only two dedicated coders survive the 20B floor (qwen3-coder, devstral),
#     so the third slot is filled by a strong DENSE general model. All three are
#     tool-capable, a hard requirement for the "opencode" developer backend.
#   * 3 strong general models → the "analyst" role reasons about the issue and
#     benefits from broad reasoning rather than pure code fluency.
# The "reviewer" role draws from ALL six, so the two reviewers can pair a coder
# with a generalist for genuinely different perspectives on the diff.
#
# VRAM: Ollama loads one model at a time, so the constraint is per-model, not the
# sum — each entry must fit on 24 GB with room left for the KV cache. Models near
# ~23 GB on disk (e.g. qwen3.6:35b-a3b) are avoided: they leave too little for a
# 32K context. Aim for ≤ ~20 GB on disk.
#
# Note: the "qa" role uses NO model — QAAgent runs deterministic Docker tools
# (ruff/mypy/bandit/pytest), it never calls an LLM, so no model declares "qa".
#
# Roles: "analyst", "developer", "reviewer"

# Coding side — developer (+ reviewer). Two dedicated coders (Qwen qwen3-coder /
# Mistral devstral) plus one dense general model, since no third dedicated coder
# clears the 20B floor while fitting the card.
_CODING_ROLES = ["developer", "reviewer"]
# General models — analyst (+ reviewer). Strongest locally-runnable variants of
# the top open-weight families (the true GLM-4.7 / Qwen3 flagships are 200B+ and
# do not fit on this host).
_GENERAL_ROLES = ["analyst", "reviewer"]

MODELS: list[ModelMeta] = [
    # ── Coding-specialised (developer + reviewer) ──────────────────────────────
    ModelMeta(
        name="qwen3-coder:30b",
        parameters_b=30,
        context_k=32,
        roles=_CODING_ROLES,
        notes="Qwen3-generation code model (MoE). Newest and strongest Qwen coder.",
    ),
    ModelMeta(
        name="devstral:24b",
        parameters_b=24,
        context_k=32,
        roles=_CODING_ROLES,
        # Mistral's agentic coding model (OpenHands scaffold). It exposes tool
        # calling in Ollama, so it drives the "opencode" developer backend —
        # unlike codestral, which it replaces here. Keeps a non-Qwen family in the
        # coding pool for genuinely different reviews.
        notes="Mistral AI Devstral Small. Agentic code model, tool-capable.",
    ),
    ModelMeta(
        # Not a dedicated coder: a strong DENSE general model filling the third
        # coding slot (no dedicated coder ≥20B remains after the 16B drop). Dense
        # (not MoE) for per-token quality on precise codegen, and a plain instruct
        # model (no reasoning `<think>` blocks) so the developer output stays clean.
        name="qwen2.5:32b",
        parameters_b=32,
        context_k=32,
        roles=_CODING_ROLES,
        notes="Qwen2.5 32B dense general model, used as the developer/coding fallback.",
    ),
    # ── General (analyst + reviewer) ───────────────────────────────────────────
    ModelMeta(
        # Name must match `ollama list` exactly. This model has no explicit tag,
        # so Ollama reports it as ":latest"; without that suffix the availability
        # check fails and the router wrongly skips (and `--sync` re-pulls) it.
        name="glm-4.7-flash:latest",
        parameters_b=32,
        context_k=32,
        roles=_GENERAL_ROLES,
        notes="Zhipu GLM-4.7 (flash/local variant). Strong general reasoning.",
    ),
    ModelMeta(
        # Dense 27B rather than the 35b-a3b MoE variant: the MoE weighs ~23 GB on
        # disk, leaving barely ~1 GB of the 24 GB card for the KV cache — too tight
        # at 32K context (spill/OOM risk). 27B dense (~17 GB) keeps a comfortable
        # context margin while staying above the 20B floor.
        name="qwen3.6:27b",
        parameters_b=27,
        context_k=32,
        roles=_GENERAL_ROLES,
        notes="Qwen3.6 27B dense. Latest Qwen general model, safe VRAM margin.",
    ),
    ModelMeta(
        name="gemma4:26b",
        parameters_b=26,
        context_k=32,
        roles=_GENERAL_ROLES,
        notes="Google Gemma 4. Reliable structured output for analyst/reviewer.",
    ),
]

# Index by name for quick lookup
_by_name: dict[str, ModelMeta] = {m.name: m for m in MODELS}


def get_models_for_role(role: str) -> list[ModelMeta]:
    """Return all models that support a given role."""
    return [m for m in MODELS if role in m.roles]


def get_model(name: str) -> ModelMeta | None:
    return _by_name.get(name)
