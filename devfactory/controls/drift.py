"""Field-by-field comparison of two consecutive control snapshots.

"The configuration changed" is not evidence an auditor can use; "on 2026-09-09 the
required approval count on the default branch went from 1 to 0" is. So the
comparison flattens both snapshots to dotted paths and reports one entry per
field that moved, rather than a whole-document diff.
"""

from __future__ import annotations

from typing import Any

# The three shapes a change can take, kept as plain strings because they are
# stored in the KB as JSON and read back by tools we do not control.
CHANGED = "changed"
ADDED = "added"
REMOVED = "removed"


def compute_drift(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare two canonical snapshots and return one entry per field that moved.

    Args:
        previous: The last recorded snapshot for the repository, or ``None`` when
            this is the first one (a baseline has nothing to drift from).
        current: The snapshot just taken.

    Returns:
        A list of ``{"path", "kind", "before", "after"}`` dicts, ordered by path.
        Empty when nothing changed.
    """
    if previous is None:
        return []

    before = flatten(previous)
    after = flatten(current)

    changes: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        if path not in after:
            changes.append(_change(path, REMOVED, before[path], None))
        elif path not in before:
            changes.append(_change(path, ADDED, None, after[path]))
        elif before[path] != after[path]:
            changes.extend(_compare_values(path, before[path], after[path]))
    return changes


def format_change(change: dict[str, Any]) -> str:
    """Render one change as the single line the CLI and the PR body show."""
    kind = change["kind"]
    if kind == ADDED:
        return f"{change['path']}: + {_render(change['after'])}"
    if kind == REMOVED:
        return f"{change['path']}: - {_render(change['before'])}"
    return f"{change['path']}: {_render(change['before'])} -> {_render(change['after'])}"


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a snapshot to ``{dotted.path: leaf}``.

    Lists stay whole so ``_compare_values`` can diff their membership; an empty
    dict stays whole too, so that a parameterless rule such as ``non_fast_forward``
    disappearing is reported as a removal instead of vanishing silently along with
    its path. The CLI reuses this so the state it prints is keyed exactly like the
    drift it prints underneath.
    """
    if isinstance(value, dict) and value:
        flat: dict[str, Any] = {}
        for key, sub in value.items():
            flat.update(flatten(sub, f"{prefix}.{key}" if prefix else str(key)))
        return flat
    return {prefix: value}


# ── Internals ─────────────────────────────────────────────────────────────────


def _compare_values(path: str, before: Any, after: Any) -> list[dict[str, Any]]:
    """Compare two values at the same path, splitting lists into item changes.

    A list membership change (a bypass actor granted, a collaborator removed) is
    reported as that item arriving or leaving rather than as the whole list being
    replaced, which would bury the one entry that matters.
    """
    if isinstance(before, list) and isinstance(after, list):
        gone = [item for item in before if item not in after]
        arrived = [item for item in after if item not in before]
        return [_change(path, REMOVED, item, None) for item in gone] + [
            _change(path, ADDED, None, item) for item in arrived
        ]
    return [_change(path, CHANGED, before, after)]


def _change(path: str, kind: str, before: Any, after: Any) -> dict[str, Any]:
    return {"path": path, "kind": kind, "before": before, "after": after}


def _render(value: Any) -> str:
    """Render a leaf compactly — quoted strings, bare scalars, JSON-ish containers."""
    if isinstance(value, str):
        return f"'{value}'"
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
