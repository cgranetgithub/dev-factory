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
    notes: str = ""


# ── Registry ──────────────────────────────────────────────────────────────────
# Add your models here as you pull them. This registry is the source of truth:
# `devfactory models --sync` pulls every model listed here that is missing from
# Ollama, and the router ignores (with an info log) any model not yet pulled.
#
# Policy: 14B parameters minimum, split into two families of three:
#   * 3 coding-specialised models → the "developer" role writes code, where a
#     model that hallucinates APIs on precise, schema-bound edits is useless.
#   * 3 strong general models → the "analyst" role reasons about the issue and
#     benefits from broad reasoning rather than pure code fluency.
# The "reviewer" role draws from ALL six, so the two reviewers can pair a coder
# with a generalist for genuinely different perspectives on the diff.
#
# Note: the "qa" role uses NO model — QAAgent runs deterministic Docker tools
# (ruff/mypy/bandit/pytest), it never calls an LLM, so no model declares "qa".
#
# Roles: "analyst", "developer", "reviewer"

# Coding-specialised models — developer (+ reviewer). Distinct families
# (Qwen / Mistral / DeepSeek) for leaderboard and reviewer diversity.
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
        name="codestral:22b",
        parameters_b=22,
        context_k=32,
        roles=_CODING_ROLES,
        notes="Mistral AI's dedicated code model. Different family from Qwen/DeepSeek.",
    ),
    ModelMeta(
        name="deepseek-coder-v2:16b",
        parameters_b=16,
        context_k=32,
        roles=_CODING_ROLES,
        notes="DeepSeek code model, excellent code generation.",
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
        name="qwen3.6:35b-a3b",
        parameters_b=35,
        context_k=32,
        roles=_GENERAL_ROLES,
        notes="Qwen3.6 (MoE, 3B active). Latest Qwen general model, fast for its size.",
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
