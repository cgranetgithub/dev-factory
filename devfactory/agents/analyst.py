"""
Analyst Agent — turns a request into a specification a developer can implement.

It reads the request *and the codebase*, through the harness in read-only mode,
and publishes what it decides as a GitHub issue linked to the original. A request
often names a symptom; the specification has to name the file, the function and
the correct behaviour, and only reading the code can supply those.
"""

from __future__ import annotations

import json
import logging
import re

from devfactory import opencode
from devfactory.agents.base import BaseAgent
from devfactory.config import settings
from devfactory.context import PipelineContext, TaskSpec
from devfactory.github.spec_issue import publish_spec

logger = logging.getLogger(__name__)

# Three attempts: the failure is usually a model wrapping JSON in prose, which a
# corrective turn fixes, and a fourth call would cost more than it recovers.
_MAX_ATTEMPTS = 3


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
        """Read the codebase, write a specification, publish it as an issue.

        The specification is not handed to the next stage. It is published where
        anyone — the developer, the reviewer, a human — can read it.
        """
        repo_path = settings.workspace / ctx.repo_name
        prompt = self._build_prompt(ctx)

        # An unusable spec is not a degraded run, it is a broken one: the developer
        # gets nothing but the issue title, and the scope gate — which compares the
        # change against the declared files — silently checks nothing, exactly when
        # it would be most useful. So the analyst is retried, and the run stops
        # rather than proceeding on an empty plan.
        problem: str | None = "the analyst produced nothing"
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            result = opencode.run(
                prompt,
                repo_path=repo_path,
                model_name=self.model.name,
                read_only=True,
                role=self.role,
            )
            ctx.log_execution(
                agent=self.role,
                model=self.model.name,
                duration_ms=result.duration_ms,
                prompt_tokens=0,
                completion_tokens=0,
            )

            spec = self._parse_task_spec(result.output)
            problem = _unusable_because(spec)

            if problem is None:
                ctx.task_spec = spec
                declared = spec.files_to_create + spec.files_to_modify
                logger.info(
                    f"[analyst] spec written: {len(spec.acceptance_criteria)} criteria, "
                    f"{len(declared)} file(s) declared: {', '.join(declared) or 'none'}"
                )
                ctx.spec_issue_number = publish_spec(
                    ctx.issue.repo, ctx.issue.number, ctx.issue.title, spec
                )
                return ctx

            logger.warning(
                f"[analyst] unusable spec on attempt {attempt}/{_MAX_ATTEMPTS}: {problem}"
            )
            prompt = (
                f"{self._build_prompt(ctx)}\n\n"
                f"## A previous attempt failed\n"
                f"It was unusable: {problem}. Read the code, then reply with the JSON "
                f"object only — no prose, no explanation, no markdown outside the code "
                f"block — and fill every field."
            )

        raise AnalystFailedError(
            f"The analyst could not produce a usable specification for issue "
            f"#{ctx.issue.number} in {_MAX_ATTEMPTS} attempts. Last problem: {problem}."
        )

    def _build_prompt(self, ctx: PipelineContext) -> str:
        """The request, and the instruction to go and read before answering."""
        return (
            f"{self.load_prompt()}\n\n"
            f"# Request: issue #{ctx.issue.number} — {ctx.issue.title}\n\n"
            f"## What was asked\n{ctx.issue.body or '(no description given)'}\n\n"
            f"## Your job\n"
            f"You are in the repository this request is about. **Read the code before "
            f"you answer.** Find the files that are actually involved, and name those — "
            f"not the ones the request happens to mention, and never ones you have not "
            f"opened.\n\n"
            f"The request may be vague, or wrong about where the problem is. Your value "
            f"is turning it into something a developer can implement without guessing.\n\n"
            f"Do not modify anything. Reply with the JSON object only."
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
