"""
Analyst Agent — reads a GitHub issue and produces a structured TaskSpec.
"""

from __future__ import annotations

import json
import logging
import re

from devfactory.agents.base import BaseAgent
from devfactory.context import PipelineContext, TaskSpec

logger = logging.getLogger(__name__)


class AnalystAgent(BaseAgent):
    role = "analyst"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        system = self.load_prompt()
        user_content = f"""# GitHub Issue #{ctx.issue.number}: {ctx.issue.title}

**Repository:** {ctx.issue.repo}

## Description
{ctx.issue.body}
"""
        messages = [
            self.system_message(system),
            self.user_message(user_content),
        ]

        response = self.chat(ctx, messages, temperature=0.1, max_tokens=4096)
        ctx.task_spec = self._parse_task_spec(response.content)
        logger.info(
            f"[analyst] TaskSpec created: {len(ctx.task_spec.acceptance_criteria)} criteria"
        )
        return ctx

    def _parse_task_spec(self, raw: str) -> TaskSpec:
        """Extract JSON block from LLM output and validate it."""
        # Extract JSON from markdown code block if present
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        json_str = match.group(1) if match else raw.strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("[analyst] Could not parse JSON, using raw output as summary")
            return TaskSpec(
                summary=raw[:500],
                acceptance_criteria=[],
                files_to_create=[],
                files_to_modify=[],
                test_strategy="",
                tech_notes=raw,
                raw=raw,
            )

        return TaskSpec(
            summary=data.get("summary", ""),
            acceptance_criteria=data.get("acceptance_criteria", []),
            files_to_create=data.get("files_to_create", []),
            files_to_modify=data.get("files_to_modify", []),
            test_strategy=data.get("test_strategy", ""),
            tech_notes=data.get("tech_notes", ""),
            raw=raw,
        )
