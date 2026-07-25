"""
Ollama API client wrapper.

Provides a thin, swappable interface over the Ollama ``/api/chat`` endpoint.
The ``OllamaClient`` class can be replaced with any OpenAI-compatible backend
(e.g. vLLM) by implementing the same ``chat()`` / ``list_models()`` interface.

Retry behaviour is provided via the ``@with_retry`` decorator on ``chat()``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from devfactory.config import settings
from devfactory.models.retry import with_retry


@dataclass
class LLMResponse:
    """Structured response from a single LLM chat call."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int


class OllamaClient:
    """Thin wrapper around the Ollama ``/api/chat`` endpoint."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")

    @with_retry(max_attempts=3, delay=10.0)
    def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 8192,
    ) -> LLMResponse:
        """
        Send a chat request to Ollama and return a structured response.

        Args:
            model:       Ollama model name (e.g. ``qwen2.5-coder:14b``).
            messages:    OpenAI-style message list (role / content dicts).
            temperature: Sampling temperature (lower = more deterministic).
            max_tokens:  Maximum tokens to generate.

        Returns:
            :class:`LLMResponse` with content and token counts.

        Raises:
            httpx.HTTPStatusError: On non-2xx response.
            RuntimeError:          After all retry attempts are exhausted.
        """
        timeout = settings.ollama_timeout_s
        start = time.monotonic()
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
            )
            resp.raise_for_status()

        data = resp.json()
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Ollama reports token usage as prompt_eval_count / eval_count
        return LLMResponse(
            content=data["message"]["content"],
            model=model,
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            duration_ms=elapsed_ms,
        )

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
