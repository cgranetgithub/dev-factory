"""Tests for diff utility functions.

The fixtures below are real unified diffs: hunk headers state the number of source
and target lines the body actually contains. They used not to, which went unnoticed
while the parsing was hand-written string work and does not survive #73.
"""

from devfactory.github.diff_utils import truncate_diff


def test_truncate_diff_under_limit():
    """Test that diff is unchanged when under the character limit."""
    diff = """diff --git a/file1.py b/file1.py
index 1234567..7654321 100644
--- a/file1.py
+++ b/file1.py
@@ -1,2 +1,2 @@
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
@@ -1,2 +1,2 @@
 def hello():
-    print("Hello")
+    print("Hello World")

diff --git a/file2.py b/file2.py
index abcdef0..fedcba0 100644
--- a/file2.py
+++ b/file2.py
@@ -1,2 +1,2 @@
 def bye():
-    print("Bye")
+    print("Bye World")

diff --git a/file3.py b/file3.py
index 1234567..7654321 100644
--- a/file3.py
+++ b/file3.py
@@ -1,2 +1,2 @@
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
    return (
        f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n@@ -0,0 +1,{lines} @@\n{body}"
    )


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


# All three come from `git diff` on a scratch repository, verbatim.
_RENAME = (
    "diff --git a/oldname.py b/newname.py\n"
    "similarity index 100%\n"
    "rename from oldname.py\n"
    "rename to newname.py\n"
)
_BINARY = (
    "diff --git a/img.png b/img.png\n"
    "index 6735744..6392b5f 100644\n"
    "Binary files a/img.png and b/img.png differ\n"
)
_NO_NEWLINE = (
    "diff --git a/nonl.txt b/nonl.txt\n"
    "index 66455a1..250eaab 100644\n"
    "--- a/nonl.txt\n"
    "+++ b/nonl.txt\n"
    "@@ -1,3 +1,3 @@\n"
    " x\n"
    "-y\n"
    "+Y\n"
    " z\n"
    "\\ No newline at end of file\n"
)


def test_a_rename_is_one_file_and_keeps_its_new_name():
    """A rename has two names and, when nothing else changed, no hunks at all.
    It is still one file, and the reviewer should be told the name it now has."""
    diff = _RENAME + _section("big.py", 500)

    result = truncate_diff(diff, 200)

    assert "1 more file(s) omitted: big.py" in result
    assert "rename to newname.py" in result


def test_a_binary_file_is_one_file_and_is_named_in_the_footer():
    diff = _section("big.py", 500) + _BINARY

    result = truncate_diff(diff, 300)

    assert "1 more file(s) omitted: img.png" in result


def test_the_no_newline_marker_does_not_start_a_new_file():
    """The marker sits below the last hunk line, where a naive scan for the next
    file boundary could easily leave it behind or attach it to the wrong file."""
    diff = _NO_NEWLINE + _section("big.py", 500)

    result = truncate_diff(diff, 250)

    assert "\\ No newline at end of file" in result
    assert "1 more file(s) omitted: big.py" in result


def test_diff_text_inside_a_diff_is_not_mistaken_for_a_file_boundary():
    """A commit that adds a `.diff` file contains lines beginning with
    `diff --git` — as hunk content, not as headers. Splitting the text on that
    marker reported three files the commit never touched."""
    nested_body = "".join(
        f"+{line}\n"
        for line in (
            "diff --git a/keep.py b/keep.py",
            "--- a/keep.py",
            "+++ b/keep.py",
            "@@ -1,1 +1,1 @@",
            "-a",
            "+b",
        )
    )
    diff = (
        "diff --git a/sample.diff b/sample.diff\n"
        "--- a/sample.diff\n+++ b/sample.diff\n"
        f"@@ -0,0 +1,6 @@\n{nested_body}"
    ) + _section("real.py", 500)

    result = truncate_diff(diff, 400)

    assert "1 more file(s) omitted: real.py" in result
    assert "keep.py" not in result.split("[...")[-1]


def test_an_unparseable_diff_is_still_cut_on_a_line_boundary():
    """Truncation is best effort: an oversized prompt is worse than a short one,
    but a line cut in half is what #60 was filed for."""
    # The hunk header promises five lines on each side and the body has one.
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,5 +1,5 @@\n" + "+some text\n" * 200
    )

    result = truncate_diff(diff, 100)

    assert len(result) <= 100
    assert result.endswith("+some text")
