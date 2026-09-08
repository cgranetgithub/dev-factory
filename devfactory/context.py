"""
PipelineContext — one run's working state.

One instance per issue. Under the autonomy rule this is orchestration state and a
mirror of the graph's counters — not a channel between agents. What an agent
produces is published (the spec as an issue, the code as commits, the review on
the pull request) and read back from there by whoever needs it. The gate reports
below are the exception, by design: they are this iteration's feedback to the
developer, and they are cited in the pull request that ends the run.
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
    """The specification, as the analyst wrote it and as the spec issue holds it.

    Never kept on the context: it is published by the analyst and read back from
    the issue by each stage that needs it (see ``github.spec_issue.spec_for``).
    """

    summary: str
    acceptance_criteria: list[str]
    files_to_create: list[str]
    files_to_modify: list[str]
    test_strategy: str
    tech_notes: str


@dataclass
class VerificationReport:
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
    score: float  # 0.0–1.0, the reviewer's own estimate of the change's quality


@dataclass
class PipelineContext:
    issue: GitHubIssue
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))

    # Git / GitHub
    branch_name: str = ""
    commits: list[str] = field(default_factory=list)
    pr_url: str | None = None
    pr_number: int | None = None
    diff: str = ""  # branch vs base, refreshed before each review

    # Where the specification was published. The spec issue is the artifact; this
    # is the address every stage reads it from, and the pull request cites.
    spec_issue_number: int | None = None
    # Gate reports — this iteration's feedback to the developer.
    verification_report: VerificationReport | None = None
    scope_report: Any | None = None  # verification.scope.ScopeReport
    review_results: list[ReviewResult] = field(default_factory=list)

    # Tracking
    verification_attempts: int = 0
    # Times the reviewer sent the change back to the developer. Counted separately
    # from verification failures — they are different gates and the distinction
    # matters in the record — but they share one budget of developer iterations.
    review_rejections: int = 0
    # Times the scope gate sent the change back because it did not touch the files
    # the task declared. Separate from the other two: a different gate, and the
    # distinction matters when reading the record afterwards.
    scope_rejections: int = 0
    # True when the budget ran out with the reviewer still requesting changes: the
    # PR is opened anyway for the human to arbitrate, and this keeps the unsatisfied
    # gate visible instead of silently dropping it.
    review_unresolved: bool = False
    # Lint issues the developer left behind, one entry per attempt, measured before
    # autofix cleans them. Without this the pipeline would tidy up after the model
    # and then score the tidied result — the developer's own hygiene must stay
    # visible. None means the measurement could not be taken.
    lint_left_behind: list[int | None] = field(default_factory=list)
    # {"analyst": "gemma4:26b", "developer": "qwen3-coder:30b", ...} — what the
    # reviewer reads to avoid the developer's model.
    model_assignments: dict[str, str] = field(default_factory=dict)

    # Execution log (for KB scoring)
    execution_log: list[dict[str, Any]] = field(default_factory=list)

    @property
    def iterations_used(self) -> int:
        """Developer iterations consumed, whichever gate sent the change back.

        Every gate draws on the same budget: a change that alternates between them
        must still terminate.
        """
        return self.verification_attempts + self.review_rejections + self.scope_rejections

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
