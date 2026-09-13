"""
Verification Agent — runs the deterministic checks and records the report.

Ruff, mypy, bandit and pytest run in Docker (devfactory.verification.runner). This
agent calls no model; the graph reads the report and decides whether the change
proceeds or goes back.
"""

from __future__ import annotations

import logging

from devfactory.agents.base import BaseAgent
from devfactory.context import PipelineContext
from devfactory.verification.runner import VerificationRunner

logger = logging.getLogger(__name__)


class VerificationAgent(BaseAgent):
    role = "verification"
    # Verification runs the Docker tools (ruff/mypy/bandit/pytest) and never calls an LLM,
    # so no model is selected for it and no "verification" execution is recorded.
    requires_model = False

    def __init__(self):
        super().__init__()
        self._runner = VerificationRunner()

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from devfactory.config import settings

        repo_path = settings.workspace / ctx.repo_name
        logger.info(f"[verification] running verification on {repo_path}")

        # 1. Run tools in Docker, get structured report. The slug is what the
        # target's verification profile is keyed on — which Python, which install,
        # which tools — so the gate has to know which repository it is verifying.
        report = self._runner.run(repo_path, repo=ctx.issue.repo)
        ctx.verification_report = report

        # 2. Log result summary
        status = "PASSED" if report.passed else "FAILED"
        logger.info(f"[verification] {status} — {report.summary}")

        return ctx
