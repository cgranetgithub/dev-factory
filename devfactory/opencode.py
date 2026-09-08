"""
One way to run the OpenCode CLI, used by every agent that needs the codebase.

Two agents drive OpenCode now — the developer edits, the analyst reads — and a
third is coming. Duplicating the invocation would mean two places to get the
provider config right, two timeout policies, and two chances for them to drift.

The only real difference between callers is whether the agent may write, which is
why that is the one required argument.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from devfactory.config import settings

logger = logging.getLogger(__name__)

# OpenCode ships a `plan` agent whose permissions deny `edit` everywhere except
# its own plans directory. Using it is how a reading agent is prevented from
# writing — by OpenCode's own permission system rather than by our good intentions.
READ_ONLY_AGENT = "plan"
WRITING_AGENT = "build"


@dataclass
class OpenCodeResult:
    """What a run produced: its final message, and how long it took."""

    output: str
    duration_ms: int


def run(
    prompt: str,
    repo_path: Path,
    model_name: str,
    *,
    read_only: bool,
    role: str,
) -> OpenCodeResult:
    """
    Run OpenCode against ``repo_path`` and return what it printed.

    Args:
        prompt:     The task, already built by the caller.
        repo_path:  The checkout OpenCode works in.
        model_name: Registry model name, e.g. ``qwen3-coder:30b``.
        read_only:  True for an agent that must not modify the checkout.
        role:       Agent name, for the logs.

    Returns:
        :class:`OpenCodeResult`. stdout carries the model's final message and
        nothing else, so a caller expecting JSON can parse it directly.

    Raises:
        RuntimeError: The binary is missing, the run timed out, or it exited
            non-zero. Never a silent failure: a caller that got no output would
            otherwise treat it as an empty answer.
    """
    if not repo_path.exists():
        raise RuntimeError(f"workspace path not found: {repo_path}")

    model_ref = f"ollama/{model_name}"
    agent = READ_ONLY_AGENT if read_only else WRITING_AGENT

    cmd = [
        settings.opencode_bin,
        "run",
        "--auto",  # auto-approve permissions — non-interactive
        "--agent",
        agent,
        "--dir",
        str(repo_path),
        "-m",
        model_ref,
        "--print-logs",
        "--log-level",
        "INFO",
        prompt,
    ]
    logger.info(f"[{role}] opencode {agent} agent → {model_ref} in {repo_path}")

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.opencode_timeout_s,
            env=_env(model_name),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"opencode run timed out after {settings.opencode_timeout_s}s") from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"opencode binary not found at {settings.opencode_bin} — install it or set OPENCODE_BIN"
        ) from exc

    duration_ms = int((time.monotonic() - start) * 1000)

    if result.returncode != 0:
        # Surface the tail of stderr so the failure is diagnosable in the logs.
        logger.error(f"[{role}] opencode exited {result.returncode}: {result.stderr[-2000:]}")
        raise RuntimeError(f"opencode run failed (exit {result.returncode})")

    logger.info(f"[{role}] opencode run complete in {duration_ms}ms")
    return OpenCodeResult(output=result.stdout, duration_ms=duration_ms)


def _env(model_name: str) -> dict[str, str]:
    """Environment carrying a provider config built from the registry.

    OpenCode resolves "ollama/<model>" against its own configuration file, which
    lives outside this project and lists models by hand. Anything the registry
    declares but that file omits fails at run time, after the analyst has already
    spent its time. Passing the config inline makes the registry the single source
    of truth it claims to be.

    The ambient environment is preserved: OpenCode runs pytest and ruff through
    PATH, and dropping it would silently disable the developer's definition of done.
    """
    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "ollama": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama (local)",
                # The OpenAI-compatible endpoint lives under /v1, while
                # ollama_base_url points at the server root.
                "options": {"baseURL": f"{settings.ollama_base_url.rstrip('/')}/v1"},
                "models": {model_name: {"name": model_name}},
            }
        },
    }
    return {**os.environ, "OPENCODE_CONFIG_CONTENT": json.dumps(config)}
