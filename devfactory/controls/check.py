"""One control check: read, compare with the previous reading, record.

The three steps belong together because none of them is evidence on its own — a
reading nobody stored proves nothing, and a drift computed against a reading that
was never persisted cannot be re-derived later.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from devfactory.controls.drift import compute_drift
from devfactory.controls.snapshot import canonical_json, snapshot_sha256, take_snapshot
from devfactory.kb.database import Database

logger = logging.getLogger(__name__)


@dataclass
class ControlCheck:
    """The outcome of one ``devfactory controls check`` run."""

    repo: str
    taken_at: str
    snapshot: dict[str, Any]
    sha256: str
    snapshot_id: int
    baseline: bool
    changes: list[dict[str, Any]] = field(default_factory=list)
    previous_id: int | None = None
    previous_taken_at: str | None = None

    @property
    def drifted(self) -> bool:
        """Whether anything moved since the previous reading."""
        return bool(self.changes)


def check_repository(repo: str, database: Database | None = None) -> ControlCheck:
    """Snapshot a repository's controls, diff against the last reading, record both.

    Args:
        repo: Repository in ``owner/repo`` form.
        database: Injected knowledge base; the ``db`` singleton by default.

    Returns:
        The recorded :class:`ControlCheck`.

    Raises:
        SnapshotError: If the GitHub configuration could not be read; nothing is
            recorded in that case, because a failed reading is not a reading of a
            compliant state and must not be storable as one.
    """
    if database is None:
        from devfactory.kb.database import db

        database = db

    snapshot = take_snapshot(repo)
    payload = canonical_json(snapshot)
    digest = snapshot_sha256(snapshot)
    taken_at = datetime.now(UTC).isoformat()

    previous = database.latest_control_snapshot(repo)
    previous_snapshot = json.loads(previous["snapshot_json"]) if previous else None
    changes = compute_drift(previous_snapshot, snapshot)

    snapshot_id = database.record_control_snapshot(
        repo=repo,
        taken_at=taken_at,
        snapshot_json=payload,
        sha256=digest,
        # NULL, not "[]", on a baseline: "nothing to compare against" and "compared,
        # nothing moved" are different facts and the evidence must keep them apart.
        drift_json=None if previous is None else json.dumps(changes),
        previous_id=previous["id"] if previous else None,
    )

    if changes:
        logger.warning(f"[controls] {repo}: {len(changes)} control change(s) since last check")
    else:
        logger.info(f"[controls] {repo}: no drift (sha256 {digest[:12]})")

    return ControlCheck(
        repo=repo,
        taken_at=taken_at,
        snapshot=snapshot,
        sha256=digest,
        snapshot_id=snapshot_id,
        baseline=previous is None,
        changes=changes,
        previous_id=previous["id"] if previous else None,
        previous_taken_at=previous["taken_at"] if previous else None,
    )
