"""
Model registry — defines which local models are available and their roles.
Edit this file to add/remove models as you pull them into Ollama.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelMeta:
    name: str  # Ollama model name (e.g. "qwen2.5-coder:7b")
    parameters_b: float  # Billion parameters (approx)
    context_k: int  # Context window in K tokens
    roles: list[str]  # Which agent roles this model can play
    notes: str = ""


# ── Registry ──────────────────────────────────────────────────────────────────
# Add your models here as you pull them.
# Roles: "analyst", "developer", "qa", "reviewer"

MODELS: list[ModelMeta] = [
    ModelMeta(
        name="qwen2.5-coder:7b",
        parameters_b=7,
        context_k=32,
        roles=["analyst", "developer", "qa", "reviewer"],
        notes="Strong on code, good instruction following",
    ),
    ModelMeta(
        name="qwen2.5-coder:14b",
        parameters_b=14,
        context_k=32,
        roles=["analyst", "developer", "qa", "reviewer"],
        notes="More capable, heavier",
    ),
    ModelMeta(
        name="deepseek-coder-v2:16b",
        parameters_b=16,
        context_k=32,
        roles=["developer", "reviewer"],
        notes="Excellent at code generation",
    ),
    ModelMeta(
        name="mistral:7b",
        parameters_b=7,
        context_k=32,
        roles=["analyst", "qa", "reviewer"],
        notes="Good at reasoning and structured output",
    ),
]

# Index by name for quick lookup
_by_name: dict[str, ModelMeta] = {m.name: m for m in MODELS}


def get_models_for_role(role: str) -> list[ModelMeta]:
    """Return all models that support a given role."""
    return [m for m in MODELS if role in m.roles]


def get_model(name: str) -> ModelMeta | None:
    return _by_name.get(name)
