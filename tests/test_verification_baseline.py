"""
Tests for the baseline store — the base branch's own report, cached per
``(repo, base SHA)`` in the knowledge base (issue #104).

Two things are being asserted, and only the first is about performance:

* the gate is run on a base commit **once**. Re-running it for every iteration of
  a loop that already runs three times would triple the cost of every pipeline
  run on a repository with standing debt;
* the rows are **append-only**. A baseline is a measurement of a commit that
  cannot change, and it is the evidence behind every later "this change
  introduced no new findings". A table a process can rewrite is not evidence —
  the same argument that governs ``control_snapshots``.

Docker is replaced by a runner stub and git by a checkout stub; nothing here needs
a container, a network or the real workspace.
"""

from __future__ import annotations

import json
from pathlib import Path

import git
import pytest

from devfactory.context import VerificationReport
from devfactory.kb.database import Database
from devfactory.verification import baseline
from devfactory.verification.runner import CLEAN, ERROR, FINDINGS

BASE_SHA = "a5ac825e1f2b3c4d5e6f708192a3b4c5d6e7f809"
OTHER_SHA = "0f1e2d3c4b5a69788796a5b4c3d2e1f009182736"
REPO = "cgranetgithub/news-watch"


def _report(ruff_status: str = FINDINGS, passed: bool = False) -> VerificationReport:
    """A report with one ruff finding, whose ruff status the caller chooses."""
    return VerificationReport(
        passed=passed,
        ruff={
            "status": ruff_status,
            "issues": [
                {
                    "filename": "/workspace/a.py",
                    "location": {"row": 3},
                    "code": "F401",
                    "message": "`os` imported but unused",
                }
            ],
        },
        mypy={"status": CLEAN, "errors": []},
        bandit={"status": CLEAN, "findings": [], "severity": "none"},
        pytest={"status": CLEAN, "passed": 210, "failed": 0, "errors": [], "raw": ""},
        summary="stub",
        raw_output="{}",
        environment="python 3.12 (from requires-python >=3.12), install from uv.lock",
    )


class _CountingRunner:
    """A gate that records how often it was asked to run, and on what."""

    def __init__(self, report: VerificationReport):
        self._report = report
        self.calls: list[tuple[Path, str | None]] = []

    def run(self, repo_path: Path, repo: str | None = None) -> VerificationReport:
        self.calls.append((repo_path, repo))
        return self._report


@pytest.fixture
def db(tmp_path) -> Database:
    """A knowledge base of this test's own. The singleton is never touched."""
    return Database(path=tmp_path / "devfactory.db")


@pytest.fixture
def checkout(monkeypatch, tmp_path) -> Path:
    """Stand in for ``git_ops.baseline_checkout``, which clones the workspace."""
    from devfactory.github import git_ops

    made = Path(tmp_path) / "baseline-checkout"
    made.mkdir()
    monkeypatch.setattr(git_ops, "baseline_checkout", lambda source, sha, name: made)
    return made


# ── The cache ──────────────────────────────────────────────────────────────────


def test_the_first_call_measures_the_base_and_stores_one_row(db, checkout, tmp_path):
    runner = _CountingRunner(_report())

    found, reason = baseline.baseline_for(REPO, tmp_path / "news-watch", BASE_SHA, runner, db)

    assert reason == ""
    assert found is not None
    assert found.ref.cached is False
    assert runner.calls == [(checkout, REPO)]
    assert len(db.verification_baselines(REPO)) == 1


def test_the_second_call_reuses_the_row_and_does_not_run_the_gate(db, checkout, tmp_path):
    runner = _CountingRunner(_report())
    baseline.baseline_for(REPO, tmp_path / "news-watch", BASE_SHA, runner, db)

    found, reason = baseline.baseline_for(REPO, tmp_path / "news-watch", BASE_SHA, runner, db)

    assert found is not None
    assert found.ref.cached is True
    assert len(runner.calls) == 1, "the gate ran twice on the same base commit"
    assert len(db.verification_baselines(REPO)) == 1


