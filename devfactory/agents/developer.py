"""
Developer Agent — generates or modifies code based on TaskSpec.
On retry, receives QA feedback as additional context.
"""

from __future__ import annotations

import logging
import re

from devfactory.agents.base import BaseAgent
from devfactory.config import settings
from devfactory.context import PipelineContext
from devfactory.repo_context import build_context_block

logger = logging.getLogger(__name__)


class DeveloperAgent(BaseAgent):
    role = "developer"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.task_spec is None:
            raise RuntimeError("DeveloperAgent requires a TaskSpec — run AnalystAgent first")

        system = self.load_prompt()
        user_content = self._build_user_prompt(ctx)

        messages = [
            self.system_message(system),
            self.user_message(user_content),
        ]

        response = self.chat(ctx, messages, temperature=0.15, max_tokens=8192)
        self._apply_code_to_workspace(ctx, response.content)
        return ctx

    def _build_user_prompt(self, ctx: PipelineContext) -> str:
        spec = ctx.task_spec
        parts = [
            f"# Task: {ctx.issue.title}",
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

        # Inject repo context (file tree + existing file contents)
        repo_path = settings.workspace / ctx.repo_name
        if repo_path.exists():
            ctx_block = build_context_block(repo_path, spec.files_to_modify)
            parts.append(f"\n{ctx_block}")

        # On retry: include QA feedback
        if ctx.qa_attempts > 0 and ctx.qa_report:
            parts.append(
                f"\n## QA Feedback (attempt {ctx.qa_attempts})\n"
                f"The previous implementation failed QA. Fix the following issues:\n\n"
                f"{ctx.qa_report.summary}"
            )

        parts.append(
            "\n## Instructions\n"
            "Return your implementation as a series of file blocks.\n"
            "Each file must use this exact format:\n\n"
            "```python\n# FILE: path/to/file.py\n<code here>\n```\n\n"
            "Use relative paths from the repository root."
        )

        return "\n".join(parts)

    def _apply_code_to_workspace(self, ctx: PipelineContext, llm_output: str):
        """Parse file blocks from LLM output and write them to the workspace."""
        repo_path = settings.workspace / ctx.repo_name
        if not repo_path.exists():
            logger.warning(f"[developer] workspace path not found: {repo_path} — files not written")
            return

        # Pattern: ```<lang>\n# FILE: <path>\n<content>\n```
        pattern = re.compile(
            r"```[a-zA-Z]*\n# FILE: ([^\n]+)\n(.*?)```",
            re.DOTALL,
        )

        written = []
        for match in pattern.finditer(llm_output):
            rel_path = match.group(1).strip()
            content = match.group(2)

            file_path = repo_path / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            written.append(rel_path)
            logger.debug(f"[developer] wrote {rel_path}")

        if not written:
            logger.warning("[developer] No file blocks found in LLM output")
        else:
            logger.info(f"[developer] wrote {len(written)} file(s): {written}")
