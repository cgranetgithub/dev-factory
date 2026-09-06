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
    # Whether the model actually DRIVES the "opencode" agentic loop — i.e. reads
    # files, edits them and runs commands, rather than describing the change in
    # prose. Stricter than Ollama's "tools" capability flag, which some models
    # advertise while producing zero edits.
    #
    # Measured, not assumed. The qualification task: a small project with two
    # functions and their tests, "add divide(a, b) raising ValueError on zero, add
    # tests in the existing style, then run pytest and ruff yourself and fix what
    # they report". Four criteria checked afterwards from outside the model —
    # function present, tests present, pytest green, ruff clean — over two
    # identical trials (2026-09-06).
    #
    # This flag was WRONG for four models until that measurement. It was first set
    # from runs made while Ollama still used its 4096-token default context: the
    # system prompt plus the tool definitions overflowed, Ollama truncated from the
    # top, the model lost the instructions telling it how to call tools, and
    # answered in prose. That reads exactly like an incapable model. Anything
    # measured before OLLAMA_CONTEXT_LENGTH was raised to 32768 should be
    # re-measured before being believed — opencode's own documentation asks for 64k.
    #
    # Re-qualify with scratchpad/qualify_real.sh-style runs, two trials minimum:
    # one model scored 4/4, then timed out, then 0/4 on the same exercise.
    drives_agentic_loop: bool = False
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
#     so the third slot is filled by a strong DENSE general model. NOTE: neither
#     of those two can drive the "opencode" agentic loop (see drives_agentic_loop);
#     they serve as reviewers and as single-shot "ollama"-backend developers.
#     The agentic drivers are qwen3-coder plus, unexpectedly, all three general
#     models — so an agentic role can be staffed by a model that is not the
#     developer's, which is what keeps reviewer and developer separable.
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
# Note: the "verification" role uses NO model — VerificationAgent runs deterministic Docker tools
# (ruff/mypy/bandit/pytest), it never calls an LLM, so no model declares "verification".
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
        # Qualified 4/4 on both trials, and by far the fastest: 20s and 21s where
        # the others need 40-670s. Speed matters more than it looks — the loop can
        # run the developer three times per issue, behind two gates.
        drives_agentic_loop=True,
        notes="Qwen3-generation code model (MoE). Newest and strongest Qwen coder.",
    ),
    ModelMeta(
        name="devstral:24b",
        parameters_b=24,
        context_k=32,
        roles=_CODING_ROLES,
        # Mistral's agentic coding model. Despite the branding and Ollama's "tools"
        # capability, it replies in prose and emits no tool calls — it explains how
        # to create the file instead of creating it. The only model here that fails
        # even the trivial one-file task, re-checked after the context fix, so this
        # verdict is not the 4096-token artefact that misjudged the others.
        notes="Mistral AI Devstral Small. Tool-capable flag, but prose-only via Ollama.",
    ),
    ModelMeta(
        # Not a dedicated coder: a strong DENSE general model on the coding side.
        # Dense (not MoE) for per-token quality on precise codegen, and a plain
        # instruct model (no reasoning `<think>` blocks).
        # Excluded from the agentic loop for INSTABILITY, not incapacity: on the
        # same exercise it scored 4/4 (269s), then hit the 900s timeout, then 0/4.
        # A model whose result is a coin toss cannot hold a pipeline role.
        name="qwen2.5:32b",
        parameters_b=32,
        context_k=32,
        roles=_CODING_ROLES,
        notes="Qwen2.5 32B dense general model. Unstable under opencode.",
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
        # Qualified 4/4 on both trials (88s, 42s). Being a general model, it gives
        # the reviewer role an agentic driver that is NOT the developer's model —
        # which is what makes an exploring reviewer possible without collapsing the
        # separation of duties onto a single model.
        drives_agentic_loop=True,
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
        # Qualified 4/4 on both trials, but the slowest that passes: 670s then 153s.
        # Usable, and a poor default while the budget is three iterations.
        drives_agentic_loop=True,
        notes="Qwen3.6 27B dense. Latest Qwen general model, safe VRAM margin.",
    ),
    ModelMeta(
        name="gemma4:26b",
        parameters_b=26,
        context_k=32,
        roles=_GENERAL_ROLES,
        # Qualified 4/4 on both trials (64s, 46s), second fastest overall. Works in
        # many small steps (25-35 where others take 9) — a different method, same
        # outcome; step count is not a quality signal.
        drives_agentic_loop=True,
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
