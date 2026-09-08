"""
Ollama API client wrapper.

Model *inference* does not go through here: every agent drives a model through the
harness (:mod:`devfactory.opencode`), which talks to Ollama itself. What is left is
the management surface the pipeline needs — which models exist, which are pulled,
and what version of the server is answering.
"""

from __future__ import annotations

import logging

import httpx

from devfactory.config import settings

logger = logging.getLogger(__name__)


class OllamaClient:
    """Thin wrapper around the Ollama ``/api/chat`` endpoint."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")

    def version(self) -> str:
        """Return the running Ollama server version (e.g. "0.33.3")."""
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{self.base_url}/api/version")
            resp.raise_for_status()
        return str(resp.json().get("version", ""))

    def list_models(self) -> list[str]:
        """Return names of models currently available in Ollama."""
        with httpx.Client(timeout=30) as client:
            resp = client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]

    def is_model_available(self, model: str) -> bool:
        """Return True if ``model`` is pulled and available in Ollama."""
        return model in self.list_models()

    def pull_model(self, model: str) -> None:
        """Pull ``model`` into Ollama, blocking until the download completes.

        Uses the ``/api/pull`` endpoint with ``stream=false`` so the request
        returns only once Ollama reports a final status. Pulling multi-GB
        weights can take minutes, hence the very long timeout.

        Raises:
            httpx.HTTPStatusError: On a non-2xx response.
            RuntimeError:          If Ollama reports a non-success final status.
        """
        # Long timeout: with stream=false Ollama holds the connection for the
        # whole download, which can take several minutes for multi-GB weights.
        # One hour is a generous cap that still avoids hanging the CLI forever.
        with httpx.Client(timeout=3600) as client:
            resp = client.post(
                f"{self.base_url}/api/pull",
                json={"model": model, "stream": False},
            )
            resp.raise_for_status()
            status = resp.json().get("status", "")
        # Ollama returns {"status": "success"} when the pull is complete.
        if status != "success":
            raise RuntimeError(f"Ollama pull of {model!r} did not succeed: status={status!r}")


# Default client instance
ollama = OllamaClient()
