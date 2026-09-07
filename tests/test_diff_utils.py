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
    # file1 is shown, truncated, so it is not omitted — file2 and file3 are. The
    # original assertion said 1, which only held because a partially included file
    # was also counted as omitted; the comment above it already said 2.
    assert "[... 2 more file(s) omitted:" in result


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


def _section(name: str, lines: int) -> str:
    body = "+x\n" * lines
    return f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n{body}"


_STAT = " devfactory/a.py | 2 +-\n devfactory/b.py | 3 +++\n 2 files changed\n\n"


def test_the_stat_summary_survives_truncation():
    """`get_diff` asks git for `--stat -p`, so the diff opens with a summary of
    every file it touches. That summary is exactly what a reviewer needs once the
    body has been cut — dropping it removes the part that says what is missing."""
    diff = _STAT + _section("a.py", 200) + _section("b.py", 200)

    result = truncate_diff(diff, 900)

    assert "2 files changed" in result
    assert "devfactory/b.py |" in result


def test_a_partially_included_file_is_not_reported_as_omitted():
    """One oversized file: it is truncated and shown. Listing it as omitted as
    well would contradict the diff the reader is looking at."""
    diff = _section("big.py", 500)

    result = truncate_diff(diff, 300)

    assert "big.py" in result
    assert "omitted" not in result


def test_files_after_a_partially_included_one_are_still_reported():
    diff = _section("big.py", 500) + _section("next.py", 10)

    result = truncate_diff(diff, 300)

    assert "1 more file(s) omitted: next.py" in result
