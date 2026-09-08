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

# Three attempts: the failure is usually a model wrapping JSON in prose, which a
# corrective turn fixes, and a fourth call would cost more than it recovers.
_MAX_ATTEMPTS = 3

# Generous, because reasoning models spend this budget on their working before they
# write a single character of the answer. At 4096 a run was observed producing 4096
# tokens of reasoning and an empty answer: the whole budget went on thinking, and
# the pipeline saw an empty spec with no idea why.
_MAX_TOKENS = 8192


class AnalystFailedError(RuntimeError):
    """Raised when the analyst cannot produce a spec the pipeline can act on."""


def _unusable_because(spec: TaskSpec) -> str | None:
    """Return why the spec cannot drive a run, or None when it can.

    Deliberately minimal. The analyst is asked for six fields; only two make the
    difference between a plan and a title. Demanding more would reject specs that
    are thin but workable, and the point is to catch the empty ones.
    """
    if not spec.summary.strip():
        return "the summary is empty"
    if not spec.acceptance_criteria:
        return "there are no acceptance criteria"
    return None


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

        # An unusable spec is not a degraded run, it is a broken one: the developer
        # gets nothing but the issue title, and the scope gate — which compares the
        # change against the declared files — silently checks nothing, exactly when
        # it would be most useful. So the analyst is retried, and the run stops
        # rather than proceeding on an empty plan.
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            response = self.chat(ctx, messages, temperature=0.1, max_tokens=_MAX_TOKENS)
            spec = self._parse_task_spec(response.content)
            problem = _unusable_because(spec)

            # A cut-off answer is not a wrong answer. Telling the model its JSON was
            # unusable when it simply never reached the end sends it to fix the one
            # thing that was not broken.
            if problem and (response.truncated or response.thinking_tokens_only):
                problem = (
                    "the answer was cut off before it was finished — the token budget "
                    "went on reasoning"
                )

            if problem is None:
                ctx.task_spec = spec
                # The declared files are a gate input, not a hint: log them so a
                # scope rejection can be read against what was actually asked for.
                declared = spec.files_to_create + spec.files_to_modify
                logger.info(
                    f"[analyst] TaskSpec created: {len(spec.acceptance_criteria)} criteria, "
                    f"{len(declared)} file(s) declared: {', '.join(declared) or 'none'}"
                )
                return ctx

            logger.warning(
                f"[analyst] unusable TaskSpec on attempt {attempt}/{_MAX_ATTEMPTS}: {problem}"
            )
            if attempt < _MAX_ATTEMPTS:
                messages = messages + [
                    {"role": "assistant", "content": response.content},
                    self.user_message(
                        f"That response is unusable: {problem}. Reply with the JSON object "
                        f"only — no prose, no explanation, no reasoning, no markdown "
                        f"outside the code block — and fill every field. Answer "
                        f"immediately; do not think it through first."
                    ),
                ]

        raise AnalystFailedError(
            f"The analyst could not produce a usable TaskSpec for issue "
            f"#{ctx.issue.number} in {_MAX_ATTEMPTS} attempts. Last problem: {problem}."
        )

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
