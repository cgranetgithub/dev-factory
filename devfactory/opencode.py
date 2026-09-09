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
import select
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
        result = _run_with_watchdog(cmd, _env(model_name), role)
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


class _StartupHangError(RuntimeError):
    """OpenCode produced nothing at all before the startup deadline.

    A subclass rather than a flag so the retry can tell this apart from every other
    failure without inspecting a message. It is still a ``RuntimeError``, so a
    caller that only knows about the documented failure type is unaffected.
    """


def _run_with_watchdog(
    cmd: list[str], env: dict[str, str], role: str
) -> subprocess.CompletedProcess[str]:
    """Run the CLI, give up early if it never starts working, and relaunch once.

    Observed three times: OpenCode initialises, logs its config, and then sits in
    its event loop having sent nothing to the model — Ollama idle, GPU at zero, no
    output. Under a single 30-minute timeout that costs half an hour of wall clock
    before the pipeline learns anything.

    So there are two deadlines. The startup one asks a narrow question: has the
    process produced *any* output yet? A working run prints its banner within
    seconds. The long one bounds the actual work, which legitimately takes minutes.

    The startup hang is transient and it is the harness, not the work: a process
    that never reached the model also never touched the checkout, so starting it
    again costs a few seconds and changes nothing else. It is retried exactly once —
    twice in a row is a broken host, not a hiccup, and the second failure raises so
    the pipeline stops instead of looping on it. A run that hit the *long* timeout
    is not retried: that one had started working, and re-running it would spend the
    same half hour again.
    """
    try:
        return _launch(cmd, env, role)
    except _StartupHangError:
        logger.warning(f"[{role}] opencode hung before reaching the model — restarting once")

    return _launch(cmd, env, role)


def _launch(cmd: list[str], env: dict[str, str], role: str) -> subprocess.CompletedProcess[str]:
    """Start the CLI once and wait on it under both deadlines."""
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    ) as process:
        try:
            return _wait_for(process, role)
        except BaseException:
            # Never leave the CLI running: it holds the model and the next attempt
            # would queue behind a process nobody is reading any more.
            process.kill()
            process.wait(timeout=10)
            raise


def _wait_for(process: subprocess.Popen[str], role: str) -> subprocess.CompletedProcess[str]:
    deadline = settings.opencode_startup_timeout_s
    try:
        stdout, stderr = process.communicate(timeout=deadline)
    except subprocess.TimeoutExpired:
        pass
    else:
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    # Still running after the startup deadline. That is normal for real work, and
    # the way to tell the two apart is whether anything has been written yet.
    if not _has_written_anything(process):
        raise _StartupHangError(
            f"opencode produced no output in {deadline}s — it appears to have hung "
            f"before reaching the model, so the run was abandoned rather than "
            f"waiting for the {settings.opencode_timeout_s}s limit"
        )

    logger.info(f"[{role}] opencode is working — waiting up to {settings.opencode_timeout_s}s")
    remaining = max(1, settings.opencode_timeout_s - deadline)
    try:
        stdout, stderr = process.communicate(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"opencode run timed out after {settings.opencode_timeout_s}s") from exc
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def _has_written_anything(process: subprocess.Popen[str]) -> bool:
    """Whether the process has written to stdout or stderr yet.

    Checked without reading, so the pipes stay intact for `communicate`: a
    non-empty read buffer on either descriptor is enough to say it is alive.
    """
    for stream in (process.stdout, process.stderr):
        if stream is None:
            continue
        ready, _, _ = select.select([stream], [], [], 0)
        if ready:
            return True
    return False


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