def test_the_cached_row_carries_the_findings_it_was_measured_with(db, checkout, tmp_path):
    runner = _CountingRunner(_report())
    baseline.baseline_for(REPO, tmp_path / "news-watch", BASE_SHA, runner, db)

    found, _ = baseline.baseline_for(REPO, tmp_path / "news-watch", BASE_SHA, runner, db)

    assert found is not None
    assert found.tools["ruff"]["keys"] == [["ruff", "a.py", "F401", "`os` imported but unused"]]


def test_a_new_base_sha_inserts_a_new_row(db, checkout, tmp_path):
    runner = _CountingRunner(_report())
    baseline.baseline_for(REPO, tmp_path / "news-watch", BASE_SHA, runner, db)

    baseline.baseline_for(REPO, tmp_path / "news-watch", OTHER_SHA, runner, db)

    rows = db.verification_baselines(REPO)
    assert len(rows) == 2
    assert {r["base_sha"] for r in rows} == {BASE_SHA, OTHER_SHA}
    assert len(runner.calls) == 2


def test_a_baseline_is_scoped_to_its_repository(db, checkout, tmp_path):
    runner = _CountingRunner(_report())
    baseline.baseline_for(REPO, tmp_path / "news-watch", BASE_SHA, runner, db)

    # The same SHA under another slug is another repository's commit, and the
    # profile that measured it may differ. Nothing is shared across the key.
    assert db.verification_baseline("cgranetgithub/biz-explore", BASE_SHA) is None


# ── Append-only ────────────────────────────────────────────────────────────────


def test_the_store_offers_no_way_to_update_or_delete_a_baseline():
    """A measurement of an immutable commit is not something a later run rewrites."""
    forbidden = [
        name
        for name in dir(Database)
        if "baseline" in name and any(verb in name for verb in ("update", "delete", "set_"))
    ]
    assert forbidden == []


def test_a_baseline_whose_tool_errored_is_measured_again_without_erasing_the_row(
    db, checkout, tmp_path
):
    """A tool that crashed on the base may have crashed for a reason of ours.

    Leaving that row to govern the SHA forever would make one bad container
    poison a repository. Measuring again inserts a new row; the first one stays as
    the record that it happened.
    """
    broken = _CountingRunner(_report(ruff_status=ERROR))
    baseline.baseline_for(REPO, tmp_path / "news-watch", BASE_SHA, broken, db)

    working = _CountingRunner(_report())
    found, _ = baseline.baseline_for(REPO, tmp_path / "news-watch", BASE_SHA, working, db)

    rows = db.verification_baselines(REPO)
    assert len(rows) == 2, "the broken reading was overwritten instead of superseded"
    assert json.loads(rows[-1]["fingerprints_json"])["ruff"]["status"] == ERROR
    assert found is not None and found.ref.cached is False


def test_the_row_records_what_the_reading_was_taken_with(db, checkout, tmp_path):
    """Two readings of one commit on different interpreters are two verdicts."""
    baseline.baseline_for(REPO, tmp_path / "news-watch", BASE_SHA, _CountingRunner(_report()), db)

    row = db.verification_baseline(REPO, BASE_SHA)

    assert row is not None
    assert row["environment"].startswith("python 3.12")
    assert row["recorded_at"]
    assert row["passed"] == 0


# ── Failing to get one ─────────────────────────────────────────────────────────


def test_a_checkout_that_cannot_be_made_yields_no_baseline_and_a_reason(monkeypatch, db, tmp_path):
    from devfactory.github import git_ops

    def refuse(source, sha, name):
        raise git.GitCommandError("checkout", 128, b"unknown revision")

    monkeypatch.setattr(git_ops, "baseline_checkout", refuse)

    found, reason = baseline.baseline_for(
        REPO, tmp_path / "news-watch", BASE_SHA, _CountingRunner(_report()), db
    )

    assert found is None
    assert "could not be checked out" in reason
    assert db.verification_baselines(REPO) == []


def test_a_gate_that_cannot_run_on_the_base_records_nothing(checkout, db, tmp_path):
    """Not a verdict on the base commit, so it must not be stored as one."""

    class _Broken:
        def run(self, repo_path, repo=None):
            raise FileNotFoundError("Repo path not found")

    found, reason = baseline.baseline_for(REPO, tmp_path / "news-watch", BASE_SHA, _Broken(), db)

    assert found is None
    assert "could not run on the base commit" in reason
    assert db.verification_baselines(REPO) == []
