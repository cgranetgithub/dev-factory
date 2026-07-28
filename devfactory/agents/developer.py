"""
Developer Agent — generates or modifies code based on TaskSpec.
On retry, receives QA feedback as additional context.

Two backends, selected by ``settings.dev_backend``:
  * "ollama"   — a single LLM call whose response is a set of full-file blocks
                 that overwrite files in the workspace (self-contained default).
  * "opencode" — delegate to the OpenCode CLI: an agentic tool loop that reads
                 and edits files in place against a local Ollama model. This
                 avoids the brittle full-file rewrite (no accidental deletion of
                 untouched code) and the single-call timeout of the "ollama" path.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time

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

        # Dispatch on the configured backend. Both mutate files in the workspace
        # repo; the rest of the pipeline (commit → QA → …) is backend-agnostic.
        if settings.dev_backend == "opencode":
            return self._run_opencode(ctx)
        return self._run_ollama(ctx)

    # ── "ollama" backend — single-shot, full-file rewrite ──────────────────────

    def _run_ollama(self, ctx: PipelineContext) -> PipelineContext:
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
        # run() guarantees the spec is set before we get here; assert narrows the
        # Optional for the type checker.
        assert spec is not None
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

    # ── "opencode" backend — agentic CLI, in-place edits ───────────────────────

    def _run_opencode(self, ctx: PipelineContext) -> PipelineContext:
        """
        Delegate implementation to the OpenCode CLI.

        OpenCode runs its own agentic loop (read/search/edit/run) against the
        selected Ollama model, editing files directly in the workspace repo.
        We only build the task prompt, invoke the CLI, and record the execution.
        """
        repo_path = settings.workspace / ctx.repo_name
        if not repo_path.exists():
            raise RuntimeError(f"workspace path not found: {repo_path}")

        prompt = self._build_opencode_prompt(ctx)
        # OpenCode addresses models as "provider/model"; our provider id is "ollama"
        # (see ~/.config/opencode/opencode.json). self.model is the router's pick.
        model_ref = f"ollama/{self.model.name}"

        cmd = [
            settings.opencode_bin,
            "run",
            "--auto",  # auto-approve edits/commands — non-interactive
            "--dir",
            str(repo_path),
            "-m",
            model_ref,
            "--print-logs",
            "--log-level",
            "INFO",
            prompt,
        ]
        logger.info(f"[developer] opencode backend → {model_ref} in {repo_path}")

        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=settings.opencode_timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"opencode run timed out after {settings.opencode_timeout_s}s"
            ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"opencode binary not found at {settings.opencode_bin} — "
                "install it or set OPENCODE_BIN"
            ) from exc

        duration_ms = int((time.monotonic() - start) * 1000)

        if result.returncode != 0:
            # Surface the tail of stderr so the failure is diagnosable in the logs.
            logger.error(
                f"[developer] opencode exited {result.returncode}: {result.stderr[-2000:]}"
            )
            raise RuntimeError(f"opencode run failed (exit {result.returncode})")

        logger.info(f"[developer] opencode run complete in {duration_ms}ms")

        # Record the execution in the KB. OpenCode does not report token counts
        # through this interface, so they are logged as 0 — developer scoring is
        # derived from the QA outcome, not token usage.
        ctx.log_execution(
            agent=self.role,
            model=self.model.name,
            duration_ms=duration_ms,
            prompt_tokens=0,
            completion_tokens=0,
        )
        return ctx

    def _build_opencode_prompt(self, ctx: PipelineContext) -> str:
        """Build the task prompt for OpenCode (no file-block format — it edits itself)."""
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

        # On retry: include QA feedback so OpenCode fixes the reported issues.
        if ctx.qa_attempts > 0 and ctx.qa_report:
            parts.append(
                f"\n## QA Feedback (attempt {ctx.qa_attempts})\n"
                f"The previous implementation failed QA. Fix the following issues:\n\n"
                f"{ctx.qa_report.summary}"
            )

        return "\n".join(parts)
