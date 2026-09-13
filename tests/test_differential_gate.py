"""
Tests for the differential verdict (issue #104).

This is the part of the gate that will be wrong, so it is tested in isolation:
no Docker, no GitHub, no database. A report goes in, a baseline goes in, and what
comes out has to say exactly what the change introduced — because that, and only
that, is what the developer is sent back to fix.

The cases that matter, and why each is here:

* identical reports are not a regression, however dirty the repository is;
* a finding whose line moved is the same finding — line numbers are not a key;
* a new rule code on a file that already had findings *is* a regression;
* one more occurrence of a key the base already had is a regression too, which
  set difference would miss and multiset difference catches;
* a fixed finding is not a regression, and is not required either;
* a tool in the error state fails the report whatever the baseline says;
* with no baseline, the absolute rule applies and the summary says so.
"""

from __future__ import annotations

from devfactory.context import VerificationReport
from devfactory.verification.differential import (
    ABSOLUTE,
    DIFFERENTIAL,
    BaselineRef,
    apply,
    fingerprints,
    judge,
)
from devfactory.verification.runner import CLEAN, ERROR, FINDINGS, SKIPPED

# ── Builders ───────────────────────────────────────────────────────────────────


def ruff_issue(filename: str, row: int, code: str, message: str) -> dict:
    """One entry of ruff's --output-format=json output."""
    return {"filename": filename, "location": {"row": row}, "code": code, "message": message}


def bandit_finding(filename: str, test_id: str, severity: str, line: int = 1) -> dict:
    """One entry of bandit's -f json `results` list."""
    return {
        "filename": filename,
        "test_id": test_id,
        "issue_severity": severity,
        "issue_text": "Possible SQL injection vector.",
        "line_number": line,
    }


def pytest_output(*nodes: str) -> str:
    """A pytest run whose short summary reports ``nodes`` as failed."""
    body = "\n".join(f"FAILED {node} - AssertionError: nope" for node in nodes)
    return f"=========== short test summary info ============\n{body}\n{len(nodes)} failed"


def report(
    ruff_issues: list[dict] | None = None,
    mypy_errors: list[str] | None = None,
    bandit_results: list[dict] | None = None,
    pytest_nodes: tuple[str, ...] = (),
    **overrides: dict,
) -> VerificationReport:
    """A report with each tool holding exactly what the caller passed.

    ``**overrides`` replaces a whole tool result, which is how the error and
    skipped states get in; the named arguments are the common case. They are named
    apart from the tool so that both can be expressed.
    """
    results: dict[str, dict] = {
        "ruff": {"status": FINDINGS if ruff_issues else CLEAN, "issues": ruff_issues or []},
        "mypy": {"status": FINDINGS if mypy_errors else CLEAN, "errors": mypy_errors or []},
        "bandit": {
            "status": FINDINGS if bandit_results else CLEAN,
            "findings": bandit_results or [],
            "severity": _top_severity(bandit_results or []),
        },
        "pytest": {
            "status": FINDINGS if pytest_nodes else CLEAN,
            "passed": 10,
            "failed": len(pytest_nodes),
            "errors": [],
            "raw": pytest_output(*pytest_nodes),
        },
    }
    results.update(overrides)
    return VerificationReport(
        passed=False,
        ruff=results["ruff"],
        mypy=results["mypy"],
        bandit=results["bandit"],
        pytest=results["pytest"],
        summary="absolute summary",
        raw_output="{}",
        environment="python 3.12 (from the documented default), install from uv.lock",
    )


def _top_severity(findings: list[dict]) -> str:
    severities = [f["issue_severity"] for f in findings]
    for level in ("HIGH", "MEDIUM", "LOW"):
        if level in severities:
            return level
    return "none"


REF = BaselineRef(
    repo="owner/target", base_sha="a5ac825e" * 5, recorded_at="2026-09-13", cached=True
)


def verdict_for(head: VerificationReport, base: VerificationReport | None):
    """Judge ``head`` against ``base``'s fingerprints, as the agent does."""
    return judge(head, fingerprints(base) if base is not None else None, ref=REF)


# ── The diff itself ────────────────────────────────────────────────────────────


