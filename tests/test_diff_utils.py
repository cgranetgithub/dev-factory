"""
Unit tests for diff utilities.
"""

from devfactory.github.diff_utils import truncate_diff


def test_truncate_diff_under_limit():
    """Test that diff under limit is returned unchanged."""
    diff = "diff --git a/file1.py b/file1.py\nindex 123..456\n--- a/file1.py\n+++ b/file1.py\n@@ -1,3 +1,3 @@\n-foo\n+bar\n"
    result = truncate_diff(diff, max_chars=1000)
    assert result == diff


def test_truncate_diff_multiple_files():
    """Test truncation between file sections."""
    # Create a diff that spans multiple files
    diff1 = "diff --git a/file1.py b/file1.py\nindex 123..456\n--- a/file1.py\n+++ b/file1.py\n@@ -1,3 +1,3 @@\n-foo\n+bar\n"
    diff2 = "diff --git a/file2.py b/file2.py\nindex 789..012\n--- a/file2.py\n+++ b/file2.py\n@@ -1,2 +1,2 @@\n-baz\n+qux\n"
    diff3 = "diff --git a/file3.py b/file3.py\nindex 345..678\n--- a/file3.py\n+++ b/file3.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"

    diff = diff1 + diff2 + diff3

    # Truncate after file1
    result = truncate_diff(diff, max_chars=100)
    # Should contain the first file but not the others
    assert "diff --git a/file1.py" in result
    assert "diff --git a/file2.py" not in result
    assert "diff --git a/file3.py" not in result
    # Should have ommission indication if needed
    assert (
        "[... 2 more file(s) omitted ...]" in result
        or "[... diff truncated for context limit ...]" in result
    )


def test_truncate_diff_single_file_exceeds_limit():
    """Test truncation of single file that exceeds limit."""
    # Create a large diff that exceeds the limit with just one file
    large_content = "line\n" * 1000
    diff = (
        "diff --git a/large.py b/large.py\nindex 123..456\n--- a/large.py\n+++ b/large.py\n@@ -1,1000 +1,1000 @@\n"
        + large_content
    )

    result = truncate_diff(diff, max_chars=200)
    # Should not exceed max_chars (accounting for footer)
    assert len(result) <= 300  # Allow some buffer
    assert "[... diff truncated for context limit ...]" in result


def test_truncate_diff_no_markers():
    """Test truncation of diff with no 'diff --git ' markers."""
    diff = "some random content without any git diff markers\nthis is just text\nwe can make it quite long\nto exceed the max chars"

    result = truncate_diff(diff, max_chars=50)
    # The test is to make sure we didn't crash
    assert "[... diff truncated for context limit ...]" in result


def test_truncate_diff_empty_string():
    """Test truncation of empty string."""
    result = truncate_diff("", max_chars=100)
    assert result == ""


def test_truncate_diff_exact_limit():
    """Test when diff length is exactly the limit."""
    diff = "diff --git a/file.py b/file.py\nindex 123..456\n--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,3 @@\n-foo\n+bar\n"
    result = truncate_diff(diff, max_chars=len(diff))
    assert result == diff
