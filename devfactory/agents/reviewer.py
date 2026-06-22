"""
Reviewer Agent — reads the PR diff + QA report and posts an inline GitHub review.
Can be run twice with different models (router excludes already-used models).
"""

from __future__ import annotations

import json
import logging
import re

from devfactory.agents.base import BaseAgent
from devfactory.context import PipelineContext, ReviewResult

logger = logging.getLogger(__name__)


class ReviewerAgent(BaseAgent):
    role = "reviewer"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.pr_number is None:
            logger.warning("[reviewer] No PR yet — skipping GitHub review posting")

        system = self.load_prompt()
        user_content = self._build_user_prompt(ctx)

        messages = [
            self.system_message(system),
            self.user_message(user_content),
        ]

        response = self.chat(ctx, messages, temperature=0.1, max_tokens=6144)
        result = self._parse_review(response.content)
        ctx.review_results.append(result)

        # Post to GitHub if PR exists
        if ctx.pr_number:
            self._post_github_review(ctx, result)

        logger.info(
            f"[reviewer] verdict={result.verdict} inline_comments={len(result.inline_comments)}"
        )
        return ctx

    def _build_user_prompt(self, ctx: PipelineContext) -> str:
        parts = [f"# Code Review: {ctx.issue.title}\n"]

        if ctx.qa_report:
            parts.append(f"## QA Report\n{ctx.qa_report.summary}\n")

        diff = ctx.diff or "[diff not available]"
        parts.append(f"## Diff\n```diff\n{diff}\n```\n")

        parts.append(
            "## Instructions\n"
            "Review the code diff above. Return a JSON object with:\n"
            "- `verdict`: 'approved' | 'changes_requested' | 'commented'\n"
            "- `summary`: overall review summary (1-3 sentences)\n"
            "- `score`: float 0.0-1.0 (code quality estimate)\n"
            "- `inline_comments`: list of {path, line, body} for specific issues\n\n"
            "Return ONLY the JSON, no extra text."
        )

        return "\n".join(parts)

    def _parse_review(self, raw: str) -> ReviewResult:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        json_str = match.group(1) if match else raw.strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
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

    def _post_github_review(self, ctx: PipelineContext, result: ReviewResult):
        try:
            from devfactory.github.review import post_review

            post_review(ctx, result)
        except ImportError:
            logger.warning("[reviewer] GitHub review module not yet available — skipping")
