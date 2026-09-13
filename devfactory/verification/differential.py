"""
The differential verdict — the gate fails on what a change *introduced*.

The four tools still run over the whole repository: a finding must never be
hidden because it is old, and the gate that hides one is not a control. What
changes is the **pass rule**. An absolute rule asks "is this repository clean?",
and on a repository with standing debt the answer is no for every change anyone
could write — so every pipeline run failed at verification whatever the developer
produced, and the summary it was handed was full of findings in files the issue
never mentioned (issue #104). The differential rule asks the question the gate was
always meant to ask: *did this change make it worse?*

Three populations come out of comparing the branch's report with the base
branch's own report at the commit the branch left:

* **introduced** — in the branch, not in the base. These fail the report, and
  these alone are what the developer is shown on a send-back.
* **inherited** — in both. Reported, counted, named in the pull request, and
  never blocking. They are the repository's standing debt and the auditor's
  evidence that we saw it; dropping them is what ``CLAUDE.md`` forbids.
* **fixed** — in the base, not in the branch. Recorded, never required. A change
  that cleans up debt on its way past should be visible as such, but demanding it
  would be the gate asking for work the issue never asked for.

Matching is by a key that survives an edit above it, because line numbers move and
a finding that only moved is not a new finding: ``(file, rule code, message)`` for
ruff and mypy, ``(file, test id, severity)`` for bandit — the three fields bandit's
JSON gives that are stable — and the node id for pytest. The node id is exactly
right for the three cases that matter: a test that passed on the base and fails now
was not in the base's failing set, so it is introduced; a test that already failed
is in both, so it is inherited; and a test the branch *adds* that fails was not in
the base's failing set either, so it is introduced too.

Counting is by multiset, not by set. ``storage/db.py`` holds twenty ``B608``
findings under one key; a change that adds a twenty-first must be a regression, and
set difference would call it inherited.

Two things the differential rule never softens:

* a tool in the **error** state fails the report whatever the baseline says. "The
  tool could not run" is not a finding that can be inherited;
* when no baseline can be had for a tool, that tool falls back to the **absolute**
  rule and the summary says so — see :func:`judge`.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from devfactory.context import VerificationReport
from devfactory.verification.runner import (
    BUILD_DIR,
    CONTAINER_WORKDIR,
    ERROR,
    SKIPPED,
    status_or,
)

logger = logging.getLogger(__name__)

# The two pass rules, named because a verdict whose rule is invisible is not
# evidence: every summary this module writes says which one produced it.
DIFFERENTIAL = "differential"
ABSOLUTE = "absolute"

# The tools, in the gate's own order. Mirrors profiles.TOOLS; imported from there
# would make this module depend on the profile machinery for a tuple of names.
TOOLS = ("ruff", "mypy", "bandit", "pytest")

# Severities that block. Bandit's LOW findings never failed the absolute gate, and
# the differential one does not start failing on them either — a change should not
# be sent back for an import bandit dislikes.
BLOCKING_SEVERITIES = ("HIGH", "MEDIUM")

# How many findings of one population are named in a summary before it stops
# listing and only counts. The developer reads this and has an iteration budget;
# twenty introduced findings is already a change to rewrite rather than patch.
_SAMPLE = 20

# `path/to/file.py:12: error: message  [code]`, with an optional column. mypy's
# own format, matched in full so that an unparseable line falls back to being its
# own key rather than being silently dropped.
_MYPY_LINE = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?::\d+)?: error: (?P<message>.*)$")
_MYPY_CODE = re.compile(r"\s+\[(?P<code>[a-zA-Z0-9_-]+)\]$")

# pytest's short summary: `FAILED tests/test_x.py::test_y - AssertionError: …` and
# `ERROR tests/test_x.py::test_y`. The node id is the first field; whatever pytest
# appends after " - " is the reason, which changes between runs and is not a key.
_PYTEST_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(?P<node>\S+)")
_PYTEST_SUMMARY_HEADER = "short test summary info"


@dataclass(frozen=True)
class ToolDiff:
    """One tool's contribution to the verdict.

    Attributes:
        tool: The tool's name.
        status: What the runner classified the branch's run as.
        rule: Which pass rule was applied to this tool.
        reason: Why the absolute rule was applied; empty under the differential one.
        introduced: Findings the change added, rendered one line each. Empty under
            the absolute rule, where nothing is attributable to the change.
        inherited: Findings the base branch already had. Empty under the absolute
            rule for the same reason.
        fixed: Findings the base had and the branch no longer reports.
        findings: Everything the branch's run reported, under either rule.
        blocking: Whether this tool keeps the report from passing.
    """

    tool: str
    status: str
    rule: str
    reason: str
    introduced: tuple[str, ...]
    inherited: tuple[str, ...]
    fixed: tuple[str, ...]
    findings: tuple[str, ...]
    blocking: bool

    @property
    def held_against_the_change(self) -> tuple[str, ...]:
        """What this tool asks the developer to fix.

        The introduced findings under the differential rule; every finding under
        the absolute one, where the gate cannot tell which are the change's.
        """
        return self.introduced if self.rule == DIFFERENTIAL else self.findings


@dataclass(frozen=True)
class BaselineRef:
    """Where the baseline this verdict was measured against came from."""

    repo: str
    base_sha: str
    recorded_at: str
    cached: bool

    def describe(self) -> str:
        origin = "from the knowledge base" if self.cached else "measured for this run"
        return f"{self.repo}@{self.base_sha[:8]}, recorded {self.recorded_at} ({origin})"


@dataclass(frozen=True)
class Verdict:
    """The differential reading of one verification report."""

    rule: str
    baseline: BaselineRef | None
    reason: str  # why the run as a whole fell back to the absolute rule
    tools: tuple[ToolDiff, ...]

    @property
    def errored(self) -> tuple[ToolDiff, ...]:
        """Tools that did not run. They fail the report under either rule."""
        return tuple(t for t in self.tools if t.status == ERROR)

    @property
    def passed(self) -> bool:
        return not self.errored and not any(t.blocking for t in self.tools)

    def count(self, population: str) -> int:
        """Total across every tool of ``introduced``, ``inherited`` or ``fixed``."""
        return sum(len(getattr(t, population)) for t in self.tools)


# ── Fingerprints ───────────────────────────────────────────────────────────────


def fingerprints(report: VerificationReport) -> dict[str, dict[str, Any]]:
    """The storable shape of a report: per tool, its status and its finding keys.

    This is what a baseline row holds. Keys rather than whole tool output because
    a key is all the comparison uses and a bandit run on a real repository is
    hundreds of findings of JSON — and because a list of keys is still a readable
    record of what was already wrong at that commit.
    """
    stored: dict[str, dict[str, Any]] = {}
    for tool in TOOLS:
        result = _result(report, tool)
        stored[tool] = {
            "status": result.get("status", ERROR),
            "keys": [list(f.key) for f in _findings(tool, result)],
        }
    return stored


@dataclass(frozen=True)
class _Finding:
    """One occurrence: the key it is matched on, and how to print it."""

    key: tuple[str, ...]
    text: str


def _result(report: VerificationReport, tool: str) -> dict:
    return {
        "ruff": report.ruff,
        "mypy": report.mypy,
        "bandit": report.bandit,
        "pytest": report.pytest,
    }[tool]


def _findings(tool: str, result: dict) -> list[_Finding]:
    """Every occurrence one tool reported, as keys plus printable text.

    A tool in the error or skipped state reports nothing comparable: the first
    has no verdict at all, the second was not asked for one.
    """
    if result.get("status") in (ERROR, SKIPPED):
        return []
    return {
        "ruff": _ruff_findings,
        "mypy": _mypy_findings,
        "bandit": _bandit_findings,
        "pytest": _pytest_findings,
    }[tool](result)


def normalise_path(path: str) -> str:
    """A path as the repository sees it, whichever container path a tool used.

    Ruff and bandit read the read-only mount; mypy and pytest read the installed
    copy. The same file therefore arrives under two different prefixes depending
    on which tool found it, and two runs of the same commit would not match if the
    prefix were part of the key.
    """
    for prefix in (f"{CONTAINER_WORKDIR}/", f"{BUILD_DIR}/"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
    if path.startswith("./"):
        path = path[2:]
    return path


def _ruff_findings(result: dict) -> list[_Finding]:
    found = []
    for issue in result.get("issues", []):
        path = normalise_path(str(issue.get("filename", "?")))
        code = str(issue.get("code") or "?")
        message = str(issue.get("message", ""))
        row = issue.get("location", {}).get("row", "?")
        found.append(
            _Finding(key=("ruff", path, code, message), text=f"{path}:{row} — [{code}] {message}")
        )
    return found


def _mypy_findings(result: dict) -> list[_Finding]:
    found = []
    for line in result.get("errors", []):
        text = normalise_path(str(line).strip())
        match = _MYPY_LINE.match(text)
        if match is None:
            # Not mypy's usual shape. Keeping the whole line as its own key is
            # stable enough to compare and keeps the finding in the record, which
            # is the part that matters.
            found.append(_Finding(key=("mypy", "", "", text), text=text))
            continue
        message = match.group("message")
        code_match = _MYPY_CODE.search(message)
        code = code_match.group("code") if code_match else "?"
        path = normalise_path(match.group("file"))
        found.append(_Finding(key=("mypy", path, code, message), text=text))
    return found


def _bandit_findings(result: dict) -> list[_Finding]:
    found = []
    for finding in result.get("findings", []):
        path = normalise_path(str(finding.get("filename", "?")))
        test_id = str(finding.get("test_id", "?"))
        severity = str(finding.get("issue_severity", "LOW"))
        line = finding.get("line_number", "?")
        text = f"{path}:{line} — {test_id} ({severity}) {finding.get('issue_text', '')}".rstrip()
        found.append(_Finding(key=("bandit", path, test_id, severity), text=text))
    return found


def _pytest_findings(result: dict) -> list[_Finding]:
    """The node ids the branch's run reports as failing or erroring.

    Read from the raw output rather than from ``errors``, which the runner caps at
    twenty lines for the summary's sake: a baseline truncated at twenty would make
    the twenty-first inherited failure look introduced on every later run.
    """
    raw = result.get("raw") or "\n".join(result.get("errors", []))
    # Only the short summary. A failing test's captured output is reproduced above
    # it, and a captured log record at ERROR level starts with the same word —
    # reading the whole stream would turn a log line into a test node id.
    header = raw.rfind(_PYTEST_SUMMARY_HEADER)
    if header >= 0:
        raw = raw[header:]
    found = []
    for line in raw.splitlines():
        match = _PYTEST_LINE.match(line.strip())
        if match is None:
            continue
        node = normalise_path(match.group("node"))
        found.append(_Finding(key=("pytest", node), text=node))
    return found


# ── The verdict ────────────────────────────────────────────────────────────────


def judge(
    report: VerificationReport,
    baseline: dict[str, dict[str, Any]] | None,
    ref: BaselineRef | None = None,
    reason: str = "",
) -> Verdict:
    """Read ``report`` against ``baseline`` and decide what the change introduced.

    Args:
        report: The branch's report, as the runner produced it.
        baseline: The base branch's fingerprints (see :func:`fingerprints`), or
            None when no baseline could be had.
        ref: Where that baseline came from, for the record.
        reason: Why there is no baseline. Printed in the summary, because the
            reader of a verdict has to know which rule produced it.

    Returns:
        The verdict. With no baseline every tool is judged absolutely, which is
        the behaviour the gate had before this existed: strictly conservative, it
        can over-block but it cannot let a regression through. Failing the run
        outright instead would turn a missing cache row into an unexplainable
        verification failure, which is the exact experience issue #104 is about.
    """
    tools = tuple(_judge_tool(report, name, baseline) for name in TOOLS)
    rule = DIFFERENTIAL if any(t.rule == DIFFERENTIAL for t in tools) else ABSOLUTE
    return Verdict(
        rule=rule, baseline=ref if rule == DIFFERENTIAL else None, reason=reason, tools=tools
    )


def _judge_tool(
    report: VerificationReport, tool: str, baseline: dict[str, dict[str, Any]] | None
) -> ToolDiff:
    result = _result(report, tool)
    status = result.get("status", ERROR)
    head = _findings(tool, result)
    findings = tuple(f.text for f in head)

    base_entry = (baseline or {}).get(tool)
    reason = _no_baseline_reason(baseline, base_entry)
    if base_entry is None or reason:
        return ToolDiff(
            tool=tool,
            status=status,
            rule=ABSOLUTE,
            reason=reason,
            introduced=(),
            inherited=(),
            fixed=(),
            findings=findings,
            blocking=_blocks(tool, result, head),
        )

    base_keys = Counter(tuple(k) for k in base_entry["keys"])

    # Walk the branch's occurrences and spend the base's count of each key. What
    # is left over is what the change added — per occurrence, so twenty-one
    # instances of a key the base had twenty times yields exactly one introduced.
    budget = Counter(base_keys)
    introduced: list[_Finding] = []
    inherited: list[_Finding] = []
    for finding in head:
        if budget[finding.key] > 0:
            budget[finding.key] -= 1
            inherited.append(finding)
        else:
            introduced.append(finding)

    # What the base had and the branch no longer reports. Rendered from the key,
    # since the baseline row holds keys and not the base run's own text.
    fixed = tuple(
        _describe(key)
        for key, count in (base_keys - Counter(f.key for f in head)).items()
        for _ in range(count)
    )

    return ToolDiff(
        tool=tool,
        status=status,
        rule=DIFFERENTIAL,
        reason="",
        introduced=tuple(f.text for f in introduced),
        inherited=tuple(f.text for f in inherited),
        fixed=fixed,
        findings=findings,
        blocking=_blocks(tool, result, introduced),
    )


def _no_baseline_reason(
    baseline: dict[str, dict[str, Any]] | None, entry: dict[str, Any] | None
) -> str:
    """Why ``tool`` cannot be judged differentially, or ``""`` when it can."""
    if baseline is None:
        return "no baseline for the base commit"
    if entry is None:
        return "the baseline was recorded before this tool was part of the gate"
    if entry.get("status") == ERROR:
        # A tool that crashed on the base branch measured nothing there, so
        # "already present" is unanswerable for it. The absolute rule is the only
        # honest reading, and it is the strict one.
        return "the base commit's run of this tool ended in the error state"
    if entry.get("status") == SKIPPED:
        return "the tool was not part of the gate when the baseline was recorded"
    return ""


def _blocks(tool: str, result: dict, findings: list[_Finding]) -> bool:
    """Whether ``findings`` keep the report from passing, by that tool's own rule.

    The same per-tool rule the absolute gate used, applied to a smaller
    population: ruff and mypy block on anything, bandit only from MEDIUM up, and
    pytest on a failure or a collection error. A tool in the error state blocks
    through :attr:`Verdict.errored` instead, whatever it did or did not report.
    """
    if result.get("status") in (ERROR, SKIPPED):
        return False
    if tool == "bandit":
        return any(f.key[3] in BLOCKING_SEVERITIES for f in findings)
    return bool(findings)


def _describe(key: tuple[str, ...]) -> str:
    """A finding key as one readable line, for the populations we hold no text for."""
    tool = key[0]
    if tool == "pytest":
        return key[1]
    if tool == "bandit":
        return f"{key[1]} — {key[2]} ({key[3]})"
    return f"{key[1]} — [{key[2]}] {key[3]}"


# ── The report the verdict produces ────────────────────────────────────────────


def apply(report: VerificationReport, verdict: Verdict) -> VerificationReport:
    """The same measurements, re-judged: a new ``passed``, and two summaries.

    The tool results are carried through untouched — they are the measurement, and
    a differential verdict changes how it is read, never what it says.
    """
    return VerificationReport(
        passed=verdict.passed,
        ruff=report.ruff,
        mypy=report.mypy,
        bandit=report.bandit,
        pytest=report.pytest,
        summary=summarise(report, verdict),
        raw_output=report.raw_output,
        environment=report.environment,
        rule=verdict.rule,
        introduced_summary=summarise(report, verdict, introduced_only=True),
        differential=_record(verdict),
    )


def _record(verdict: Verdict) -> dict:
    """The verdict as data, for the pull request body and the stored evidence."""
    return {
        "rule": verdict.rule,
        "reason": verdict.reason,
        "baseline": (
            {
                "repo": verdict.baseline.repo,
                "base_sha": verdict.baseline.base_sha,
                "recorded_at": verdict.baseline.recorded_at,
                "cached": verdict.baseline.cached,
            }
            if verdict.baseline
            else None
        ),
        "tools": {
            t.tool: {
                "rule": t.rule,
                "reason": t.reason,
                "status": t.status,
                "introduced": list(t.introduced),
                "inherited_count": len(t.inherited),
                "inherited_sample": list(t.inherited[:_SAMPLE]),
                "fixed_count": len(t.fixed),
                "fixed_sample": list(t.fixed[:_SAMPLE]),
            }
            for t in verdict.tools
        },
    }


def summarise(report: VerificationReport, verdict: Verdict, introduced_only: bool = False) -> str:
    """The report as text.

    Args:
        report: The branch's report, for the per-tool counts and the tools that
            did not run.
        verdict: What the change introduced, inherited and fixed.
        introduced_only: Write the developer's copy — what this change introduced,
            and nothing else. This is the whole point of the differential gate: a
            developer handed the repository's standing debt spends its iteration
            budget on files the issue never mentioned. The inherited findings are
            not lost, they are in the other copy, which is what the pull request
            and the record carry.

    Returns:
        Markdown.
    """
    lines = ["## Verification Report\n"]
    lines.append(f"**Overall: {'✓ PASSED' if verdict.passed else '✗ FAILED'}**\n")
    lines.append(f"*Pass rule: {_rule_line(verdict)}*\n")
    if report.environment:
        lines.append(f"*Environment: {report.environment}*\n")

    lines += _tool_lines(report, verdict)

    held = [(t, t.held_against_the_change) for t in verdict.tools]
    blocking = [(t, items) for t, items in held if t.blocking and items]
    if blocking:
        lines.append("\n### Introduced by this change — fix these:")
        for tool, items in blocking:
            if tool.rule == ABSOLUTE:
                lines.append(f"- **{tool.tool}** — judged absolutely ({tool.reason}):")
            for item in items[:_SAMPLE]:
                lines.append(f"  - [{tool.tool}] {item}")
            if len(items) > _SAMPLE:
                lines.append(f"  - …and {len(items) - _SAMPLE} more")

    if not introduced_only:
        lines += _inherited_lines(verdict)
        lines += _fixed_lines(verdict)

    lines += _tools_that_did_not_run(report)
    lines += _tools_the_profile_skips(report)

    # Same normalisation as the runner's own summary: whoever reads this works in
    # a checkout, not in the container, and "/build/foo.py" is a path it cannot
    # open.
    text = "\n".join(lines)
    return text.replace(f"{CONTAINER_WORKDIR}/", "").replace(f"{BUILD_DIR}/", "")


def _rule_line(verdict: Verdict) -> str:
    """One sentence naming the rule that produced the verdict, and its basis."""
    if verdict.rule == DIFFERENTIAL and verdict.baseline:
        mixed = [t.tool for t in verdict.tools if t.rule == ABSOLUTE and t.status not in (SKIPPED,)]
        line = (
            f"differential — against the base branch's own report ({verdict.baseline.describe()})"
        )
        if mixed:
            line += f"; judged absolutely for {', '.join(mixed)}"
        return line
    reason = verdict.reason or "no baseline for the base commit"
    return f"absolute — every finding counts ({reason})"


def _tool_lines(report: VerificationReport, verdict: Verdict) -> list[str]:
    """One line per tool: what it measured, and how the verdict reads it."""
    by_name = {t.tool: t for t in verdict.tools}
    details = {
        "ruff": ("Ruff (lint)", f"{len(report.ruff.get('issues', []))} issue(s)", report.ruff),
        "mypy": ("Mypy (types)", f"{len(report.mypy.get('errors', []))} error(s)", report.mypy),
        "bandit": (
            "Bandit (security)",
            f"severity={report.bandit.get('severity', 'none')}, "
            f"{len(report.bandit.get('findings', []))} finding(s)",
            report.bandit,
        ),
        "pytest": (
            "Pytest",
            (
                f"{report.pytest.get('passed', 0)} passed, {report.pytest.get('failed', 0)} failed"
                if (report.pytest.get("passed", 0) or report.pytest.get("failed", 0))
                else "no tests collected"
            ),
            report.pytest,
        ),
    }
    lines = []
    for name in TOOLS:
        label, detail, result = details[name]
        diff = by_name[name]
        if diff.rule == DIFFERENTIAL and (diff.introduced or diff.inherited or diff.fixed):
            detail = (
                f"{detail} — {len(diff.introduced)} introduced, "
                f"{len(diff.inherited)} inherited, {len(diff.fixed)} fixed"
            )
        lines.append(f"- **{label}:** {status_or(result, detail)}")
    return lines


def _inherited_lines(verdict: Verdict) -> list[str]:
    """The standing debt, named and counted. Never dropped, never blocking."""
    inherited = [t for t in verdict.tools if t.inherited]
    if not inherited:
        return []
    total = sum(len(t.inherited) for t in inherited)
    base = verdict.baseline.describe() if verdict.baseline else "the base branch"
    lines = [
        f"\n### Inherited — {total} finding(s) already present on the base branch",
        f"Measured on {base}. They are the repository's standing debt, not this "
        "change's: they do not block it and must not be fixed as part of it.",
    ]
    for tool in inherited:
        lines.append(f"- **{tool.tool}**: {len(tool.inherited)} finding(s)")
        for item in tool.inherited[:5]:
            lines.append(f"  - {item}")
        if len(tool.inherited) > 5:
            lines.append(f"  - …and {len(tool.inherited) - 5} more")
    return lines


def _fixed_lines(verdict: Verdict) -> list[str]:
    """What the change cleaned up on its way past. Recorded, never required."""
    fixed = [t for t in verdict.tools if t.fixed]
    if not fixed:
        return []
    total = sum(len(t.fixed) for t in fixed)
    lines = [
        f"\n### Fixed — {total} inherited finding(s) the base branch had and this change does not"
    ]
    for tool in fixed:
        lines.append(f"- **{tool.tool}**: {len(tool.fixed)} finding(s)")
        for item in tool.fixed[:5]:
            lines.append(f"  - {item}")
        if len(tool.fixed) > 5:
            lines.append(f"  - …and {len(tool.fixed) - 5} more")
    return lines


def _tools_that_did_not_run(report: VerificationReport) -> list[str]:
    """Named with what they printed, as the absolute summary does — see runner."""
    named = [(name, _result(report, name)) for name in TOOLS]
    failed = [(name, r) for name, r in named if r.get("status") == ERROR]
    if not failed:
        return []
    lines = ["\n### Tools that did not run:"]
    for name, result in failed:
        lines.append(f"- **{name}**: {result.get('error', 'did not run')}")
        tail = (result.get("raw") or "").strip()[-1500:]
        if tail:
            lines.append(f"  ```\n{tail}\n  ```")
    return lines


def _tools_the_profile_skips(report: VerificationReport) -> list[str]:
    named = [(name, _result(report, name)) for name in TOOLS]
    skipped = [(name, r) for name, r in named if r.get("status") == SKIPPED]
    if not skipped:
        return []
    lines = ["\n### Tools the profile does not run:"]
    for name, result in skipped:
        lines.append(f"- **{name}**: {result.get('skipped', 'skipped')}")
    return lines
