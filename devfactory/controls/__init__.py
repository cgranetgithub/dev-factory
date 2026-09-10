"""Control monitoring — evidence that the branch-protection controls still operate.

A control that is only *declared* is not a control. The configuration enforcing
separation of duties on the default branch (the ruleset, required approvals,
CODEOWNERS, the bypass list, collaborator roles) is mutable, so this package
reads it through the GitHub API, normalises it, hashes it, stores every reading
append-only in the knowledge base and reports what changed since the previous one.

The series of timestamped records — not any single reading — is what answers the
question SOC 2 Type II and ISO 27001 actually ask: did the control operate
*throughout the period*? See ``docs/VISION.md``, "Verifying the controls".
"""

from devfactory.controls.check import ControlCheck, check_repository
from devfactory.controls.drift import compute_drift, flatten, format_change
from devfactory.controls.snapshot import (
    SnapshotError,
    canonical_json,
    snapshot_sha256,
    take_snapshot,
)

__all__ = [
    "ControlCheck",
    "SnapshotError",
    "canonical_json",
    "check_repository",
    "compute_drift",
    "flatten",
    "format_change",
    "snapshot_sha256",
    "take_snapshot",
]
