"""
Check that the change touches the files the task said it would.

Twice now a run has passed every gate and produced a pull request containing a
feature that is never called: a fallback wrapped around an exception that cannot
reach it, and a helper module wired into nothing. In both cases the developer
wrote the new code and skipped the one line elsewhere that connects it. Lint sees
well-formed code, the type checker sees consistent types, the tests exercise the
new function directly, and the reviewer reads a diff that looks complete.

Nothing in that chain asks the one question that would have caught it: *did the
change touch the files the plan named?*

This gate does, and it costs a set comparison. It runs before the container and
before the model, because a gate that is nearly free should never queue behind
one that is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ScopeReport:
    """Difference between the files a task declared and the files it changed."""

    # Declared but never touched. This is the blocking signal: the plan said the
    # change would reach these files and it did not.
    missing: list[str] = field(default_factory=list)
    # Touched without being declared. Reported, never blocking — an analyst cannot
    # foresee every file a correct implementation needs, and blocking on that would
    # punish good work. It does catch drive-by edits to unrelated files, which have
    # shown up in several runs.
    unexpected: list[str] = field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return not self.missing

    def summary(self) -> str:
        """Feedback for the developer, naming the files rather than the rule."""
        lines = []
        if self.missing:
            lines.append(
                "The task declared these files, but the change does not touch them:\n"
                + "\n".join(f"- {p}" for p in self.missing)
                + "\n\nA new module that nothing calls is dead code. If one of these "
                "files is genuinely not needed, the work is not finished — wire the "
                "change in, or implement what is missing."
            )
        if self.unexpected:
            lines.append(
                "These files were changed without being part of the task:\n"
                + "\n".join(f"- {p}" for p in self.unexpected)
                + "\n\nRevert anything unrelated to the task."
            )
        return "\n\n".join(lines)


def _normalise(path: str) -> str:
    """Strip the decorations a model puts around a path so paths compare equal."""
    return path.strip().strip("`'\"").lstrip("./").rstrip("/")


def check_scope(declared: list[str], changed: list[str]) -> ScopeReport:
    """Compare the files a task declared against the files it actually changed.

    Args:
        declared: Paths from the task spec (created + modified), repo-relative.
        changed:  Paths the branch actually changed, repo-relative.

    Returns:
        A :class:`ScopeReport`. An empty ``declared`` yields an empty report: with
        nothing declared there is nothing to check, and inventing a violation from
        an absent plan would block every run whose analyst returned no file list.
    """
    declared_set = {_normalise(p) for p in declared if _normalise(p)}
    changed_set = {_normalise(p) for p in changed if _normalise(p)}

    if not declared_set:
        logger.info("[scope] the task declared no files — nothing to check")
        return ScopeReport()

    return ScopeReport(
        missing=sorted(declared_set - changed_set),
        unexpected=sorted(changed_set - declared_set),
    )
