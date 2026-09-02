"""
BaseAgent — all agents inherit from this.
Handles: model selection, prompt loading, LLM call, logging, execution recording.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from devfactory.context import PipelineContext
from devfactory.models.client import LLMResponse, ollama
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
    # call an LLM (e.g. the QA agent, which only runs deterministic Docker tools)
    # set this to False so execute() skips router selection — otherwise it would
    # fail on a role that no model declares.
    requires_model: bool = True

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
            # Only exclude the model already used for THIS role in this run (e.g. the
            # first reviewer), so the two reviewers differ — without starving a role
            # whose model pool overlaps models already taken by earlier roles.
            already_used = ctx.model_assignments.get(self.role)
            exclude = [already_used] if already_used else None
            self._model = self._forced_model or router.select(
                role=self.role, exclude=exclude, require_agentic_loop=self.requires_agentic_loop()
            )
            ctx.model_assignments[self.role] = self._model.name
            logger.info(f"[{self.role}] starting with model={self._model.name}")
        else:
            # No LLM for this agent (e.g. QA runs deterministic Docker tools only).
            logger.info(f"[{self.role}] starting (no LLM — deterministic tools)")

        updated_ctx = self.run(ctx)

        logger.info(f"[{self.role}] done")
        return updated_ctx

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

    def chat(
        self,
        ctx: PipelineContext,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 8192,
    ) -> LLMResponse:
        """Call the LLM and record the execution in context."""
        response = ollama.chat(
            model=self.model.name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        ctx.log_execution(
            agent=self.role,
            model=self.model.name,
            duration_ms=response.duration_ms,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
        )
        logger.debug(
            f"[{self.role}] tokens: {response.prompt_tokens}→{response.completion_tokens} "
            f"in {response.duration_ms}ms"
        )
        return response

    def system_message(self, content: str) -> dict:
        return {"role": "system", "content": content}

    def user_message(self, content: str) -> dict:
        return {"role": "user", "content": content}

    def assistant_message(self, content: str) -> dict:
        return {"role": "assistant", "content": content}
