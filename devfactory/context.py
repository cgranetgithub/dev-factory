"""
PipelineContext — shared state object passed between all agents in a pipeline run.
One instance per issue processed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class GitHubIssue:
    number: int
    title: str
    body: str
    repo: str  # "owner/repo"
    labels: list[str]
    url: str


@dataclass
class TaskSpec:
    """Structured output from the Analyst agent."""

    summary: str
    acceptance_criteria: list[str]
    files_to_create: list[str]
    files_to_modify: list[str]
    test_strategy: str
    tech_notes: str
    raw: str  # original LLM output (for debugging)


@dataclass
class QAReport:
    passed: bool
    ruff: dict  # {"issues": [...], "score": float}
    mypy: dict  # {"errors": [...], "score": float}
    bandit: dict  # {"findings": [...], "severity": str}
    pytest: dict  # {"passed": int, "failed": int, "errors": [...]}
    summary: str  # human-readable summary for agents
    raw_output: str  # full combined output


@dataclass
class ReviewResult:
    model: str
    verdict: str  # "approved" | "changes_requested" | "commented"
    summary: str
    inline_comments: list[dict]  # [{"path": str, "line": int, "body": str}]
    score: float  # 0.0–1.0 quality score assigned by scorer


@dataclass
class PipelineContext:
    issue: GitHubIssue
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))

    # Git / GitHub
    branch_name: str = ""
    commits: list[str] = field(default_factory=list)
    pr_url: str | None = None
    pr_number: int | None = None
    diff: str = ""  # populated by orchestrator after push, injected into reviewer

    # Agent outputs
    task_spec: TaskSpec | None = None
    qa_report: QAReport | None = None
    review_results: list[ReviewResult] = field(default_factory=list)

    # Tracking
    qa_attempts: int = 0
    model_assignments: dict[str, str] = field(default_factory=dict)
    # {"analyst": "qwen2.5-coder:7b", "developer": "deepseek-coder-v2:16b", ...}

    # Execution log (for KB scoring)
    execution_log: list[dict[str, Any]] = field(default_factory=list)

    def log_execution(
        self,
        agent: str,
        model: str,
        duration_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
        metadata: dict | None = None,
    ):
        self.execution_log.append(
            {
                "agent": agent,
                "model": model,
                "duration_ms": duration_ms,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "metadata": metadata or {},
                "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat(),
            }
        )

    @property
    def repo_owner(self) -> str:
        return self.issue.repo.split("/")[0]

    @property
    def repo_name(self) -> str:
        return self.issue.repo.split("/")[1]
