"""
Reviewer Agent — reads the change *and the code around it*, then gives a verdict.

It used to see a diff and nothing else, and it approved a real defect three times:
a fallback wrapped around an exception that could not reach it, a module wired into
nothing, and a truncation that silently dropped the summary it existed to preserve.
Each of those was visible only one file outside the diff.

So it runs in the repository now, read-only, and can go and look.
"""

from __future__ import annotations

import json
import logging
import re

from devfactory import opencode
from devfactory.agents.base import BaseAgent
from devfactory.config import settings
from devfactory.context import PipelineContext, ReviewResult
from devfactory.github import git_ops, spec_issue

logger = logging.getLogger(__name__)


class ReviewerAgent(BaseAgent):
    role = "reviewer"
    # An agent reviewing its own work is not a review. Four models drive the
    # agentic loop, so keeping the reviewer off the developer's model costs
    # nothing and preserves the separation of duties documented in docs/VISION.md.
    avoid_models_from_roles = ["developer"]

    def run(self, ctx: PipelineContext) -> PipelineContext:
        repo_path = settings.workspace / ctx.repo_name
        result_output = opencode.run(
            self._build_prompt(ctx),
            repo_path=repo_path,
            model_name=self.model.name,
            read_only=True,
            role=self.role,
        )
        ctx.log_execution(
            agent=self.role,
            model=self.model.name,
            duration_ms=result_output.duration_ms,
            prompt_tokens=0,
            completion_tokens=0,
        )

        # A reviewer that edited the code it is judging has stopped being a
        # reviewer. OpenCode's plan agent forbids it; this checks rather than
        # trusts, because the check is one call and the failure would be silent.
        if git_ops.working_tree_has_changes(ctx):
            raise RuntimeError(
                "the reviewer modified the working tree — a review must not change "
                "the code it is judging"
            )

        # The verdict is recorded here and acted on by the graph. It reaches GitHub
        # later, once the pull request exists — see Pipeline._publish_review.
        result = self._parse_review(result_output.output)
        ctx.review_results.append(result)

        logger.info(
            f"[reviewer] verdict={result.verdict} inline_comments={len(result.inline_comments)}"
        )
        return ctx

    def _build_prompt(self, ctx: PipelineContext) -> str:
        parts = [
            self.load_prompt(),
            f"\n# Code review: {ctx.issue.title}\n",
            "You are in the repository, on the branch that carries this change. "
            "Read whatever you need — the change is the diff below, but the question "
            "is whether it is *correct in this codebase*, and the answer is rarely "
            "inside the diff.\n",
        ]

        parts.append(f"## The request\nIssue #{ctx.issue.number}: {ctx.issue.body or ''}\n")
        parts.append(
            f"The specification is issue #{ctx.spec_issue_number}, and the "
            f"acceptance criteria below come from it.\n"
        )

        # The acceptance criteria are the point of the review. Verification already
        # decided that the code is well-formed and its tests pass; what no automated
        # check can answer is whether the change actually does what was asked.
        spec = spec_issue.spec_for(ctx)
        parts.append(f"## What was asked\n{spec.summary}\n")
        if spec.acceptance_criteria:
            criteria = "\n".join(f"- {c}" for c in spec.acceptance_criteria)
            parts.append(f"## Acceptance criteria\n{criteria}\n")

        if ctx.verification_report:
            parts.append(f"## Verification Report\n{ctx.verification_report.summary}\n")

        diff = ctx.diff or "[diff not available]"
        parts.append(f"## Diff\n```diff\n{diff}\n```\n")

        parts.append(
            "## Instructions\n"
            "The change has already passed lint, type checking, security scanning and "
            "its tests — do not spend the review on formatting or style. Judge whether "
            "it satisfies the acceptance criteria, whether the design is sound, and "
            "whether it breaks anything.\n\n"
            "**Open the files the change calls into, not only the ones it edits.** "
            "Three defects have been approved by reviewers that read only a diff: a "
            "fallback wrapped around an exception raised somewhere it could never "
            "catch, a new module that nothing called, and a helper that silently "
            "dropped the data it existed to preserve. Each was invisible in the diff "
            "and obvious one file away. Check that new code is actually reached, and "
            "that what it replaces did not do something it no longer does.\n\n"
            "Do not modify anything. You are reading.\n\n"
            "Use `changes_requested` only for a defect that must be fixed before merge: "
            "an unmet acceptance criterion, a bug, or a harmful side effect. A "
            "suggestion or a preference is `commented`.\n\n"
            "Return a JSON object with:\n"
            "- `verdict`: 'approved' | 'changes_requested' | 'commented'\n"
            "- `summary`: overall review summary (1-3 sentences)\n"
            "- `score`: float 0.0-1.0 (code quality estimate)\n"
            "- `inline_comments`: list of {path, line, body} for specific issues\n\n"
            "Return ONLY the JSON, no extra text."
        )

        return "\n".join(parts)

    def _parse_review(self, raw: str) -> ReviewResult:
        data = _verdict_object(raw)
        if data is None:
            logger.warning("[reviewer] Could not parse JSON review")
            return ReviewResult(
                model=self.model.name,
                verdict="commented",
                summary=raw[:300],
                inline_comments=[],
                score=0.5,
            )

        return ReviewResult(
            model=self.model.name,
            verdict=data.get("verdict", "commented"),
            summary=data.get("summary", ""),
            inline_comments=data.get("inline_comments", []),
            score=float(data.get("score", 0.5)),
        )


def _verdict_object(raw: str) -> dict | None:
    """The JSON object in a reviewer's answer, however the model wrapped it.

    Three shapes have come back from the reviewer models: a fenced block, the
    bare object, and — measured on 2026-09-10 for #101 — a sentence of preamble
    followed by the object. Only the first two used to parse. The third fell
    through to the "could not parse" path, which returns ``commented``: a real
    ``changes_requested`` was recorded as a non-refusal, in the very gate whose
    refusals are being counted. ``glm-4.7-flash:latest`` did exactly that on one
    of two trials against a violated acceptance criterion.

    Returns None when nothing in ``raw`` parses as a JSON object, which is a
    genuinely unusable answer rather than an awkwardly wrapped one.
    """
    candidates = []

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))

    candidates.append(raw.strip())

    # Last resort: the span from the first brace to the last. It is what recovers
    # the preamble case, and it is tried last because it is the loosest — a model
    # that returned a clean object is never read through this.
    opening, closing = raw.find("{"), raw.rfind("}")
    if 0 <= opening < closing:
        candidates.append(raw[opening : closing + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None
