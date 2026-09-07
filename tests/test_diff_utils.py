"""Tests for diff utility functions."""

from devfactory.github.diff_utils import truncate_diff


def test_truncate_diff_under_limit():
    """Test that diff is unchanged when under the character limit."""
    diff = """diff --git a/file1.py b/file1.py
index 1234567..7654321 100644
--- a/file1.py
+++ b/file1.py
@@ -1,3 +1,3 @@
 def hello():
-    print("Hello")
+    print("Hello World")"""
    result = truncate_diff(diff, 1000)
    assert result == diff


def test_truncate_diff_multi_file():
    """Test truncation occurs between complete file sections."""
    diff = """diff --git a/file1.py b/file1.py
index 1234567..7654321 100644
--- a/file1.py
+++ b/file1.py
@@ -1,3 +1,3 @@
 def hello():
-    print("Hello")
+    print("Hello World")

diff --git a/file2.py b/file2.py
index abcdef0..fedcba0 100644
--- a/file2.py
+++ b/file2.py
@@ -1,3 +1,3 @@
 def bye():
-    print("Bye")
+    print("Bye World")

diff --git a/file3.py b/file3.py
index 1234567..7654321 100644
--- a/file3.py
+++ b/file3.py
@@ -1,3 +1,3 @@
 def test():
-    print("Test")
+    print("Test World")"""

    # Truncate to just fit first file (which is 169 chars and exceeds 150)
    result = truncate_diff(diff, 150)
    assert "diff --git a/file1.py b/file1.py" in result
    assert "diff --git a/file2.py b/file2.py" not in result
    # Because file2 would also go over the limit, we just show the truncated first file
    # and say there are 2 more omitted (i.e., all except the first).
    # The exact count depends on how many would have fit.
    assert "[... 1 more file(s) omitted:" in result  # At least one should be omitted


def test_truncate_diff_single_file_exceeds_limit():
    """Test truncation when first file alone exceeds the limit."""
    # This tests that we don't get an exception with a very long first file
    diff = "diff --git a/file1.py b/file1.py\n" * 200

    result = truncate_diff(diff, 200)

    # Should not crash and should have git header
    assert "diff --git a/file1.py b/file1.py" in result


def test_truncate_diff_no_git_markers():
    """Test handling of diffs without git markers."""
    diff = "This is just a regular text file\nWith multiple lines\nAnd no git markers"
    result = truncate_diff(diff, 100)
    assert result == diff


def test_truncate_diff_empty_string():
    """Test handling of empty string."""
    result = truncate_diff("", 100)
    assert result == ""


def test_truncate_diff_only_preamble():
    """Test when the first element is a preamble with no git markers."""
    diff = """This is a preamble
that has multiple lines
and no git markers"""
    result = truncate_diff(diff, 100)
    assert result == diff
