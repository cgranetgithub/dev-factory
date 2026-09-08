"""
BaseAgent — all agents inherit from this.
Handles: model selection, prompt loading, LLM call, logging, execution recording.
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

    # Whether to avoid re-picking the model already used for this role in this run.
    # Only the reviewer sets this True, so its two passes get different models for
    # genuinely different perspectives. It must stay False for agents that run more
    # than once for other reasons — notably the developer, which re-executes on
    # each verification retry: excluding its previous model would starve a single-model pool
    # (e.g. the opencode backend, pinned to the one agentic-loop driver).
    avoid_repeated_model: bool = False
    # Roles whose model this agent must not share. Set by the reviewer to keep it
    # off the developer's model: an agent reviewing its own work is not a review.
    avoid_models_from_roles: list[str] = []

    def requires_agentic_loop(self) -> bool:
        """Whether this agent needs a model that drives the opencode agentic loop.

        Default False (plain-chat agents). The developer agent overrides this to
        True when its "opencode" backend is active, so the router only picks models
        verified to drive the tool loop. Kept as a method (not a class attribute)
        because the answer can depend on runtime settings, not just the agent class.
        """
        return False

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
            # For agents that opt in (the reviewer), exclude the model already used
            # for THIS role in this run so the two passes differ. Off by default —
            # the developer re-executes on each verification retry and must be free to reuse
            # its model (its pool may hold a single eligible driver).
            exclude = self._models_to_avoid(ctx)
            self._model = self._forced_model or router.select(
                role=self.role, exclude=exclude, require_agentic_loop=self.requires_agentic_loop()
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

        Two different reasons, kept apart because they are different controls:
        an agent may want a fresh perspective on its own previous attempt, and an
        agent may need to differ from *another* role — a reviewer that shares the
        developer's model is reviewing its own work, which is the separation of
        duties we document as a control.
        """
        avoid = []
        if self.avoid_repeated_model:
            previous = ctx.model_assignments.get(self.role)
            if previous:
                avoid.append(previous)
        for role in self.avoid_models_from_roles:
            other = ctx.model_assignments.get(role)
            if other:
                avoid.append(other)
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

    def system_message(self, content: str) -> dict:
        return {"role": "system", "content": content}

    def user_message(self, content: str) -> dict:
        return {"role": "user", "content": content}

    def assistant_message(self, content: str) -> dict:
        return {"role": "assistant", "content": content}
