from __future__ import annotations

import pytest

from devfactory.github.diff_utils import truncate_diff


def test_short_diff():
    diff = "some diff content"
    assert truncate_diff(diff, 20_000) == diff


def test_diff_at_boundary_exactly():
    diff = "12345"
    assert truncate_diff(diff, 5) == diff


def test_empty_diff():
    assert truncate_diff("", 20_000) == ""


def test_no_markers_truncation():
    diff = "no markers here"
    assert truncate_diff(diff, 10) == "no markers"


def test_multi_file_diff_truncation():
    diff = (
        "diff --git a/file1.py b/file1.py\n"
        "index 123..456 100644\n"
        "--- a/file1.py\n"
        "+++ b/file1.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/file2.py b/file2.py\n"
        "index 222..333 100644\n"
        "--- a/file2.py\n"
        "+++ b/file2.py\n"
        "@@ -1 +1 @@\n"
        "-old2\n"
        "+new2\n"
        "diff --git a/file3.py b/file3.py\n"
        "index 444..555 100644\n"
        "--- a/file3.py\n"
        "+++ b/file3.py\n"
        "@@ -1 +1 @@\n"
        "-old3\n"
        "+new3\n"
    )
    # max_chars is small enough to truncate after the first file
    # The first section is file1.py. The second is file2.py. The third is file3.py.
    # We want to include only the first section.
    # Total length of first section is approx 80 chars.
    # Let's set max_chars to 100.
    result = truncate_diff(diff, 100)
    assert "file1.py" in result
    assert "file2.py" not in result
    assert "file3.py" not in result
    assert "[... 2 more file(s) omitted: file2.py, file3.py ...]" in result


def test_single_file_too_large():
    diff = (
        "diff --git a/file1.py b/file1.py\n"
        "index 123..456 100644\n"
        "--- a/file1.py\n"
        "+++ b/file1.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/file2.py b/file2.py\n"
        "index 222..333 100644\n"
        "--- a/file2.py\n"
        "+++ b/file2.py\n"
        "@@ -1 +1 @@\n"
        "-old2\n"
        "+new2\n"
    )
    # max_chars is very small
    result = truncate_diff(diff, 20)
    assert len(result) <= 20 + 100  # len(footer) is around 60
    assert result.startswith("diff --git a/file1.py")
    assert "[... 1 more file(s) omitted: file2.py ...]" in result


def test_footer_format():
    diff = "diff --git a/f1 b/f1\ndiff --git a/f2 b/f2\ndiff --git a/f3 b/f3\n"
    result = truncate_diff(diff, 30)
    assert "[... 2 more file(s) omitted: f2, f3 ...]" in result


@pytest.mark.parametrize("max_chars", [10, 50, 100])
def test_no_exception_with_various_limits(max_chars):
    diff = "diff --git a/f1 b/f1\ncontent\ndiff --git a/f2 b/f2\ncontent"
    # Should not raise error
    result = truncate_diff(diff, max_chars)
    assert isinstance(result, str)
