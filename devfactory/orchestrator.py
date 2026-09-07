"""
Orchestrator — runs the sequential agent pipeline for a single GitHub issue.

Flow:
  1. Analyst    → reads issue, produces TaskSpec
  2. Git setup  → clone repo, create feature branch
  3. Dev→Verification loop → developer writes code, ruff autofixes it, verification runs
                   (max N retries)
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


class VerificationFailedError(RuntimeError):
    """Raised when the Dev↔Verification loop has exhausted all its attempts.

    Distinct from a generic error: the poller catches it specifically to apply
    the ``devfactory:verification-failed`` label instead of ``devfactory:error``.
    """


class Pipeline:
    def __init__(self, model_overrides: dict[str, str] | None = None) -> None:
        """
        Args:
            model_overrides: role → model name, pinning that role for the whole run
                instead of letting the router draw at random. Comparing two models
                is impossible while every run rolls the dice, so this is the
                instrument that turns "it felt better" into a measurement.
                Unknown role names and unknown model names raise rather than being
                silently ignored — a comparison run that quietly used a different
                model than requested is worse than no run.
        """
        from devfactory.agents.analyst import AnalystAgent
        from devfactory.agents.developer import DeveloperAgent
        from devfactory.agents.reviewer import ReviewerAgent
        from devfactory.agents.verification import VerificationAgent

        forced = self._resolve_overrides(model_overrides or {})

        self.analyst: AnalystAgent = AnalystAgent(forced.get("analyst"))
        self.developer: DeveloperAgent = DeveloperAgent(forced.get("developer"))
        self.verification: VerificationAgent = VerificationAgent()
        self.reviewer: ReviewerAgent = ReviewerAgent(forced.get("reviewer"))

    @staticmethod
    def _resolve_overrides(overrides: dict[str, str]) -> dict:
        from devfactory.models.registry import ModelMeta, get_model

        known_roles = {"analyst", "developer", "reviewer"}
        resolved: dict[str, ModelMeta] = {}

        for role, model_name in overrides.items():
            if role not in known_roles:
                raise ValueError(f"Unknown role '{role}' — expected one of {sorted(known_roles)}")
            model = get_model(model_name)
            if model is None:
                raise ValueError(f"Model '{model_name}' is not in the registry")
            if role not in model.roles:
                raise ValueError(f"Model '{model_name}' does not declare the '{role}' role")
            resolved[role] = model
            logger.info(f"[pipeline] {role} pinned to {model_name}")

        return resolved

    def run(self, issue: GitHubIssue) -> PipelineContext:
        ctx = PipelineContext(issue=issue)
        task_id = db.create_task(issue.number, issue.repo)
        db.update_task(task_id, status="in_progress")

        logger.info(f"[pipeline] start issue=#{issue.number} '{issue.title}' repo={issue.repo}")

        # Before anything is spent: make the host ready, or say what is wrong with
        # it. A missing model discovered at the developer step has already cost the
        # analyst its run.
        from devfactory.models.provisioning import prepare_host

        prepare_host()

        try:
            # ── 1. Analyst ────────────────────────────────────────────────────
            ctx = self.analyst.execute(ctx)

            # ── 2. Git: clone + create branch ─────────────────────────────────
            self._setup_git(ctx)
            db.update_task(task_id, branch_name=ctx.branch_name)

            # ── 3. Developer → verification → review loop ─────────────────────
            ctx = self._build_loop(ctx, task_id)

            # ── 4. Git: push branch ───────────────────────────────────────────
            self._push_branch(ctx)

            # ── 5. Create PR ──────────────────────────────────────────────────
            ctx = self._create_pr(ctx, task_id)

            # ── 6. Publish the review that governed the accepted iteration ────
            self._publish_review(ctx)

            # ── 7. Human notification (via issue comment + label) ─────────────
            # Done by poller.mark_ready_for_review after pipeline returns

            db.update_task(
                task_id,
                status="ready_for_merge",
                pr_url=ctx.pr_url,
                completed_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
            )
            logger.info(f"[pipeline] done — PR: {ctx.pr_url}")

        except VerificationFailedError:
            # The "verification_failed" status was already set inside the Dev↔Verification loop;
            # do not overwrite it with "error". The poller applies the right label.
            logger.warning(f"[pipeline] Verification failed on #{issue.number} (retries exhausted)")
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

    def _build_loop(self, ctx: PipelineContext, task_id: int) -> PipelineContext:
        """Developer → verification → review, until both gates are satisfied.

        Two gates, in this order on purpose. Verification is deterministic and takes
        a couple of minutes; the review costs a model call. Sending code that does not
        even pass its own tests to a reviewer spends the expensive resource on what the
        cheap one already found — and the reviewer then wastes its judgement on
        mechanics instead of on whether the change actually does what the issue asked.

        Both gates draw on one shared budget of developer iterations, so a change
        cannot ping-pong between them indefinitely.
        """
        from devfactory.config import settings
        from devfactory.github import git_ops
        from devfactory.github.git_ops import _workspace_path
        from devfactory.verification.autofix import autofix
        from devfactory.verification.scope import check_scope

        max_retries = settings.max_verification_retries

        while True:
            ctx = self.developer.execute(ctx)

            # Clear the mechanical lint failures before the gate sees them, so the
            # retry budget is spent on real defects rather than on line length. The
            # return value is what the developer left behind, kept for scoring.
            ctx.lint_left_behind.append(
                autofix(_workspace_path(ctx), git_ops.changed_python_files(ctx))
            )

            git_ops.commit_changes(ctx, attempt=ctx.iterations_used + 1)

            # ── Gate 0: scope (a set comparison) ──────────────────────────────
            # First because it is nearly free. The container costs a minute or two
            # and the reviewer costs a model call; neither should queue behind a
            # question answered by comparing two lists of paths.
            spec = ctx.task_spec
            declared = (spec.files_to_create + spec.files_to_modify) if spec else []
            ctx.scope_report = check_scope(declared, git_ops.files_changed_on_branch(ctx))

            if not ctx.scope_report.satisfied:
                ctx.scope_rejections += 1
                logger.warning(
                    f"[pipeline] Declared files untouched: "
                    f"{', '.join(ctx.scope_report.missing)} — "
                    f"iteration {ctx.iterations_used}/{max_retries}"
                )
                if ctx.iterations_used >= max_retries:
                    db.update_task(task_id, status="verification_failed")
                    raise VerificationFailedError(
                        f"After {max_retries} attempt(s) on issue #{ctx.issue.number}, "
                        f"the change still does not touch the files the task declared:\n"
                        f"{ctx.scope_report.summary()}"
                    )
                continue

            if ctx.scope_report.unexpected:
                # Not blocking: an analyst cannot foresee every file. Recorded so a
                # model that edits unrelated files is visible in the run.
                logger.warning(
                    f"[pipeline] Files changed outside the task: "
                    f"{', '.join(ctx.scope_report.unexpected)}"
                )

            # ── Gate 1: verification (deterministic) ──────────────────────────
            ctx = self.verification.execute(ctx)
            report = ctx.verification_report

            if not (report and report.passed):
                ctx.verification_attempts += 1
                if ctx.iterations_used >= max_retries:
                    db.update_task(task_id, status="verification_failed")
                    raise VerificationFailedError(
                        f"Verification failed after {max_retries} attempt(s) "
                        f"on issue #{ctx.issue.number}.\n"
                        f"Last report:\n{report.summary if report else 'N/A'}"
                    )
                logger.warning(
                    f"[pipeline] Verification failed — "
                    f"iteration {ctx.iterations_used}/{max_retries}"
                )
                continue

            logger.info(f"[pipeline] Verification passed on iteration {ctx.iterations_used + 1}")

            # ── Gate 2: review (judgement) ────────────────────────────────────
            ctx.diff = self._get_diff(ctx)
            ctx = self.reviewer.execute(ctx)
            verdict = ctx.review_results[-1].verdict if ctx.review_results else "commented"

            if verdict != "changes_requested":
                logger.info(f"[pipeline] Review verdict={verdict} — proceeding to PR")
                return ctx

            ctx.review_rejections += 1

            if ctx.iterations_used >= max_retries:
                # The code passes verification; only the reviewer is unsatisfied.
                # Blocking here would produce nothing at all, so the change goes to
                # the PR with the unresolved review attached and the human decides.
                # Recorded loudly rather than dropped: a gate that was not satisfied
                # must remain visible in the evidence.
                logger.warning(
                    f"[pipeline] Review still requests changes after {max_retries} "
                    f"iteration(s) — opening the PR with the review unresolved"
                )
                ctx.review_unresolved = True
                return ctx

            logger.warning(
                f"[pipeline] Review requested changes — "
                f"iteration {ctx.iterations_used}/{max_retries}"
            )

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

    def _publish_review(self, ctx: PipelineContext):
        """Post the review that governed the accepted iteration onto the PR.

        The review already did its work inside the loop — it decided whether the
        change could proceed. Publishing it here makes that decision visible at the
        point where the human approver acts, instead of leaving it in a log. What is
        published is exactly what drove the decision, not a fresh opinion written
        afterwards about code that was already accepted.
        """
        if not ctx.review_results or ctx.pr_number is None:
            return

        from devfactory.github.review import post_review

        post_review(ctx, ctx.review_results[-1])
