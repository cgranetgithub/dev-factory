"""
Developer Agent — edits the workspace repository to implement the task.

It works through the harness (:mod:`devfactory.opencode`), which drives an agentic
tool loop: the model reads files, edits them in place and runs commands. There used
to be a second path — one call returning whole files that overwrote the workspace —
kept because the agentic loop appeared not to work. That appearance was a
measurement error (the context window was 4096), and a single-shot rewrite deletes
code it was never asked to touch.

On a send-back, the prompt carries the feedback from whichever gate refused.
"""

from __future__ import annotations

import logging

from devfactory import opencode
from devfactory.agents.base import BaseAgent
from devfactory.config import settings
from devfactory.context import PipelineContext

logger = logging.getLogger(__name__)


class DeveloperAgent(BaseAgent):
    role = "developer"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        """Implement the task by editing the workspace repository.

        The harness runs the agentic loop (read/search/edit/run) against the
        selected model, editing files in place. This builds the task prompt,
        invokes the harness, and records the execution.
        """
        result = opencode.run(
            self._build_prompt(ctx),
            repo_path=settings.workspace / ctx.repo_name,
            model_name=self.model.name,
            read_only=False,
            role=self.role,
        )

        # Record the execution in the KB. OpenCode does not report token counts
        # through this interface, so they are logged as 0 — developer scoring is
        # derived from the verification outcome, not token usage.
        ctx.log_execution(
            agent=self.role,
            model=self.model.name,
            duration_ms=result.duration_ms,
            prompt_tokens=0,
            completion_tokens=0,
        )
        return ctx

    def _build_prompt(self, ctx: PipelineContext) -> str:
        """The task, the specification, and the feedback from whichever gate refused."""
        spec = ctx.task_spec
        assert spec is not None

        parts = [
            self.load_prompt("developer_opencode.md"),
            f"\n# Task: {ctx.issue.title}",
            f"\n## Summary\n{spec.summary}",
            "\n## Acceptance Criteria\n" + "\n".join(f"- {c}" for c in spec.acceptance_criteria),
        ]

        if spec.files_to_create:
            parts.append(
                "\n## Files to Create\n" + "\n".join(f"- `{f}`" for f in spec.files_to_create)
            )
        if spec.files_to_modify:
            parts.append(
                "\n## Files to Modify\n" + "\n".join(f"- `{f}`" for f in spec.files_to_modify)
            )

        parts.append(f"\n## Test Strategy\n{spec.test_strategy}")
        parts.append(f"\n## Technical Notes\n{spec.tech_notes}")

        # On retry: include verification feedback so OpenCode fixes the reported issues.
        if ctx.verification_attempts > 0 and ctx.verification_report:
            parts.append(
                f"\n## Verification Feedback (attempt {ctx.verification_attempts})\n"
                f"The previous implementation failed verification. Fix the following issues:\n\n"
                f"{ctx.verification_report.summary}"
            )

        # On a scope rejection: the change did not reach the files the task named.
        # Shown before the other feedback because it is the most concrete thing the
        # developer can act on — a named file it has not opened.
        if ctx.scope_rejections > 0 and ctx.scope_report is not None:
            parts.append(
                f"\n## Scope Feedback (rejection {ctx.scope_rejections})\n"
                f"{ctx.scope_report.summary()}"
            )
        # On rejection: the reviewer read the change and sent it back. Its comments
        # are about intent and design, not mechanics — verification already covers
        # those — so they are shown separately rather than merged into one list.
        if ctx.review_rejections > 0 and ctx.review_results:
            review = ctx.review_results[-1]
            comments = "\n".join(
                f"- {c.get('path', '?')}:{c.get('line', '?')} — {c.get('body', '')}"
                for c in review.inline_comments
            )
            parts.append(
                f"\n## Review Feedback (rejection {ctx.review_rejections})\n"
                f"A reviewer read the change and requested modifications.\n\n"
                f"{review.summary}\n\n{comments}"
            )

        return "\n".join(parts)
