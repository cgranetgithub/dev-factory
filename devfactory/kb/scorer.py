"""
Scorer — persists all agent executions and quality scores from a completed pipeline run.
Called by the orchestrator after each agent and at the end of the pipeline.
"""

from __future__ import annotations

import logging

from devfactory.context import PipelineContext
from devfactory.kb.database import Database
from devfactory.kb.database import db as _global_db

logger = logging.getLogger(__name__)


class Scorer:
    def __init__(self, database: Database | None = None):
        self._db = database or _global_db

    def flush(self, ctx: PipelineContext, task_id: int):
        """
        Persist all executions logged in ctx to the DB, then compute and store scores.
        """
        for entry in ctx.execution_log:
            exec_id = self._db.record_execution(
                task_id=task_id,
                model_name=entry["model"],
                agent_type=entry["agent"],
                prompt_tokens=entry["prompt_tokens"],
                completion_tokens=entry["completion_tokens"],
                duration_ms=entry["duration_ms"],
            )
            self._score_entry(exec_id, entry, ctx)

    def _score_entry(self, exec_id: int, entry: dict, ctx: PipelineContext):
        agent = entry["agent"]

        if agent == "verification":
            # Verification scores are now attributed to the developer execution
            # This branch is kept to not affect the function signature but won't be called
            # in practice with the new logic
            pass
        elif agent == "reviewer":
            self._score_reviewer(exec_id, ctx)
        elif agent == "developer":
            self._score_developer_quality(exec_id, ctx)
            self._db.record_score(
                exec_id,
                "retry_count",
                ctx.verification_attempts,
                f"Verification retries: {ctx.verification_attempts}",
            )

    def _score_developer_quality(self, exec_id: int, ctx: PipelineContext):
        if ctx.verification_report is None:
            return
        r = ctx.verification_report

        # tests_pass_rate: ratio of passed tests
        total = r.pytest.get("passed", 0) + r.pytest.get("failed", 0)
        pass_rate = r.pytest["passed"] / total if total > 0 else (1.0 if r.passed else 0.0)
        self._db.record_score(exec_id, "tests_pass_rate", pass_rate)

        # lint_score: 0.0 (many issues) → 1.0 (clean). Measured on the verification
        # report, i.e. AFTER autofix — this is the state of the code that reached the
        # gate, not the state the developer delivered. See lint_left_behind below.
        ruff_issues = len(r.ruff.get("issues", []))
        lint_score = max(0.0, 1.0 - ruff_issues * 0.05)
        self._db.record_score(
            exec_id, "lint_score", lint_score, f"{ruff_issues} ruff issues after autofix"
        )

        # lint_left_behind: what the developer itself failed to clean up, measured
        # before autofix ran. This is the honest signal about the model — a model that
        # runs ruff as instructed scores 0 here, one that ignores its definition of
        # done does not. Entries are one per attempt; None means unmeasured.
        measured = [n for n in ctx.lint_left_behind if n is not None]
        if measured:
            self._db.record_score(
                exec_id,
                "lint_left_behind",
                float(measured[-1]),
                f"issues left for autofix, per attempt: {ctx.lint_left_behind}",
            )

        # security: bandit severity → score
        severity_map = {"LOW": 0.8, "MEDIUM": 0.5, "HIGH": 0.2, "none": 1.0}
        severity = r.bandit.get("severity", "none")
        self._db.record_score(exec_id, "security_score", severity_map.get(severity, 0.5), severity)

    def _score_reviewer(self, exec_id: int, ctx: PipelineContext):
        if not ctx.review_results:
            return
        last = ctx.review_results[-1]
        verdict_map = {"approved": 1.0, "commented": 0.6, "changes_requested": 0.3}
        score = verdict_map.get(last.verdict, 0.5)
        self._db.record_score(exec_id, "review_verdict", score, last.verdict)
        self._db.record_score(exec_id, "review_quality", last.score, last.summary[:200])


scorer = Scorer()
