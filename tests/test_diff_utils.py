from __future__ import annotations

import pytest

from devfactory.github.diff_utils import truncate_diff


def test_truncate_diff_short_diff():
    diff = "some diff content"
    assert truncate_diff(diff, 20000) == diff


def test_truncate_diff_empty_string():
    assert truncate_diff("", 20000) == ""


def test_truncate_diff_no_markers():
    diff = "plain text content without markers"
    # max_chars is 10, so it should be truncated to 10 chars
    # Since no markers found, it's one single section (the preamble).
    assert truncate_diff(diff, 10) == diff[:10]


def test_truncate_diff_boundary_exact_max_chars():
    diff = "12345"
    assert truncate_diff(diff, 5) == diff


def test_truncate_diff_multi_file_truncation():
    s1 = "diff --git a/file1 b/file1\nindex 000..111 10064ss\n--- a/file1\n+++ b/file1\n+line1\n"
    s2 = "diff --git a/file2 b/file2\nindex 000..222 10064ss\n--- a/file2\n+++ b/file2\n+line2\n"
    s3 = "diff --git a/file3 b/file3\nindex 000..333 10064ss\n--- a/file3\n+++ b/file3\n+line3\n"
    diff = s1 + s2 + s3

    # Max chars allows s1 and s2, but not s3.
    max_chars = len(s1) + len(s2)
    result = truncate_diff(diff, max_chars)

    assert s1 in result
    assert s2 in result
    assert s3 not in result
    assert "[... 1 more file(s) omitted: file3 ...]" in result


def test_truncate_diff_single_file_too_large():
    s1 = "diff --git a/file1 b/file1\nindex 000..111 10064ss\n--- a/file1\n+++ b/file1\n+line1\n"
    s2 = "diff --git a/file2 b/file2\nindex 000..222 10064ss\n--- a/file2\n+++ b/file2\n+line2\n"
    diff = s1 + s2
    # max_chars = 10. First section is > 10.
    result = truncate_diff(diff, 10)
    assert result == s1[:10] + "\n\n[... 1 more file(s) omitted: file2 ...]"


@pytest.mark.parametrize(
    "diff, max_chars, expected_contains, expected_omitted",
    [
        (
            "diff --git a/f1 b/f1\n+1\ndiff --git a/f2 b/n/f2\n+2\ndiff --git a/f3 b/f3\n+3\n",
            50,
            "diff --git a/f1 b/f1",
            "f3",
        ),
        ("diff --git a/f1 b/f1\n+1\ndiff --git a/f2 b/f2\n+2\n", 100, "diff --git a/f2 b/f2", ""),
    ],
)
def test_truncate_diff_parametrized(diff, max_chars, expected_contains, expected_omitted):
    result = truncate_diff(diff, max_chars)
    assert expected_contains in result
    if expected_omitted:
        assert expected_omitted in result