def test_identical_reports_introduce_nothing():
    """A dirty repository whose branch changes nothing about it passes."""
    dirty = report(
        ruff_issues=[ruff_issue("/workspace/a.py", 3, "F401", "`os` imported but unused")],
        mypy_errors=["b.py:7: error: Incompatible types  [assignment]"],
        bandit_results=[bandit_finding("./storage/db.py", "B608", "MEDIUM")],
    )

    verdict = verdict_for(dirty, dirty)

    assert verdict.passed
    assert verdict.rule == DIFFERENTIAL
    assert verdict.count("introduced") == 0
    assert verdict.count("inherited") == 3
    assert verdict.count("fixed") == 0


def test_a_finding_whose_line_moved_is_not_a_regression():
    """Adding a line above a finding moves it; it is still the same finding."""
    base = report(
        ruff_issues=[ruff_issue("/workspace/a.py", 3, "F401", "`os` imported but unused")]
    )
    head = report(
        ruff_issues=[ruff_issue("/workspace/a.py", 41, "F401", "`os` imported but unused")]
    )

    verdict = verdict_for(head, base)

    assert verdict.passed
    assert verdict.count("introduced") == 0
    assert verdict.count("inherited") == 1


def test_a_new_rule_code_on_an_existing_file_is_a_regression():
    """The file was already dirty; the change made it dirtier, and that blocks."""
    base = report(
        ruff_issues=[ruff_issue("/workspace/a.py", 3, "F401", "`os` imported but unused")]
    )
    head = report(
        ruff_issues=[
            ruff_issue("/workspace/a.py", 3, "F401", "`os` imported but unused"),
            ruff_issue("/workspace/a.py", 9, "E712", "Comparison to `True`"),
        ]
    )

    verdict = verdict_for(head, base)

    assert not verdict.passed
    ruff = next(t for t in verdict.tools if t.tool == "ruff")
    assert ruff.blocking
    assert ruff.introduced == ("a.py:9 — [E712] Comparison to `True`",)
    assert len(ruff.inherited) == 1


def test_one_more_occurrence_of_an_existing_key_is_a_regression():
    """Twenty B608 in one file were inherited; the twenty-first is not.

    Set difference would call this clean — the key is already in the base — so the
    comparison counts occurrences.
    """
    twenty = [bandit_finding("./storage/db.py", "B608", "MEDIUM", line=i) for i in range(20)]
    base = report(bandit_results=twenty)
    head = report(
        bandit_results=twenty + [bandit_finding("./storage/db.py", "B608", "MEDIUM", line=99)]
    )

    verdict = verdict_for(head, base)

    assert not verdict.passed
    bandit = next(t for t in verdict.tools if t.tool == "bandit")
    assert len(bandit.introduced) == 1
    assert len(bandit.inherited) == 20


def test_a_fixed_finding_is_not_a_regression_and_is_recorded():
    """Cleaning up inherited debt passes, and the verdict says how much."""
    base = report(
        ruff_issues=[
            ruff_issue("/workspace/a.py", 3, "F401", "`os` imported but unused"),
            ruff_issue("/workspace/a.py", 9, "E712", "Comparison to `True`"),
        ]
    )
    head = report(
        ruff_issues=[ruff_issue("/workspace/a.py", 3, "F401", "`os` imported but unused")]
    )

    verdict = verdict_for(head, base)

    assert verdict.passed
    ruff = next(t for t in verdict.tools if t.tool == "ruff")
    assert ruff.introduced == ()
    assert ruff.fixed == ("a.py — [E712] Comparison to `True`",)


def test_a_clean_branch_on_a_dirty_base_passes():
    """Every inherited finding fixed at once is still not a regression."""
    base = report(mypy_errors=["b.py:7: error: Incompatible types  [assignment]"])
    head = report()

    verdict = verdict_for(head, base)

    assert verdict.passed
    assert verdict.count("fixed") == 1


