"""
BaseAgent — all agents inherit from this.
Handles model selection and prompt loading. The model itself is reached through
the harness (:mod:`devfactory.opencode`), never from here.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from devfactory.context import PipelineContext
from devfactory.models.registry import ModelMeta
from devfactory.models.router import router

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


class BaseAgent(ABC):
    """
    Abstract base for all pipeline agents.

    Subclasses must define:
      - role: str          — matches registry roles and prompt filename
      - run(ctx) -> ctx   — agent logic
    """

    role: str  # must be set by subclass

    # Whether this agent needs a model selected before run(). Agents that do not
    # call an LLM (e.g. the verification step agent, which only runs deterministic Docker tools)
    # set this to False so execute() skips router selection — otherwise it would
    # fail on a role that no model declares.
    requires_model: bool = True

    # Roles whose model this agent must not share. Set by the reviewer to keep it
    # off the developer's model: an agent reviewing its own work is not a review.
    avoid_models_from_roles: list[str] = []

    def __init__(self, model: ModelMeta | None = None):
        """
        Args:
            model: Force a specific model. If None, router selects randomly.
        """
        self._forced_model = model
        self._model: ModelMeta | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        """Entry point called by the orchestrator."""
        if self.requires_model:
            self._model = self._forced_model or router.select(
                role=self.role, exclude=self._models_to_avoid(ctx)
            )
            ctx.model_assignments[self.role] = self._model.name
            logger.info(f"[{self.role}] starting with model={self._model.name}")
        else:
            # No LLM for this agent (e.g. verification runs deterministic Docker tools only).
            logger.info(f"[{self.role}] starting (no LLM — deterministic tools)")

        updated_ctx = self.run(ctx)

        logger.info(f"[{self.role}] done")
        return updated_ctx

    def _models_to_avoid(self, ctx: PipelineContext) -> list[str] | None:
        """Models this agent must not be given, or None to leave the draw free.

        A reviewer that shares the developer's model is reviewing its own work —
        the separation of duties we document as a control. Within one role the
        model is deliberately *kept* across iterations: a developer that changed
        model on every retry would lose the context it had built, and a reviewer
        that changed would move its verdict for reasons unrelated to the code.
        """
        avoid = [
            ctx.model_assignments[role]
            for role in self.avoid_models_from_roles
            if role in ctx.model_assignments
        ]
        return avoid or None

    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext:
        """Agent-specific logic. Must return updated context."""
        ...

    # ── Helpers for subclasses ─────────────────────────────────────────────────

    @property
    def model(self) -> ModelMeta:
        if self._model is None:
            raise RuntimeError("model not set — call execute() instead of run() directly")
        return self._model

    def load_prompt(self, filename: str | None = None) -> str:
        """Load prompt template from prompts/ directory."""
        name = filename or f"{self.role}.md"
        path = PROMPTS_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8")
