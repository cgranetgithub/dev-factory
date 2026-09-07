"""
Tests for the scope gate — did the change touch the files the task declared?

The gate exists because two pull requests reached a human with a feature that is
never called: the new code was written, the one line wiring it in was not. Every
other gate passed those, so the cases below are written from those failures.
"""

from __future__ import annotations

from devfactory.verification.scope import ScopeReport, check_scope


def test_a_change_touching_everything_declared_is_satisfied():
    report = check_scope(
        declared=["devfactory/github/diff_utils.py", "devfactory/github/git_ops.py"],
        changed=["devfactory/github/diff_utils.py", "devfactory/github/git_ops.py"],
    )

    assert report.satisfied
    assert report.missing == []
    assert report.unexpected == []


def test_the_unwired_module_case():
    """PR #41: the helper and its tests were written, git_ops.py never touched."""
    report = check_scope(
        declared=[
            "devfactory/github/diff_utils.py",
            "devfactory/github/git_ops.py",
            "tests/test_diff_utils.py",
        ],
        changed=["devfactory/github/diff_utils.py", "tests/test_diff_utils.py"],
    )

    assert not report.satisfied
    assert report.missing == ["devfactory/github/git_ops.py"]


def test_the_missing_tests_case():
    """PR #39: the feature was written, the tests the task demanded were not."""
    report = check_scope(
        declared=["devfactory/agents/developer.py", "tests/test_developer_fallback.py"],
        changed=["devfactory/agents/developer.py", "devfactory/config.py"],
    )

    assert not report.satisfied
    assert "tests/test_developer_fallback.py" in report.missing


def test_extra_files_are_reported_but_never_block():
    """An analyst cannot foresee every file a correct change needs, so an extra
    file is worth reporting and never worth blocking on."""
    report = check_scope(
        declared=["devfactory/config.py"],
        changed=["devfactory/config.py", "README.md", "CONTRIBUTING.md"],
    )

    assert report.satisfied
    assert report.unexpected == ["CONTRIBUTING.md", "README.md"]


def test_an_empty_declaration_checks_nothing():
    """With no plan there is nothing to check. Inventing a violation from an absent
    file list would block every run whose analyst returned none."""
    report = check_scope(declared=[], changed=["anything.py"])

    assert report.satisfied
    assert report.missing == []
    assert report.unexpected == []


def test_paths_compare_equal_despite_decoration():
    """Models quote and prefix paths; "`./a.py`" and "a.py" are the same file."""
    report = check_scope(
        declared=["`./devfactory/a.py`", " tests/b.py "], changed=["devfactory/a.py", "tests/b.py"]
    )

    assert report.satisfied


def test_summary_names_the_files_not_the_rule():
    """The developer acts on a filename, not on a policy statement."""
    report = ScopeReport(missing=["devfactory/github/git_ops.py"], unexpected=["README.md"])

    summary = report.summary()

    assert "devfactory/github/git_ops.py" in summary
    assert "README.md" in summary
    assert "dead code" in summary


def test_summary_is_empty_when_there_is_nothing_to_say():
    assert ScopeReport().summary() == ""