def test_bandit_low_findings_never_block():
    """LOW never failed the absolute gate, and does not start failing here."""
    base = report()
    head = report(bandit_results=[bandit_finding("a.py", "B404", "LOW")])

    verdict = verdict_for(head, base)

    assert verdict.passed
    bandit = next(t for t in verdict.tools if t.tool == "bandit")
    assert len(bandit.introduced) == 1
    assert not bandit.blocking


def test_an_introduced_medium_bandit_finding_blocks():
    base = report()
    head = report(bandit_results=[bandit_finding("a.py", "B608", "MEDIUM")])

    verdict = verdict_for(head, base)

    assert not verdict.passed


def test_the_same_mypy_error_reported_under_two_container_prefixes_matches():
    """mypy runs over the installed copy; nothing about the key may depend on that."""
    base = report(mypy_errors=["/build/b.py:7: error: Incompatible types  [assignment]"])
    head = report(mypy_errors=["/workspace/b.py:7: error: Incompatible types  [assignment]"])

    verdict = verdict_for(head, base)

    assert verdict.passed
    assert verdict.count("inherited") == 1


# ── pytest, by node id ─────────────────────────────────────────────────────────


def test_a_test_that_already_failed_is_inherited():
    base = report(pytest_nodes=("tests/test_a.py::test_one",))
    head = report(pytest_nodes=("tests/test_a.py::test_one",))

    verdict = verdict_for(head, base)

    assert verdict.passed
    assert verdict.count("inherited") == 1


def test_a_test_that_passed_on_the_base_and_fails_now_is_a_regression():
    base = report()
    head = report(pytest_nodes=("tests/test_a.py::test_one",))

    verdict = verdict_for(head, base)

    assert not verdict.passed
    pytest_diff = next(t for t in verdict.tools if t.tool == "pytest")
    assert pytest_diff.introduced == ("tests/test_a.py::test_one",)


def test_a_new_test_that_fails_is_introduced():
    """The branch adds a test and it fails: the node id was not in the base's set."""
    base = report(pytest_nodes=("tests/test_a.py::test_old",))
    head = report(pytest_nodes=("tests/test_a.py::test_old", "tests/test_new.py::test_added"))

    verdict = verdict_for(head, base)

    assert not verdict.passed
    pytest_diff = next(t for t in verdict.tools if t.tool == "pytest")
    assert pytest_diff.introduced == ("tests/test_new.py::test_added",)
    assert pytest_diff.inherited == ("tests/test_a.py::test_old",)


def test_a_captured_log_line_is_not_read_as_a_failing_test():
    """A test's captured output holds lines starting with ERROR; they are not node ids."""
    raw = (
        "ERROR    devfactory.graph:graph.py:12 the gate refused\n"
        "=========== short test summary info ============\n"
        "FAILED tests/test_a.py::test_one - AssertionError\n"
    )
    head = report(pytest={"status": FINDINGS, "passed": 1, "failed": 1, "errors": [], "raw": raw})

    stored = fingerprints(head)

    assert stored["pytest"]["keys"] == [["pytest", "tests/test_a.py::test_one"]]


def test_pytest_failures_are_read_past_the_runners_twenty_line_cap():
    """The runner caps `errors` at twenty for the summary; the key set must not be."""
    nodes = tuple(f"tests/test_a.py::test_{i}" for i in range(25))
    head = report(
        pytest={
            "status": FINDINGS,
            "passed": 0,
            "failed": 25,
            "errors": [f"FAILED {n}" for n in nodes[:20]],
            "raw": pytest_output(*nodes),
        }
    )

    stored = fingerprints(head)

    assert len(stored["pytest"]["keys"]) == 25


# ── The rules that never soften ────────────────────────────────────────────────


def test_a_tool_in_the_error_state_fails_whatever_the_baseline_says():
    """ "The tool could not run" is not a finding that can be inherited."""
    base = report(ruff={"status": ERROR, "issues": [], "error": "exited 2", "raw": "boom"})
    head = report(ruff={"status": ERROR, "issues": [], "error": "exited 2", "raw": "boom"})

    verdict = verdict_for(head, base)

    assert not verdict.passed
    assert [t.tool for t in verdict.errored] == ["ruff"]
    assert "did not run" in apply(head, verdict).summary


