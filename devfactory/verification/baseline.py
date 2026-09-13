"""
The base branch's own verification report, cached per ``(repo, base SHA)``.

The differential verdict (issue #104) needs to know what was already wrong before
the change, and the only honest source of that is the gate itself, run on the
commit the branch left. Running it there on every iteration would triple the cost
of a loop that already runs three times, so each reading is written to the
knowledge base and read back.

Caching is not the only reason it is stored, and arguably not the main one. A
baseline row is the evidence behind the sentence a differential verdict is for:
*this change introduced no new findings.* It says which commit that was measured
against, when, with which environment, and exactly what was already there. The
table is append-only for the same reason ``control_snapshots`` is — a measurement
of an immutable commit is not something a later run gets to rewrite.

Nothing here is fatal. Every way of failing to obtain a baseline returns None with
a reason, and the caller falls back to the absolute rule and says so: the gate
that cannot measure the base is the gate we had before this module existed, which
over-blocks but never lets a regression through.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import git

from devfactory.context import VerificationReport
from devfactory.kb.database import Database
from devfactory.kb.database import db as _global_db
from devfactory.verification.differential import BaselineRef, fingerprints
from devfactory.verification.runner import ERROR, VerificationRunner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Baseline:
    """One reading of a base commit's own gate."""

    ref: BaselineRef
    #: Per tool: its status on the base commit, and the keys of what it found.
    tools: dict[str, dict[str, Any]]

    @property
    def usable_tools(self) -> list[str]:
        """Tools whose reading can be compared against. See ``differential.judge``."""
        return [name for name, entry in self.tools.items() if entry.get("status") != ERROR]


def record(
    report: VerificationReport,
    repo: str,
    base_sha: str,
    database: Database | None = None,
) -> int:
    """Write one gate run down as the baseline for ``base_sha``.

    Called wherever the gate is run on a commit that is somebody's base: by
    :func:`baseline_for` when it has to measure one, and by the onboarding dry run,
    which runs exactly this gate on the default branch and would otherwise throw
    the reading away.

    Returns:
        The row id.
    """
    db = database or _global_db
    row_id = db.record_verification_baseline(
        repo=repo,
        base_sha=base_sha,
        recorded_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
        passed=report.passed,
        fingerprints_json=json.dumps(fingerprints(report)),
        environment=report.environment,
    )
    logger.info(f"[baseline] recorded the gate on {repo}@{base_sha[:8]} as baseline #{row_id}")
    return row_id


def baseline_for(
    repo: str,
    workspace: Path,
    base_sha: str,
    runner: VerificationRunner | None = None,
    database: Database | None = None,
) -> tuple[Baseline | None, str]:
    """The base commit's report — from the knowledge base, or measured and stored.

    Args:
        repo: The target's ``owner/repo`` slug. Also selects its verification
            profile, so the baseline is measured by the same gate as the branch.
        workspace: The run's checkout. It contains ``base_sha``, and it is cloned
            rather than moved — see ``git_ops.baseline_checkout``.
        base_sha: The commit the branch left the default branch at.
        runner: The gate. Injected in tests; the default is the real one.
        database: The knowledge base. Injected in tests.

    Returns:
        The baseline and an empty reason, or None and the reason there is none.
        A cached reading whose tools all ran is returned as it stands; one where a
        tool ended in the error state is measured again, because that error may
        have been ours and a poisoned row would otherwise govern the SHA forever.
        The re-measurement inserts a new row; the old one is left as the record
        that it happened.
    """
    db = database or _global_db

    cached = db.verification_baseline(repo, base_sha)
    if cached is not None:
        tools = json.loads(cached["fingerprints_json"])
        errored = [name for name, entry in tools.items() if entry.get("status") == ERROR]
        if not errored:
            logger.info(f"[baseline] reusing baseline #{cached['id']} for {repo}@{base_sha[:8]}")
            return (
                Baseline(
                    ref=BaselineRef(
                        repo=repo,
                        base_sha=base_sha,
                        recorded_at=cached["recorded_at"],
                        cached=True,
                    ),
                    tools=tools,
                ),
                "",
            )
        logger.warning(
            f"[baseline] baseline #{cached['id']} for {repo}@{base_sha[:8]} has "
            f"{', '.join(errored)} in the error state — measuring the base again"
        )

    return _measure(repo, workspace, base_sha, runner or VerificationRunner(), db)


def _measure(
    repo: str,
    workspace: Path,
    base_sha: str,
    runner: VerificationRunner,
    db: Database,
) -> tuple[Baseline | None, str]:
    """Run the gate on the base commit and store what it found."""
    from devfactory.github import git_ops

    try:
        checkout = git_ops.baseline_checkout(workspace, base_sha, workspace.name)
    except (git.GitError, OSError) as exc:
        reason = f"the base commit {base_sha[:8]} could not be checked out ({exc})"
        logger.warning(f"[baseline] {reason}")
        return None, reason

    try:
        report = runner.run(checkout, repo=repo)
    except (OSError, ValueError, RuntimeError) as exc:
        # The gate itself failed to start on the base — a missing image, an
        # unreadable profile. Not a verdict on the base commit, so nothing is
        # recorded and the branch is judged absolutely.
        reason = f"the gate could not run on the base commit {base_sha[:8]} ({exc})"
        logger.warning(f"[baseline] {reason}")
        return None, reason

    row_id = record(report, repo, base_sha, database=db)
    row = db.verification_baseline(repo, base_sha)
    recorded_at = row["recorded_at"] if row else datetime.now(UTC).replace(tzinfo=None).isoformat()
    logger.info(
        f"[baseline] measured {repo}@{base_sha[:8]} (row #{row_id}): "
        f"the base commit {'passes' if report.passed else 'does not pass'} its own gate"
    )
    return (
        Baseline(
            ref=BaselineRef(repo=repo, base_sha=base_sha, recorded_at=recorded_at, cached=False),
            tools=fingerprints(report),
        ),
        "",
    )
