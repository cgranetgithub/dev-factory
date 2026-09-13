"""
Verification Agent — runs the deterministic checks and records the report.

Ruff, mypy, bandit and pytest run in Docker (devfactory.verification.runner). This
agent calls no model; the graph reads the report and decides whether the change
proceeds or goes back.

The tools measure; this agent decides how the measurement is read. Since issue
#104 that is a differential verdict: the same four tools over the whole
repository, but the report fails on what the change *introduced*, judged against
the base commit's own report (devfactory.verification.differential). The absolute
rule is still there and still applies whenever no baseline can be had — it
over-blocks, which is the safe direction, and the summary says which rule was used.
"""

from __future__ import annotations

import logging

from devfactory.agents.base import BaseAgent
from devfactory.context import PipelineContext, VerificationReport
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

        # 2. Re-read the same measurements against the base branch's own report.
        report = self._judge(ctx, report)
        ctx.verification_report = report

        # 3. Log result summary
        status = "PASSED" if report.passed else "FAILED"
        logger.info(f"[verification] {status} ({report.rule}) — {report.summary}")

        return ctx

    def _judge(self, ctx: PipelineContext, report: VerificationReport) -> VerificationReport:
        """Apply the pass rule: differential when a baseline can be had, else absolute.

        Every way of not getting a baseline — the setting turned off, a branch
        whose base commit cannot be found, a gate that could not run on it — ends
        the same way: the absolute rule, with the reason written into the summary.
        Falling back rather than failing is deliberate. The absolute rule is
        conservative (it can send a change back for a finding it did not cause, it
        cannot let a regression through), and failing the run outright would turn a
        missing cache row into an unexplainable verification failure, which is the
        experience issue #104 exists to end.
        """
        from devfactory.config import settings
        from devfactory.github import git_ops
        from devfactory.verification import baseline as baseline_store
        from devfactory.verification import differential

        if not settings.differential_gate:
            return differential.apply(
                report,
                differential.judge(report, None, reason="the differential gate is turned off"),
            )

        sha = git_ops.base_sha(ctx)
        if not sha:
            return differential.apply(
                report,
                differential.judge(
                    report, None, reason="the branch's base commit could not be determined"
                ),
            )

        found, reason = baseline_store.baseline_for(
            repo=ctx.issue.repo, workspace=git_ops.workspace_path(ctx), base_sha=sha
        )
        if found is None:
            return differential.apply(report, differential.judge(report, None, reason=reason))

        return differential.apply(report, differential.judge(report, found.tools, ref=found.ref))