def test_a_tool_the_profile_skips_blocks_nothing():
    skipped = {"status": SKIPPED, "issues": [], "skipped": "not part of the gate"}
    base = report(ruff=skipped)
    head = report(ruff=skipped)

    verdict = verdict_for(head, base)

    assert verdict.passed


def test_without_a_baseline_the_absolute_rule_applies_and_is_named():
    """The chosen fallback: strict, and visible. Never a silent pass."""
    head = report(
        ruff_issues=[ruff_issue("/workspace/a.py", 3, "F401", "`os` imported but unused")]
    )

    verdict = judge(head, None, reason="no baseline for the base commit")
    rebuilt = apply(head, verdict)

    assert verdict.rule == ABSOLUTE
    assert not rebuilt.passed
    assert "Pass rule: absolute" in rebuilt.summary
    assert "no baseline for the base commit" in rebuilt.summary
    assert rebuilt.differential is not None
    assert rebuilt.differential["rule"] == ABSOLUTE


def test_a_tool_that_errored_on_the_base_is_judged_absolutely():
    """The base measured nothing for it, so "already present" is unanswerable."""
    base = report(mypy={"status": ERROR, "errors": [], "error": "exited 2", "raw": ""})
    head = report(mypy_errors=["b.py:7: error: Incompatible types  [assignment]"])

    verdict = verdict_for(head, base)
    mypy = next(t for t in verdict.tools if t.tool == "mypy")

    assert not verdict.passed
    assert mypy.rule == ABSOLUTE
    assert "error state" in mypy.reason
    # The other tools still get the differential rule — the fallback is per tool.
    assert next(t for t in verdict.tools if t.tool == "ruff").rule == DIFFERENTIAL


# ── What each reader is handed ─────────────────────────────────────────────────


def test_the_developer_is_shown_only_what_the_change_introduced():
    """The whole point: 67 inherited findings must not reach the developer."""
    inherited = [
        ruff_issue("/workspace/old.py", i, "E501", f"Line too long ({i})") for i in range(67)
    ]
    base = report(ruff_issues=inherited)
    head = report(
        ruff_issues=[*inherited, ruff_issue("/workspace/new.py", 2, "F821", "Undefined name `x`")]
    )

    rebuilt = apply(head, verdict_for(head, base))

    assert not rebuilt.passed
    assert "new.py:2 — [F821] Undefined name `x`" in rebuilt.developer_feedback
    assert "old.py" not in rebuilt.developer_feedback
    assert "Inherited" not in rebuilt.developer_feedback


def test_the_full_summary_names_and_counts_the_inherited_findings():
    """Reported, never dropped — the evidence rule in CLAUDE.md."""
    inherited = [
        ruff_issue("/workspace/old.py", i, "E501", f"Line too long ({i})") for i in range(67)
    ]
    base = report(ruff_issues=inherited)
    head = report(ruff_issues=inherited)

    rebuilt = apply(head, verdict_for(head, base))

    assert rebuilt.passed
    assert "Pass rule: differential" in rebuilt.summary
    assert "67 finding(s) already present on the base branch" in rebuilt.summary
    assert "old.py" in rebuilt.summary
    assert rebuilt.differential is not None
    assert rebuilt.differential["tools"]["ruff"]["inherited_count"] == 67


def test_the_rebuilt_report_carries_the_measurements_through_untouched():
    """A differential verdict changes how a measurement is read, never what it says."""
    head = report(bandit_results=[bandit_finding("a.py", "B608", "MEDIUM")])

    rebuilt = apply(head, verdict_for(head, head))

    assert rebuilt.ruff is head.ruff
    assert rebuilt.bandit is head.bandit
    assert rebuilt.raw_output == head.raw_output
    assert rebuilt.environment == head.environment


def test_the_summary_reports_paths_the_developer_can_open():
    """Same rule as the absolute summary: no container prefixes reach a reader."""
    base = report()
    head = report(mypy_errors=["/build/devfactory/runner.py:12: error: bad type  [assignment]"])

    rebuilt = apply(head, verdict_for(head, base))

    assert "/build/" not in rebuilt.summary
    assert "devfactory/runner.py:12" in rebuilt.summary
