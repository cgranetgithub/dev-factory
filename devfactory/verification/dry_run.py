"""
A dry run of the verification gate on a repository's default branch.

Onboarding a repository asks a question the pipeline cannot answer: does this
repository pass its own gate *as it stands*? If it does not, every run of the
factory on it fails at verification whatever the developer produced, and the
owner should learn that in a minute rather than after an analyst, a developer and
three retries.

The answer separates two things that look alike in a report and are nothing alike
in practice:

* **the gate could not run** — a tool crashed, or the target's environment could
  not be built. Ours to fix (or a declaration missing from the target);
* **the target's own suite fails** — ruff findings, mypy errors, a MEDIUM bandit
  finding, failing tests. Theirs to fix, and not something the gate should be
  weakened to hide.

Nothing here writes to the target: the checkout is a clone in the factory's
workspace and the gate only reads it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from devfactory.context import VerificationReport
from devfactory.github import git_ops
from devfactory.verification.environment import TargetEnvironment, resolve_environment
from devfactory.verification.profiles import TOOLS, VerificationProfile, load_profile
from devfactory.verification.runner import ERROR, FINDINGS, SKIPPED, VerificationRunner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolOutcome:
    """One tool's verdict, and whose problem it is.

    Attributes:
        name: The tool.
        status: ``clean``, ``findings``, ``error`` or ``skipped``, as the runner
            classified it.
        detail: What it found, or why it did not run.
        ours: True when the gate itself is at fault — the tool did not run.
        blocking: True when this outcome is what keeps the report from passing.
            A finding is not automatically one: bandit's LOW findings never fail
            the gate, and the dry run must not present them as a blocker.
    """

    name: str
    status: str
    detail: str
    ours: bool
    blocking: bool


@dataclass(frozen=True)
class GateDryRun:
    """What the gate did on one repository, and what it means for onboarding."""

    repo: str
    path: Path
    commit: str
    branch: str
    environment: TargetEnvironment
    profile: VerificationProfile
    report: VerificationReport
    duration_s: float
    outcomes: tuple[ToolOutcome, ...]

    @property
    def gate_failures(self) -> tuple[ToolOutcome, ...]:
        """Tools that did not run. Our problem."""
        return tuple(o for o in self.outcomes if o.ours)

    @property
    def target_failures(self) -> tuple[ToolOutcome, ...]:
        """Tools that ran and found the repository wanting. The target's problem."""
        return tuple(o for o in self.outcomes if not o.ours and o.blocking)

    @property
    def exit_code(self) -> int:
        """0 the repository passes its gate, 1 it does not, 2 the gate could not run.

        The same convention as ``devfactory controls check``: "we could not look"
        is never reported as "nothing wrong".
        """
        if self.gate_failures:
            return 2
        return 0 if self.report.passed else 1


def dry_run(repo: str, path: Path | None = None) -> GateDryRun:
    """Run the full gate over ``repo``'s default branch and describe the result.

    Args:
        repo: The target's ``owner/repo`` slug. Also the key its verification
            profile is stored under, which is why it is required even when the
            checkout is local.
        path: An existing checkout to verify instead of cloning. For a repository
            that is not on GitHub yet, or a clone made by hand; the slug still
            selects the profile.

    Returns:
        The outcome per tool, the environment the gate built, and the timing.
    """
    checkout = path or git_ops.clone_for_onboarding(repo)
    commit, branch = git_ops.head_of(checkout)
    profile = load_profile(repo)
    environment = resolve_environment(checkout, profile)

    logger.info(f"[onboarding] gate dry run on {repo} @ {commit[:8]} ({branch})")
    started = time.monotonic()
    report = VerificationRunner().run(checkout, repo=repo)
    duration = time.monotonic() - started

    return GateDryRun(
        repo=repo,
        path=checkout,
        commit=commit,
        branch=branch,
        environment=environment,
        profile=profile,
        report=report,
        duration_s=duration,
        outcomes=_outcomes(report),
    )


def _outcomes(report: VerificationReport) -> tuple[ToolOutcome, ...]:
    """Turn the report into one line per tool, in the gate's own order.

    ``blocking`` repeats the runner's pass rule per tool rather than inventing a
    second one: bandit fails only on MEDIUM and above, pytest on a failure or a
    collection error, ruff and mypy on any finding. Reading it off the report
    would only tell us that *something* failed, and an onboarding report that
    cannot name what to fix is of no use.
    """
    # Each tool: its result, how to describe it, and what makes it blocking.
    results = {
        "ruff": (
            report.ruff,
            lambda r: f"{len(r.get('issues', []))} finding(s)",
            lambda r: bool(r.get("issues")),
        ),
        "mypy": (
            report.mypy,
            lambda r: f"{len(r.get('errors', []))} error(s)",
            lambda r: bool(r.get("errors")),
        ),
        "bandit": (
            report.bandit,
            lambda r: (
                f"{len(r.get('findings', []))} finding(s), top severity {r.get('severity', 'none')}"
            ),
            lambda r: r.get("severity") in ("HIGH", "MEDIUM"),
        ),
        "pytest": (
            report.pytest,
            lambda r: f"{r.get('passed', 0)} passed, {r.get('failed', 0)} failed",
            lambda r: bool(r.get("failed")) or bool(r.get("errors")),
        ),
    }
    outcomes = []
    for name in TOOLS:
        result, describe, is_blocking = results[name]
        status = result.get("status", ERROR)
        if status == ERROR:
            detail = result.get("error", "did not run")
        elif status == SKIPPED:
            detail = result.get("skipped", "skipped")
        else:
            detail = describe(result)
        outcomes.append(
            ToolOutcome(
                name=name,
                status=status,
                detail=detail,
                ours=status == ERROR,
                blocking=status == FINDINGS and is_blocking(result),
            )
        )
    return tuple(outcomes)
