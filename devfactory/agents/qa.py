"""
QA Agent — interprets the Docker QA report and decides pass/retry.
The actual test execution happens in devfactory.qa.runner (Docker).
"""

from __future__ import annotations

import logging

from devfactory.agents.base import BaseAgent
from devfactory.context import PipelineContext
from devfactory.qa.runner import QARunner

logger = logging.getLogger(__name__)


class QAAgent(BaseAgent):
    role = "qa"

    def __init__(self):
        super().__init__()
        self._runner = QARunner()

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from devfactory.config import settings

        repo_path = settings.workspace / ctx.repo_name
        logger.info(f"[qa] running QA on {repo_path}")

        # 1. Run tools in Docker, get structured report
        report = self._runner.run(repo_path)
        ctx.qa_report = report

        # 2. Log result summary
        status = "PASSED" if report.passed else "FAILED"
        logger.info(f"[qa] {status} — {report.summary}")

        return ctx
