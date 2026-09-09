"""
Orchestrator — runs the sequential agent pipeline for a single GitHub issue.

Flow:
  1. Git setup  → clone repo, create feature branch
  2. Analyst    → reads the request AND the codebase, publishes a spec issue
  3. Graph      → developer, then three gates (scope, verification, review); any
                   gate can send the change back, on one shared budget
  4. Git push   → push feature branch to remote
  5. PR         → create GitHub PR, citing the spec issue
  6. Review     → post the review that governed the accepted iteration
  7. Labels     → the pipeline marks the issue's outcome itself
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from github import GithubException
from langgraph.checkpoint.sqlite import SqliteSaver

from devfactory import graph
from devfactory.context import GitHubIssue, PipelineContext
from devfactory.github import issues
from devfactory.kb.database import db
from devfactory.kb.scorer import scorer
from devfactory.verification.scope import ScopeReport

logger = logging.getLogger(__name__)


class VerificationFailedError(RuntimeError):
    """Raised when the Dev↔Verification loop has exhausted all its attempts.

    Distinct from a generic error: the poller catches it specifically to apply
    the ``devfactory:verification-failed`` label instead of ``devfactory:error``.
    """


class Pipeline:
    def __init__(
        self,
        model_overrides: dict[str, str] | None = None,
        resume_thread: str | None = None,
    ) -> None:
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
        # Set to continue a run that was interrupted: the graph picks up from its
        # last checkpoint instead of paying for the developer again.
        self._thread_id = resume_thread

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

        # The pipeline owns the issue's status labels. They used to live in the
        # poller, so a run started from the CLI left the issue labelled
        # ready-for-dev — and the poller would pick it up again. One owner, one
        # behaviour, whatever started the run.
        self._mark(issues.mark_in_progress, issue.repo, issue.number)

        # Before anything is spent: make the host ready, or say what is wrong with
        # it. A missing model discovered at the developer step has already cost the
        # analyst its run.
        from devfactory.models.provisioning import prepare_host

        prepare_host()

        try:
            # ── 1. Git: clone + create branch ─────────────────────────────────
            # Before the analyst, not after: it reads the codebase to write the
            # specification, so it needs a checkout. The branch is empty at this
            # point, so what it reads is the base branch.
            self._setup_git(ctx)
            db.update_task(task_id, branch_name=ctx.branch_name)

            # ── 2. Analyst ────────────────────────────────────────────────────
            ctx = self.analyst.execute(ctx)

            # ── 3. Developer → verification → review loop ─────────────────────
            ctx = self._build_loop(ctx, task_id)

            # ── 4. Git: push branch ───────────────────────────────────────────
            self._push_branch(ctx)

            # ── 5. Create PR ──────────────────────────────────────────────────
            ctx = self._create_pr(ctx, task_id)

            # ── 6. Publish the review that governed the accepted iteration ────
            self._publish_review(ctx)

            db.update_task(
                task_id,
                status="ready_for_merge",
                pr_url=ctx.pr_url,
                completed_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
            )
            logger.info(f"[pipeline] done — PR: {ctx.pr_url}")
            if ctx.pr_url:
                self._mark(issues.mark_ready_for_review, issue.repo, issue.number, ctx.pr_url)

        except VerificationFailedError as e:
            # The "verification_failed" status was already set inside the Dev↔Verification loop;
            # do not overwrite it with "error". The poller applies the right label.
            logger.warning(f"[pipeline] Verification failed on #{issue.number} (retries exhausted)")
            self._mark(issues.mark_verification_failed, issue.repo, issue.number, str(e))
            raise

        except Exception as e:
            db.update_task(task_id, status="error")
            logger.error(f"[pipeline] failed on #{issue.number}: {e}", exc_info=True)
            self._mark(issues.mark_error, issue.repo, issue.number, str(e))
            raise

        finally:
            scorer.flush(ctx, task_id)

        return ctx

    @staticmethod
    def _mark(fn, *args) -> None:
        """Apply a status label, and never let GitHub being unreachable end a run.

        The labels are how a human sees where an issue stands, but they are not the
        work. Losing one is worth a warning; losing the run that produced a pull
        request because a label call timed out is not.
        """
        try:
            fn(*args)
        except (GithubException, OSError) as e:
            logger.warning(f"[pipeline] could not update the issue's labels ({e})")

    # ── Steps ────────────────────────────────────────────────────────────────

    def _setup_git(self, ctx: PipelineContext):
        from devfactory.github import git_ops

        git_ops.setup_branch(ctx)
        logger.info(f"[pipeline] branch ready: {ctx.branch_name}")

    def _build_loop(self, ctx: PipelineContext, task_id: int) -> PipelineContext:
        """Run the developer → gates graph until every gate is satisfied.

        The graph owns the flow (see :mod:`devfactory.graph`); this holds the
        context the nodes work on.
        """
        from devfactory.config import settings

        self._ctx = ctx
        self._task_id = task_id
        self._max_iterations = settings.max_verification_retries

        compiled = graph.build(self, self._max_iterations).compile(
            checkpointer=self._checkpointer()
        )
        # A fresh thread per run, not per issue. Reusing the issue number would
        # make a deliberate re-run silently resume a half-finished one, which is a
        # surprising way to lose an hour. Resuming is opt-in: the id is logged, and
        # `devfactory run --resume <id>` continues that exact run.
        thread_id = self._thread_id or f"issue-{ctx.issue.number}-{ctx.started_at:%Y%m%d-%H%M%S}"
        logger.info(f"[pipeline] graph thread {thread_id} (resume with --resume {thread_id})")
        config = {"configurable": {"thread_id": thread_id}}
        compiled.invoke(graph.initial_state(), config)
        return self._ctx

    @staticmethod
    def _checkpointer():
        """Persist the graph state beside the knowledge base.

        A developer step has taken sixteen minutes; a run that dies after it should
        not pay for it again. The checkpoint holds orchestration state only, so
        resuming re-runs a node and lets it re-read the world.
        """
        from devfactory.config import settings

        path = settings.db_path.parent / "checkpoints.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        return SqliteSaver(conn)

    # ── Graph nodes ──────────────────────────────────────────────────────────
    # Each runs one stage and reports whether the change may proceed. Routing
    # lives in devfactory.graph, so the flow can be read in one place.
    #
    # The graph state is the truth for the counters — it is what gets checkpointed.
    # The context mirrors them after every change, because the developer's prompt
    # reads the counters to know which feedback to show, the scorer records them,
    # and the pull request body cites them. A mirror that was only refreshed at
    # the end left the developer retrying with no feedback at all.

    def _refused(self, state: graph.LoopState, counter: str) -> graph.LoopState:
        """The gate that just ran sent the change back: count it, mirror it."""
        new_state = dict(state)
        new_state[counter] = state[counter] + 1  # type: ignore[literal-required]
        new_state["last_gate_passed"] = False
        self._mirror(new_state)  # type: ignore[arg-type]
        return new_state  # type: ignore[return-value]

    @staticmethod
    def _passed(state: graph.LoopState) -> graph.LoopState:
        return {**state, "last_gate_passed": True}

    def _mirror(self, state: graph.LoopState) -> None:
        self._ctx.verification_attempts = state["verification_attempts"]
        self._ctx.review_rejections = state["review_rejections"]
        self._ctx.scope_rejections = state["scope_rejections"]

    def node_developer(self, state: graph.LoopState) -> graph.LoopState:
        from devfactory.github import git_ops
        from devfactory.github.git_ops import workspace_path
        from devfactory.verification.autofix import autofix

        self._ctx = self.developer.execute(self._ctx)

        # Did this iteration produce anything at all? Checked before staging, so it
        # answers for this iteration rather than for the branch.
        if not git_ops.working_tree_has_changes(self._ctx):
            self._ctx.scope_report = ScopeReport.nothing_produced()
            logger.warning("[pipeline] Developer produced no changes")
            return self._refused(state, "scope_rejections")

        # Clear the mechanical lint failures before the gates see them, so the
        # budget is spent on real defects rather than on line length.
        self._ctx.lint_left_behind.append(
            autofix(workspace_path(self._ctx), git_ops.changed_python_files(self._ctx))
        )
        git_ops.commit_changes(self._ctx, attempt=graph.iterations_used(state) + 1)
        return self._passed(state)

    def node_scope(self, state: graph.LoopState) -> graph.LoopState:
        from devfactory.github import git_ops
        from devfactory.verification.scope import check_scope

        # The developer node already refused an empty change; nothing more to say.
        if not state["last_gate_passed"]:
            return state

        spec = self._ctx.task_spec
        declared = (spec.files_to_create + spec.files_to_modify) if spec else []
        report = check_scope(declared, git_ops.files_changed_on_branch(self._ctx))
        self._ctx.scope_report = report

        if report.unexpected:
            # Not blocking: an analyst cannot foresee every file. Recorded so a
            # model that edits unrelated files stays visible.
            logger.warning(
                f"[pipeline] Files changed outside the task: {', '.join(report.unexpected)}"
            )

        if report.satisfied:
            return self._passed(state)

        logger.warning(f"[pipeline] Declared files untouched: {', '.join(report.missing)}")
        return self._refused(state, "scope_rejections")

    def node_verification(self, state: graph.LoopState) -> graph.LoopState:
        self._ctx = self.verification.execute(self._ctx)
        report = self._ctx.verification_report

        if report and report.passed:
            logger.info("[pipeline] Verification passed")
            return self._passed(state)

        return self._refused(state, "verification_attempts")

    def node_review(self, state: graph.LoopState) -> graph.LoopState:
        self._ctx.diff = self._get_diff(self._ctx)
        self._ctx = self.reviewer.execute(self._ctx)
        verdict = self._ctx.review_results[-1].verdict if self._ctx.review_results else "commented"

        # Only changes_requested sends the change back; a suggestion is not a block.
        if verdict != "changes_requested":
            logger.info(f"[pipeline] Review verdict={verdict} — proceeding to PR")
            return self._passed(state)

        return self._refused(state, "review_rejections")

    def on_budget_exhausted(self, state: graph.LoopState) -> None:
        """Called when a gate refused for the last time.

        A verification failure ends the run: the code does not work. An
        unconvinced reviewer does not, because the code *does* work and blocking
        would produce nothing at all — the pull request opens with the gate
        recorded as unsatisfied, and a human arbitrates.
        """
        self._mirror(state)

        report = self._ctx.verification_report
        if report is not None and not report.passed:
            db.update_task(self._task_id, status="verification_failed")
            raise VerificationFailedError(
                f"Verification failed after {self._max_iterations} attempt(s) "
                f"on issue #{self._ctx.issue.number}.\n"
                f"Last report:\n{report.summary}"
            )

        scope = self._ctx.scope_report
        if scope is not None and not scope.satisfied:
            db.update_task(self._task_id, status="verification_failed")
            raise VerificationFailedError(
                f"After {self._max_iterations} attempt(s) on issue "
                f"#{self._ctx.issue.number}, the change still does not cover what "
                f"the task declared:\n{scope.summary()}"
            )

        logger.warning(
            f"[pipeline] Review still requests changes after {self._max_iterations} "
            f"iteration(s) — opening the PR with the review unresolved"
        )
        self._ctx.review_unresolved = True

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
