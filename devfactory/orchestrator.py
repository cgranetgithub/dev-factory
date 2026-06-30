"""
Orchestrator — runs the sequential agent pipeline for a single GitHub issue.

Flow:
  1. Analyst    → reads issue, produces TaskSpec
  2. Git setup  → clone repo, create feature branch
  3. Dev→QA loop → developer writes code, QA validates (max N retries)
  4. Git push   → push feature branch to remote
  5. PR         → create GitHub PR
  6. Reviewer×2 → inline code reviews (different models)
  7. Notify     → human notification on the issue
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from devfactory.context import GitHubIssue, PipelineContext
from devfactory.kb.database import db
from devfactory.kb.scorer import scorer

logger = logging.getLogger(__name__)


class QAFailedError(RuntimeError):
    """Raised when the Dev↔QA loop has exhausted all its attempts.

    Distinct from a generic error: the poller catches it specifically to apply
    the ``devfactory:qa-failed`` label instead of ``devfactory:error``.
    """


class Pipeline:
    def __init__(self):
        from devfactory.agents.analyst import AnalystAgent
        from devfactory.agents.developer import DeveloperAgent
        from devfactory.agents.qa import QAAgent
        from devfactory.agents.reviewer import ReviewerAgent

        self.analyst = AnalystAgent()
        self.developer = DeveloperAgent()
        self.qa = QAAgent()
        self.reviewer = ReviewerAgent()

    def run(self, issue: GitHubIssue) -> PipelineContext:
        ctx = PipelineContext(issue=issue)
        task_id = db.create_task(issue.number, issue.repo)
        db.update_task(task_id, status="in_progress")

        logger.info(f"[pipeline] start issue=#{issue.number} '{issue.title}' repo={issue.repo}")

        try:
            # ── 1. Analyst ────────────────────────────────────────────────────
            ctx = self.analyst.execute(ctx)

            # ── 2. Git: clone + create branch ─────────────────────────────────
            self._setup_git(ctx)
            db.update_task(task_id, branch_name=ctx.branch_name)

            # ── 3. Developer → QA loop ────────────────────────────────────────
            ctx = self._dev_qa_loop(ctx, task_id)

            # ── 4. Git: push branch ───────────────────────────────────────────
            self._push_branch(ctx)

            # ── 5. Create PR ──────────────────────────────────────────────────
            ctx = self._create_pr(ctx, task_id)

            # ── 6. Reviewer × 2 (different models via exclude list) ───────────
            diff = self._get_diff(ctx)
            ctx = self._run_reviewers(ctx, diff)

            # ── 7. Human notification (via issue comment + label) ─────────────
            # Done by poller.mark_ready_for_review after pipeline returns

            db.update_task(
                task_id,
                status="ready_for_merge",
                pr_url=ctx.pr_url,
                completed_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
            )
            logger.info(f"[pipeline] done — PR: {ctx.pr_url}")

        except QAFailedError:
            # The "qa_failed" status was already set inside the Dev↔QA loop;
            # do not overwrite it with "error". The poller applies the right label.
            logger.warning(f"[pipeline] QA failed on #{issue.number} (retries exhausted)")
            raise

        except Exception as e:
            db.update_task(task_id, status="error")
            logger.error(f"[pipeline] failed on #{issue.number}: {e}", exc_info=True)
            raise

        finally:
            scorer.flush(ctx, task_id)

        return ctx

    # ── Steps ────────────────────────────────────────────────────────────────

    def _setup_git(self, ctx: PipelineContext):
        from devfactory.github import git_ops

        git_ops.setup_branch(ctx)
        logger.info(f"[pipeline] branch ready: {ctx.branch_name}")

    def _dev_qa_loop(self, ctx: PipelineContext, task_id: int) -> PipelineContext:
        from devfactory.config import settings
        from devfactory.github import git_ops

        max_retries = settings.max_qa_retries

        while True:
            ctx = self.developer.execute(ctx)
            git_ops.commit_changes(ctx, attempt=ctx.qa_attempts + 1)

            ctx = self.qa.execute(ctx)

            if ctx.qa_report and ctx.qa_report.passed:
                logger.info(f"[pipeline] QA passed after {ctx.qa_attempts + 1} attempt(s)")
                return ctx

            ctx.qa_attempts += 1

            if ctx.qa_attempts >= max_retries:
                db.update_task(task_id, status="qa_failed")
                raise QAFailedError(
                    f"QA failed after {max_retries} attempt(s) on issue #{ctx.issue.number}.\n"
                    f"Last report:\n{ctx.qa_report.summary if ctx.qa_report else 'N/A'}"
                )

            logger.warning(f"[pipeline] QA failed — retry {ctx.qa_attempts}/{max_retries}")

    def _push_branch(self, ctx: PipelineContext):
        from devfactory.github import git_ops

        git_ops.push_branch(ctx)

    def _create_pr(self, ctx: PipelineContext, task_id: int) -> PipelineContext:
        from devfactory.github.pr import create_or_update_pr

        pr_url, pr_number = create_or_update_pr(ctx)
        ctx.pr_url = pr_url
        ctx.pr_number = pr_number
        db.update_task(task_id, pr_url=pr_url)
        logger.info(f"[pipeline] PR #{pr_number}: {pr_url}")
        return ctx

    def _get_diff(self, ctx: PipelineContext) -> str:
        from devfactory.github import git_ops

        return git_ops.get_diff(ctx)

    def _run_reviewers(self, ctx: PipelineContext, diff: str) -> PipelineContext:
        ctx.diff = diff

        # First reviewer
        ctx = self.reviewer.execute(ctx)

        # Second reviewer — router will exclude the first model
        return self.reviewer.execute(ctx)
