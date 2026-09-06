"""
Make the host ready before a run, instead of failing halfway through one.

Two things have silently blocked every pipeline run in this project's history:
a dependency missing from the verification image, and a model the registry
declared that the runtime could not resolve. Both were invisible until something
unrelated failed, and both were the user's to fix by hand.

The factory should provision what it declares. What cannot be provisioned — the
version of a server it does not own — is reported up front, in one line, rather
than discovered through a model that behaves strangely.
"""

from __future__ import annotations

import logging

import httpx

from devfactory.config import settings
from devfactory.models.client import ollama
from devfactory.models.registry import MODELS

logger = logging.getLogger(__name__)


def prepare_host() -> None:
    """Run every readiness check. Never raises: a warned run beats no run."""
    check_ollama_version()
    ensure_models_available()


def _parse_version(raw: str) -> tuple[int, ...]:
    """Parse "0.33.3" or "0.33.3-rc1" into a comparable tuple.

    Unparseable input yields an empty tuple, which compares below everything and
    therefore triggers the warning rather than silently passing.
    """
    head = raw.strip().lstrip("v").split("-", 1)[0]
    parts = []
    for chunk in head.split("."):
        if not chunk.isdigit():
            break
        parts.append(int(chunk))
    return tuple(parts)


def check_ollama_version() -> bool:
    """Warn when the Ollama server is older than the validated minimum.

    A warning, not an error: an older server may well work. But its differences
    show up as behaviour rather than as failures — a model that answers in prose
    instead of calling tools, or a context window that silently truncates — and
    those cost hours to diagnose. One line at the start of the run is cheaper.

    Returns:
        True when the version is at or above the minimum, or could not be read.
    """
    try:
        running = ollama.version()
    except (httpx.HTTPError, OSError) as e:
        logger.warning(f"[provisioning] could not read the Ollama version ({e})")
        return True

    if _parse_version(running) >= _parse_version(settings.min_ollama_version):
        logger.info(f"[provisioning] Ollama {running}")
        return True

    logger.warning(
        f"[provisioning] Ollama {running} is older than the validated minimum "
        f"{settings.min_ollama_version}. This is not necessarily broken, but older "
        f"releases differ in tool calling and context handling — symptoms look like "
        f"a weak model, not like an error. Consider upgrading."
    )
    return False


def ensure_models_available() -> list[str]:
    """Pull every registry model Ollama does not have yet.

    Without this the router quietly skips missing models, so the pool narrows
    without anyone noticing: a registry of six models can silently become a
    registry of one, and the comparison the knowledge base is built for stops
    meaning anything.

    Returns:
        The models actually pulled. Failures are logged and skipped rather than
        aborting: one unavailable model must not take the whole run down when the
        others can still do the work.
    """
    if not settings.auto_pull_models:
        logger.info("[provisioning] auto-pull disabled — skipping model provisioning")
        return []

    try:
        available = set(ollama.list_models())
    except (httpx.HTTPError, OSError) as e:
        logger.warning(f"[provisioning] could not list Ollama models ({e}) — skipping pulls")
        return []

    missing = [m.name for m in MODELS if m.name not in available]
    if not missing:
        return []

    # Weights are multi-GB: say what is happening, or the run looks hung.
    logger.warning(
        f"[provisioning] {len(missing)} registered model(s) missing from Ollama: "
        f"{', '.join(missing)} — pulling now, this can take several minutes each"
    )

    pulled = []
    for name in missing:
        try:
            ollama.pull_model(name)
        except (httpx.HTTPError, OSError, RuntimeError) as e:
            logger.error(f"[provisioning] could not pull {name}: {e}")
            continue
        logger.info(f"[provisioning] pulled {name}")
        pulled.append(name)

    return pulled
