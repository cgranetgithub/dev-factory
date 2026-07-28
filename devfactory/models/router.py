"""
Model router — selects a model for a given agent role.
Strategy: random among available models that support the role.
Verifies availability against live Ollama instance.
"""

from __future__ import annotations

import logging
import random

from devfactory.models.client import ollama
from devfactory.models.registry import MODELS, ModelMeta, get_models_for_role

logger = logging.getLogger(__name__)


class ModelRouter:
    def __init__(self, verify_availability: bool = True):
        self._verify = verify_availability
        self._available_cache: set[str] | None = None

    def _get_available(self) -> set[str]:
        """Fetch available models from Ollama (cached per router instance)."""
        if self._available_cache is None:
            try:
                self._available_cache = set(ollama.list_models())
                logger.debug(f"Available models: {self._available_cache}")
                self._warn_unavailable(self._available_cache)
            except Exception as e:
                logger.warning(
                    f"Could not fetch Ollama model list: {e}. Skipping availability check."
                )
                self._available_cache = set()
        return self._available_cache

    def _warn_unavailable(self, available: set[str]) -> None:
        """Log an info message for registered models that are not pulled in Ollama.

        These are silently skipped during selection; surfacing them here tells the
        operator to run ``devfactory models --sync`` (or ``ollama pull``) instead of
        wondering why a registered model is never chosen.
        """
        missing = [m.name for m in MODELS if m.name not in available]
        if missing:
            logger.info(
                "Registered but not pulled in Ollama, skipping: %s. "
                "Run `devfactory models --sync` to pull them.",
                ", ".join(sorted(missing)),
            )

    def select(
        self,
        role: str,
        exclude: list[str] | None = None,
        require_tools: bool = False,
    ) -> ModelMeta:
        """
        Select a random model for the given role.

        Args:
            role: Agent role ("analyst", "developer", "qa", "reviewer")
            exclude: Model names to exclude (e.g. already used in this pipeline run)
            require_tools: If True, only consider models that expose tool/function
                calling (needed by the "opencode" developer backend).

        Returns:
            Selected ModelMeta

        Raises:
            RuntimeError: No suitable model found
        """
        candidates = get_models_for_role(role)

        if not candidates:
            raise RuntimeError(f"No models registered for role '{role}'")

        if exclude:
            candidates = [m for m in candidates if m.name not in exclude]

        # Drop models that cannot do tool calling when the caller needs it.
        if require_tools:
            candidates = [m for m in candidates if m.supports_tools]

        if self._verify:
            available = self._get_available()
            if available:  # only filter if we got a valid list
                candidates = [m for m in candidates if m.name in available]

        if not candidates:
            raise RuntimeError(
                f"No available models for role '{role}' "
                f"(excluded: {exclude}, require_tools={require_tools}, "
                "check Ollama and registry)"
            )

        selected = random.choice(candidates)
        logger.info(f"[router] role={role} → model={selected.name}")
        return selected

    def invalidate_cache(self):
        """Force re-check of available models on next select() call."""
        self._available_cache = None


# Default router instance
router = ModelRouter()
