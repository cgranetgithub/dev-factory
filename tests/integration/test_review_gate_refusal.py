"""Can the review gate refuse? — the measurement behind issue #101.

Across every sandbox run of 2026-09-08 and 2026-09-09 the review gate approved,
every time, and never once sent a change back. Each verdict was defensible, but a
control with no recorded refusal is indistinguishable from a control that is
switched off — and ``docs/VISION.md`` claims the reviewer as a control.

So this measures it, on two changes whose defects are known in advance, against
every model that holds the ``reviewer`` role and drives the agentic loop:

- **Case 1 — a violated acceptance criterion.** The specification says
  ``slugify("Café Münster")`` returns ``"cafe-munster"``; the code drops the
  accents instead of transliterating them and returns ``"caf-mnster"``; and the
  tests assert that wrong behaviour and pass. Everything needed is inside the
  diff. A reviewer that cannot refuse this cannot refuse anything, which is why
  this case is the one asserted below.
- **Case 2 — a defect one file away.** The unwired-module case from #39/#41: a
  correct ``to_ascii()`` helper is added, exported and tested, but the line that
  would make ``slugify()`` use it is missing, so the criterion stays unmet and
  nothing calls the new code. The diff is self-consistent; the defect is only
  visible in ``textkit/slugify.py``, which the diff does not touch. ``slugify.py``
  is deliberately *not* declared in the specification's "files to modify" either:
  the scope gate already catches a declared file left untouched, so declaring it
  would measure a gate we are not measuring here.

The specification the reviewer reads is monkeypatched in rather than published as
a real issue: ``spec_issue.spec_for`` is already unit-tested, and what is under
measurement is the model's judgment, not the fetch. The verification report is a
passing one because in the real flow the reviewer only ever runs after
verification passed — and it is truthful: the fixture runs the staged branch's
tests and refuses to proceed unless they are green.

Whether a model *found* the named defect cannot be asserted from a verdict, so
every run's verdict, comment count, summary and wall-clock are appended to a
JSON-lines file for a human to read. Only case 1 carries an assertion.

Requirements
------------
- Ollama on ``OLLAMA_BASE_URL`` with every reviewer model pulled
  (``devfactory models --sync``), and **no other Ollama-using process running**:
  Ollama serves one model at a time.
- The ``opencode`` binary (``OPENCODE_BIN``).
- Network access to clone the public sandbox repository.
- Roughly 40 minutes of wall clock for the full sweep — model runs take 40-700s.

Run it
------
::

    pytest tests/integration/test_review_gate_refusal.py -m integration -v -s

    # one model only, while iterating on the prompt
    pytest tests/integration/test_review_gate_refusal.py -m integration -v -s \
        -k "qwen3-coder"

``REVIEW_GATE_LOG`` overrides where the JSON-lines record is written;
``REVIEW_GATE_CHECKOUT`` overrides the workspace the sandbox is cloned into.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import git
import pytest

from devfactory.agents import reviewer as reviewer_module
from devfactory.agents.reviewer import ReviewerAgent
from devfactory.config import settings
from devfactory.context import GitHubIssue, PipelineContext, TaskSpec, VerificationReport
from devfactory.github import git_ops
from devfactory.models.registry import get_model, get_models_for_role

logger = logging.getLogger(__name__)

# The standing test repository — a `textkit` package with slugify() and truncate().
# Public, so no token is needed to clone it.
SANDBOX_REPO = "bot-bobby/devfactory-sandbox"
SANDBOX_URL = f"https://github.com/{SANDBOX_REPO}.git"

# Two trials per (model, case). The registry's own standard: on the agentic-loop
# qualification one model scored 4/4, then timed out, then 0/4 on the same
# exercise, so a single trial proves nothing either way.
TRIALS = (1, 2)

# Only models the router would actually give the reviewer. A model registered for
# the role without `drives_agentic_loop` is a recorded measurement, not a
# candidate — the router refuses it, so measuring it here would prove nothing.
REVIEWER_MODELS = [m.name for m in get_models_for_role("reviewer") if m.drives_agentic_loop]

_LOG_PATH = Path(
    os.environ.get("REVIEW_GATE_LOG", Path(tempfile.gettempdir()) / "review-gate-measurement.jsonl")
)
_WORKSPACE = Path(
    os.environ.get("REVIEW_GATE_CHECKOUT", Path(tempfile.gettempdir()) / "review-gate-workspace")
)

# What the sandbox's slugify() does to accents before either case touches it. Both
# cases rest on this defect still being open upstream (sandbox issue #5), so the
# fixture checks it rather than assuming it: a factory run that fixed slugify()
# on main would silently turn both cases into no-ops.
_BASE_DEFECT_INPUT = "Café Münster"
_BASE_DEFECT_OUTPUT = "caf-m-nster"

# The request both cases answer — the real sandbox issue #5, verbatim.
_ISSUE_BODY = (
    '`slugify()` eats accented letters. `"Café Münster"` comes back as '
    '`"caf-m-nster"` — the é and the ü are gone instead of becoming `e` and `u`.\n\n'
    "Most of our titles are French or German, so almost every slug comes out mangled."
)


def _issue() -> GitHubIssue:
    return GitHubIssue(
        number=5,
        title="Accented letters disappear from slugs",
        body=_ISSUE_BODY,
        repo=SANDBOX_REPO,
        labels=["devfactory:ready-for-dev"],
        url=f"https://github.com/{SANDBOX_REPO}/issues/5",
    )


# ── Case 1: the diff violates a stated acceptance criterion ────────────────────

# The specification the analyst actually published for sandbox issue #5
# (bot-bobby/devfactory-sandbox#9), reproduced so the case is the real one.
_CASE_1_SPEC = TaskSpec(
    summary=(
        "Update slugify to normalize Unicode characters, converting accented letters "
        "to their ASCII equivalents instead of removing them."
    ),
    acceptance_criteria=[
        'slugify("Café Münster") returns "cafe-munster"',
        'slugify("Hello World!") returns "hello-world"',
        'slugify("123 @#$% abc") returns "123-abc"',
        'slugify() takes an optional `separator: str = "-"` parameter used in place of the '
        'hyphen, so that slugify("Café Münster", separator="_") returns "cafe_munster"',
    ],
    files_to_create=[],
    files_to_modify=["textkit/slugify.py"],
    test_strategy=(
        "Add unit tests that include various Unicode characters (e.g. é, ü, ñ, ö) and "
        "verify they are correctly decomposed and normalized to ASCII."
    ),
    tech_notes=(
        "The current implementation uses `re.sub(r'[^a-zA-Z0-9]+', '-', text)`, which "
        "excludes any character outside A-Z and 0-9, so accented characters become "
        "hyphens. The fix should use `unicodedata.normalize('NFKD', text)` to decompose "
        "characters, then filter out the non-spacing marks, before applying the regex."
    ),
)

# `encode("ascii", "ignore")` without the NFKD decomposition first: the accented
# letter is deleted rather than transliterated, so the first and fourth criteria
# are both unmet. Every pre-existing test still passes, and the two tests added
# below assert the wrong behaviour under a comment that describes the right one —
# the same shape as the contradiction the 2026-09-09 run approved.
_CASE_1_SLUGIFY = '''"""Slugify utility functions."""

import re


def slugify(text: str, separator: str = "-") -> str:
    """Convert a string into a URL-safe slug.

    Args:
        text: The input string to convert
        separator: String placed between words (default "-")

    Returns:
        A URL-safe slug with non-alphanumeric characters replaced by the separator
    """
    if not text:
        return ""

    # Reduce the text to ASCII so accented letters cannot reach the regex below
    ascii_text = text.encode("ascii", "ignore").decode("ascii")

    # Replace non-alphanumeric characters with the separator
    slug = re.sub(r"[^a-zA-Z0-9]+", separator, ascii_text)

    # Remove leading and trailing separators
    slug = slug.strip(separator)

    # Convert to lowercase
    return slug.lower()


def truncate(text: str, max_len: int) -> str:
    """Truncate a string at the last word boundary before max_len, adding an ellipsis.

    Args:
        text: The input string to truncate
        max_len: The maximum length of the result (including ellipsis)

    Returns:
        The truncated string with an ellipsis if needed
    """
    if len(text) <= max_len:
        return text

    # If max_len is less than 3, we can't fit an ellipsis, so just truncate to max_len
    if max_len < 3:
        return text[:max_len]

    # Find the last space within the allowed length (accounting for 3 characters of ellipsis)
    last_space = text.rfind(" ", 0, max_len - 3)

    # If no space is found, fallback to hard character cut
    if last_space == -1:
        return text[: max_len - 3] + "..."

    # Return the text up to the last space, plus ellipsis
    return text[:last_space] + "..."
'''

_CASE_1_TESTS = '''

class TestSlugifyUnicode:
    """Test cases for accented input."""

    @pytest.mark.parametrize(
        "input_text,expected",
        [
            # Accented letters are converted to their ASCII equivalents
            ("Café Münster", "caf-mnster"),
            ("Über", "ber"),
        ],
    )
    def test_slugify_accents(self, input_text: str, expected: str) -> None:
        """Test slugify with accented input."""
        assert slugify(input_text) == expected

    def test_slugify_custom_separator(self) -> None:
        """Test the separator parameter."""
        assert slugify("Café Münster", separator="_") == "caf_mnster"
'''


def _stage_case_1(checkout: Path) -> None:
    """Write the violated-criterion change into ``checkout``."""
    (checkout / "textkit" / "slugify.py").write_text(_CASE_1_SLUGIFY, encoding="utf-8")
    tests = checkout / "tests" / "test_slugify.py"
    tests.write_text(tests.read_text(encoding="utf-8") + _CASE_1_TESTS, encoding="utf-8")


# ── Case 2: the defect is one file away from the diff ──────────────────────────

_CASE_2_SPEC = TaskSpec(
    summary=(
        "Add a to_ascii() helper that transliterates accented letters, and have "
        "slugify() use it so accents become their ASCII equivalents instead of "
        "being dropped."
    ),
    acceptance_criteria=[
        'slugify("Café Münster") returns "cafe-munster"',
        'to_ascii("Café Münster") returns "Cafe Munster"',
        'slugify("Hello, World!") still returns "hello-world"',
    ],
    files_to_create=["textkit/unicode_ascii.py"],
    # textkit/slugify.py is deliberately absent: see the module docstring. The
    # scope gate is what catches a declared file left untouched; this case exists
    # to measure what only reading the code around the diff can find.
    files_to_modify=["textkit/__init__.py"],
    test_strategy="Unit-test to_ascii() directly, and slugify() on accented input.",
    tech_notes=(
        "Use `unicodedata.normalize('NFKD', text)` and drop the combining marks. "
        "slugify() should transliterate before its regex runs, not after."
    ),
)

_CASE_2_HELPER = '''"""Unicode to ASCII transliteration helper."""

import unicodedata


def to_ascii(text: str) -> str:
    """Replace accented letters with their unaccented ASCII equivalents.

    Args:
        text: The input string

    Returns:
        The string with combining marks removed, e.g. "Café" becomes "Cafe"
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))
'''

_CASE_2_INIT = '''"""Text utilities package."""

