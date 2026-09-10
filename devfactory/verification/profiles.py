"""
Per-repository verification profiles — how the gate treats one target repository.

The profile lives here, in DevFactory, and not in the target: a client repository
must not have to know that we exist. It is keyed by ``owner/repo`` in
``profiles/verification.toml``, and **every field is optional**. What is not
declared is derived from the repository itself (see
:mod:`devfactory.verification.environment`), so the common case needs no entry at
all — an empty file is the expected state.

TOML rather than YAML: ``tomllib`` is in the standard library, the file describes
the same things ``pyproject.toml`` declares, and adding a YAML dependency to read
six optional keys is the wheel we are told not to reinvent.

A profile is a control on the gate: it can change what runs, so a typo in it must
be loud. Unknown keys are rejected, and so are tool names and version strings that
are not what they claim to be — a silently ignored line would mean the recorded
verdict was produced by a gate nobody configured.
"""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

logger = logging.getLogger(__name__)

# Mirrors PROMPTS_DIR in devfactory/agents/base.py: data the factory loads at
# runtime lives beside the package, versioned with it.
PROFILES_PATH = Path(__file__).parent.parent.parent / "profiles" / "verification.toml"

# The four tools the gate runs. A profile may narrow this list; it cannot invent
# a tool the runner has no code for.
TOOLS = ("ruff", "mypy", "bandit", "pytest")

# `3.12`, or a full `3.12.7` when a target needs a specific patch release.
_VERSION_RE = re.compile(r"^3\.\d+(\.\d+)?$")


class ProfileError(ValueError):
    """The profile file exists but cannot be trusted."""


class VerificationProfile(BaseModel):
    """What one repository needs the gate to do differently.

    Attributes:
        python_version: Interpreter the target is verified with, e.g. ``"3.14"``.
            Overrides everything the repository declares — use it when the
            repository declares nothing and its CI knows better.
        install: Shell commands replacing the derived install. They run in the
            copy of the checkout, with a virtualenv already created and
            ``VIRTUAL_ENV`` exported, so ``uv pip install …`` reaches it.
        extras: Optional-dependency groups to install. Replaces the derived set
            (see ``DERIVED_EXTRAS``); ``[]`` installs none.
        tools: Which of :data:`TOOLS` run. A tool left out is reported as skipped,
            naming this profile — never as a pass.
        ruff_args: Extra arguments appended to ``ruff check``.
        mypy_args: Extra arguments appended to ``mypy``.
        pytest_args: Extra arguments appended to ``pytest``.
    """

    # forbid: an unrecognised key is a mistake in a file that governs the gate.
    model_config = ConfigDict(extra="forbid", frozen=True)

    python_version: str | None = None
    install: list[str] | None = None
    extras: list[str] | None = None
    tools: list[str] = list(TOOLS)
    ruff_args: list[str] = []
    mypy_args: list[str] = []
    pytest_args: list[str] = []

    @field_validator("python_version")
    @classmethod
    def _check_version(cls, value: str | None) -> str | None:
        if value is not None and not _VERSION_RE.match(value):
            raise ValueError(f"python_version must look like '3.12', got {value!r}")
        return value

    @field_validator("tools")
    @classmethod
    def _check_tools(cls, value: list[str]) -> list[str]:
        unknown = [t for t in value if t not in TOOLS]
        if unknown:
            raise ValueError(f"unknown tool(s) {unknown}; known tools are {list(TOOLS)}")
        return value

    def runs(self, tool: str) -> bool:
        """Whether ``tool`` is part of the gate for this repository."""
        return tool in self.tools

    @property
    def skipped_tools(self) -> list[str]:
        """The tools this profile leaves out, in the gate's own order."""
        return [t for t in TOOLS if t not in self.tools]


def load_profile(repo: str | None, path: Path | None = None) -> VerificationProfile:
    """The profile for ``repo`` (``owner/repo``), or pure defaults.

    A repository with no entry — the expected case — gets
    ``VerificationProfile()``, which is "derive everything".

    Args:
        repo: The target's ``owner/repo`` slug, or None when the caller has no
            slug to key on (a bare path). Matched case-insensitively, as GitHub
            treats it.
        path: The profile file. Defaults to :data:`PROFILES_PATH`; tests pass
            their own.

    Raises:
        ProfileError: The file is not valid TOML, or an entry is not a valid
            profile. Refusing is the point: a gate configured by accident cannot
            produce evidence.
    """
    source = path or PROFILES_PATH
    if not source.exists():
        logger.debug(f"[verification] no profile file at {source}; deriving everything")
        return VerificationProfile()

    try:
        data = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"could not read the verification profiles at {source}: {exc}") from exc

    # Validate every entry, not just the one we need: a broken profile for another
    # repository is a defect in this file, and the run that finds it should say so
    # rather than leave it for the day that repository is next verified.
    profiles: dict[str, VerificationProfile] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            raise ProfileError(f"{source}: '{key}' must be a table keyed by owner/repo")
        try:
            profiles[key.lower()] = VerificationProfile(**entry)
        except ValueError as exc:
            raise ProfileError(f"{source}: profile for '{key}' is invalid: {exc}") from exc

    profile = profiles.get((repo or "").lower())
    if profile is None:
        return VerificationProfile()
    logger.info(f"[verification] using the verification profile for {repo}")
    return profile