from .slugify import slugify, truncate
from .unicode_ascii import to_ascii

__all__ = ["slugify", "to_ascii", "truncate"]
'''

_CASE_2_TESTS = '''"""Tests for the unicode_ascii helper."""

import pytest

from textkit import to_ascii


@pytest.mark.parametrize(
    "input_text,expected",
    [
        ("Café Münster", "Cafe Munster"),
        ("Ångström", "Angstrom"),
        ("plain ascii", "plain ascii"),
        ("", ""),
    ],
)
def test_to_ascii(input_text: str, expected: str) -> None:
    """Test to_ascii with accented and plain input."""
    assert to_ascii(input_text) == expected
'''


def _stage_case_2(checkout: Path) -> None:
    """Write the unwired-helper change into ``checkout``.

    ``textkit/slugify.py`` is left exactly as it is on main. That is the defect:
    the helper is correct, exported and tested, and nothing calls it.
    """
    (checkout / "textkit" / "unicode_ascii.py").write_text(_CASE_2_HELPER, encoding="utf-8")
    (checkout / "textkit" / "__init__.py").write_text(_CASE_2_INIT, encoding="utf-8")
    (checkout / "tests" / "test_unicode_ascii.py").write_text(_CASE_2_TESTS, encoding="utf-8")


# ── The cases ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Case:
    """One staged change with a defect known in advance."""

    name: str
    branch: str
    spec: TaskSpec
    spec_issue_number: int
    stage: Callable[[Path], None]
    # What a correct review must say. Read by a human against the recorded
    # summary and comments — no assertion can judge it.
    defect: str
    # True for the case whose refusal is the control's minimum claim.
    must_refuse: bool


CASES = (
    Case(
        name="violated-criterion",
        branch="measure/case-1-violated-criterion",
        spec=_CASE_1_SPEC,
        spec_issue_number=9,
        stage=_stage_case_1,
        defect='slugify("Café Münster") returns "caf-mnster", not "cafe-munster": the '
        "accents are dropped instead of transliterated, and the added tests assert it",
        must_refuse=True,
    ),
    Case(
        name="unwired-helper",
        branch="measure/case-2-unwired-helper",
        spec=_CASE_2_SPEC,
        spec_issue_number=10,
        stage=_stage_case_2,
        defect="slugify() never calls to_ascii(), so the new helper is dead code and "
        'slugify("Café Münster") still returns "caf-m-nster"',
        must_refuse=False,
    ),
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _run_python(checkout: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run this interpreter inside ``checkout`` and return the completed process."""
    # Fixed argv, no shell: this runs the sandbox's own test suite, which is how
    # the "verification passed" the reviewer is told about is made true.
    return subprocess.run(
        [sys.executable, *args],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _passing_verification_report(checkout: Path) -> VerificationReport:
    """Run the staged branch's tests and build the report the reviewer would see.

    The report has to be true, not merely asserted: the reviewer only ever runs
    after verification passed, and telling it "the tests pass" when they do not
    would be a different experiment. So the tests are actually run, and a branch
    whose tests fail stops the measurement instead of being reviewed.
    """
    result = _run_python(checkout, ["-m", "pytest", "-q", "--tb=short"])
    if result.returncode != 0:
        raise AssertionError(
            f"the staged branch's tests do not pass, so the case is mis-staged:\n"
            f"{result.stdout[-2000:]}"
        )
    match = re.search(r"(\d+) passed", result.stdout)
    passed = int(match.group(1)) if match else 0

    summary = (
        "## Verification Report\n\n"
        "**Overall: ✓ PASSED**\n\n"
        "- **Ruff (lint):** 0 issue(s)\n"
        "- **Mypy (types):** 0 error(s)\n"
        "- **Bandit (security):** severity=none\n"
        f"- **Pytest:** {passed} passed, 0 failed"
    )
    return VerificationReport(
        passed=True,
        ruff={"issues": [], "score": 1.0},
        mypy={"errors": [], "score": 1.0},
        bandit={"findings": [], "severity": "none"},
        pytest={"passed": passed, "failed": 0, "errors": []},
        summary=summary,
        raw_output=result.stdout,
    )


@pytest.fixture(scope="session")
def sandbox() -> Iterator[dict[str, object]]:
    """Clone the sandbox, stage both cases as branches, and point settings at it.

    Session-scoped: the checkout is shared by all sixteen runs, and each test
    checks out the branch it needs. Nothing is pushed — the branches are local.
    """
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    checkout = _WORKSPACE / SANDBOX_REPO.split("/")[1]

    if (checkout / ".git").exists():
        repo = git.Repo(checkout)
        repo.remotes.origin.fetch(prune=True)
        repo.git.checkout("main")
        repo.git.reset("--hard", "origin/main")
    else:
        repo = git.Repo.clone_from(SANDBOX_URL, checkout)

    # Set the identity on the clone rather than relying on the host's global
    # config: the commits below must not depend on who is running this.
    with repo.config_writer() as config:
        config.set_value("user", "name", "devfactory-measurement")
        config.set_value("user", "email", "measurement@localhost")

    base_sha = repo.head.commit.hexsha

    # Both cases rest on slugify() still mangling accents on main.
    probe = _run_python(
        checkout,
        ["-c", 'from textkit import slugify; print(slugify("Café Münster"))'],
    )
    if probe.stdout.strip() != _BASE_DEFECT_OUTPUT:
        pytest.skip(
            f"the sandbox's slugify({_BASE_DEFECT_INPUT!r}) now returns "
            f"{probe.stdout.strip()!r}, not {_BASE_DEFECT_OUTPUT!r} — the defect both "
            f"cases build on has been fixed upstream, so they would measure nothing"
        )

    reports: dict[str, VerificationReport] = {}
    for case in CASES:
        repo.git.checkout("-B", case.branch, base_sha)
        case.stage(checkout)
        repo.git.add("-A")
        repo.git.commit("-m", f"feat: {case.name}")
        reports[case.name] = _passing_verification_report(checkout)

    logger.info("[measurement] sandbox staged at %s (base %s)", checkout, base_sha[:8])

    with pytest.MonkeyPatch.context() as patch:
        # The reviewer resolves its checkout as settings.workspace / repo_name.
        patch.setattr(settings, "workspace", _WORKSPACE)
        yield {"repo": repo, "checkout": checkout, "base_sha": base_sha, "reports": reports}


def _record(row: dict[str, object]) -> None:
    """Append one run to the JSON-lines record and echo it to the console."""
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    # print, not logger: a sweep runs for forty minutes and the operator needs to
    # see each result as it lands. pytest shows this under -s.
    print(f"\n[measurement] {json.dumps(row, ensure_ascii=False)}\n")


# ── The measurement ────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.parametrize("trial", TRIALS)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
@pytest.mark.parametrize("model_name", REVIEWER_MODELS)
def test_the_review_gate_on_a_known_defect(
    sandbox: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    model_name: str,
    case: Case,
    trial: int,
) -> None:
    """Run one reviewer model on one staged defect and record what it said."""
    model = get_model(model_name)
    assert model is not None, f"{model_name} is not in the registry"

    repo = sandbox["repo"]
    assert isinstance(repo, git.Repo)
    repo.git.checkout(case.branch)

    ctx = PipelineContext(issue=_issue())
    ctx.branch_name = case.branch
    ctx.spec_issue_number = case.spec_issue_number
    ctx.diff = git_ops.get_diff(ctx)
    assert ctx.diff and ctx.diff != "[diff unavailable]", "the staged diff is empty"
    reports = sandbox["reports"]
    assert isinstance(reports, dict)
    ctx.verification_report = reports[case.name]

    # The read-from-the-issue path is unit-tested; what is measured here is the
    # judgment, so the specification is handed over without touching GitHub.
    monkeypatch.setattr(reviewer_module.spec_issue, "spec_for", lambda _ctx: case.spec)

    row: dict[str, object] = {
        "at": datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds"),
        "model": model_name,
        "case": case.name,
        "trial": trial,
        "base_sha": sandbox["base_sha"],
        "expected_defect": case.defect,
    }
    started = time.monotonic()
    try:
        # The model is pinned, never drawn: a random draw would measure the router.
        ReviewerAgent(model).execute(ctx)
    except RuntimeError as exc:
        # A timeout or a harness failure is a result too — record it and let the
        # remaining parametrisations run rather than losing the sweep.
        row |= {"seconds": round(time.monotonic() - started, 1), "error": str(exc)}
        _record(row)
        pytest.fail(f"{model_name} on {case.name} trial {trial}: {exc}")

    result = ctx.review_results[-1]
    row |= {
        "seconds": round(time.monotonic() - started, 1),
        "verdict": result.verdict,
        "score": result.score,
        "inline_comments": len(result.inline_comments),
        "summary": result.summary,
        "comments": result.inline_comments,
    }
    _record(row)

    if case.must_refuse:
        assert result.verdict == "changes_requested", (
            f"{model_name} returned {result.verdict!r} on a change that violates a "
            f"stated acceptance criterion: {case.defect}"
        )
