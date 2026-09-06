"""
Unit tests for GitHub review functionality in devfactory/github/review.py
"""

import unittest.mock as mock

from github import GithubException

from devfactory.context import PipelineContext, ReviewResult
from devfactory.github.review import (
    VERDICT_MAP,
    _build_diff_position_map,
    _build_review_body,
    _build_review_comments,
    post_review,
)


def test_verdict_mapping():
    """Test that verdicts are mapped to the correct review events."""

    # Test all verdict mappings
    assert VERDICT_MAP["approved"] == "APPROVE"
    assert VERDICT_MAP["changes_requested"] == "REQUEST_CHANGES"
    assert VERDICT_MAP["commented"] == "COMMENT"
    # Test default mapping
    assert VERDICT_MAP.get("unknown", "COMMENT") == "COMMENT"


def test_build_diff_position_map_single_hunk():
    """Test that _build_diff_position_map correctly maps lines in a single hunk."""

    # Mock a file with a simple diff
    mock_file = mock.MagicMock()
    mock_file.filename = "test.py"
    mock_file.patch = """@@ -1,3 +1,4 @@
 line1
-line2
+line2 modified
 line3
+line4 added
"""

    mock_pr = mock.MagicMock()
    mock_pr.get_files.return_value = [mock_file]

    result = _build_diff_position_map(mock_pr)

    # Should correctly map line numbers to diff positions
    assert "test.py" in result
    line_map = result["test.py"]
    # Line 1 in file corresponds to diff position 2 (header line)
    # Line 2 in file corresponds to diff position 4 (line2 modified)
    # Line 3 in file corresponds to diff position 5 (line3)
    # Line 4 in file corresponds to diff position 6 (line4 added)
    assert line_map[1] == 2  # line1 is context line
    assert line_map[2] == 4  # line2 modified
    assert line_map[3] == 5  # line3
    assert line_map[4] == 6  # line4 added


def test_build_diff_position_map_multiple_hunks():
    """Test that _build_diff_position_map correctly handles multiple hunks."""

    mock_file = mock.MagicMock()
    mock_file.filename = "test.py"
    mock_file.patch = """@@ -1,3 +1,4 @@
 line1
-line2
+line2 modified
 line3
@@ -10,3 +11,4 @@
 line10
-line11
+line11 modified
 line12
"""

    mock_pr = mock.MagicMock()
    mock_pr.get_files.return_value = [mock_file]

    result = _build_diff_position_map(mock_pr)

    assert "test.py" in result
    line_map = result["test.py"]
    # First hunk: lines 1,2,3 map to diff positions 2,4,5 (line1, line2 modified, line3)
    # Second hunk: lines 11,12,13 map to diff positions 7,9,10 (line10, line11 modified, line12)
    assert line_map[1] == 2  # line1 (context)
    assert line_map[2] == 4  # line2 (modified)
    assert line_map[3] == 5  # line3 (context)
    # The second hunk starts with new line 11 (from the second hunk header)
    assert (
        line_map[11] == 7
    )  # line10 after first hunk (because hunk header resets current_line to 10)
    assert line_map[12] == 9  # line11 (modified) after second hunk
    assert line_map[13] == 10  # line12 (context) after second hunk


def test_build_diff_position_map_skips_none_patch():
    """Test that files with None patch are skipped without raising exception."""

    mock_file = mock.MagicMock()
    mock_file.filename = "test.py"
    mock_file.patch = None

    mock_pr = mock.MagicMock()
    mock_pr.get_files.return_value = [mock_file]

    result = _build_diff_position_map(mock_pr)

    # Should not include the file with None patch
    assert "test.py" not in result


def test_build_diff_position_map_handles_missing_lines():
    """Test that _build_diff_position_map handles comments on lines not in diff."""

    mock_file = mock.MagicMock()
    mock_file.filename = "test.py"
    mock_file.patch = """@@ -1,3 +1,4 @@
 line1
-line2
+line2 modified
 line3
"""

    mock_pr = mock.MagicMock()
    mock_pr.get_files.return_value = [mock_file]

    result = _build_diff_position_map(mock_pr)

    assert "test.py" in result
    line_map = result["test.py"]
    # Line 20 doesn't exist in the diff
    assert line_map.get(20) is None


def test_build_review_body():
    """Test that review body is formatted correctly."""

    result = ReviewResult(
        model="test-model",
        verdict="approved",
        summary="Good code quality",
        inline_comments=[],
        score=0.9,
    )

    body = _build_review_body(result)
    expected = (
        "**Model:** `test-model`\n"
        "**Quality score:** 0.9/1.0\n"
        "**Verdict:** approved\n\n"
        "Good code quality"
    )

    assert body == expected


def test_build_review_comments():
    """Test that review comments are built correctly."""

    # Mock a diff position mapping
    mock_pr = mock.MagicMock()
    diff_map = {
        "test.py": {
            10: 5,  # line 10 in file maps to diff position 5
            15: 12,  # line 15 in file maps to diff position 12
        }
    }

    # Patch the method that would be called
    with mock.patch("devfactory.github.review._build_diff_position_map", return_value=diff_map):
        inline_comments = [
            {"path": "test.py", "line": 10, "body": "Fix this issue", "model": "test-model"},
            {"path": "test.py", "line": 15, "body": "Another issue", "model": "test-model"},
        ]

        comments = _build_review_comments(mock_pr, inline_comments)

        assert len(comments) == 2
        assert comments[0]["path"] == "test.py"
        assert comments[0]["position"] == 5
        assert "Fix this issue" in comments[0]["body"]


def test_build_review_comments_missing_line_in_diff():
    """Test that review comments fall back to position=1 for lines not in diff."""

    mock_pr = mock.MagicMock()
    diff_map = {
        "test.py": {
            10: 5,  # line 10 in file maps to diff position 5
        }
    }

    # Simulate line 20 being missing from diff
    with mock.patch("devfactory.github.review._build_diff_position_map", return_value=diff_map):
        inline_comments = [
            {
                "path": "test.py",
                "line": 20,  # This line is not in diff
                "body": "Issue on missing line",
                "model": "test-model",
            }
        ]

        comments = _build_review_comments(mock_pr, inline_comments)

        # Should fall back to position=1
        assert comments[0]["position"] == 1


def test_post_review_success():
    """Test that post_review works correctly when all inputs are valid."""

    mock_repo = mock.MagicMock()
    mock_issue = mock.MagicMock()
    mock_repo.get_issue.return_value = mock_issue
    mock_issue.create_comment.return_value = mock.MagicMock()

    mock_pr = mock.MagicMock()
    mock_pr.create_review.return_value = mock.MagicMock()
    mock_repo.get_pull.return_value = mock_pr

    mock_gh = mock.MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    # Mock the context
    ctx = PipelineContext(
        issue=mock.MagicMock(),
        pr_number=123,
        diff="",
        task_spec=None,
        verification_report=None,
        review_results=[],
    )

    result = ReviewResult(
        model="test-model",
        verdict="approved",
        summary="Good code quality",
        inline_comments=[],
        score=0.9,
    )

    # Patch the github client and run
    with mock.patch("devfactory.github.review.gh", mock_gh):
        post_review(ctx, result)

    # The review was created successfully
    mock_pr.create_review.assert_called_once()


def test_post_review_fallback_to_issue_comment():
    """Test that post_review falls back to create_issue_comment when create_review fails."""

    mock_repo = mock.MagicMock()
    mock_issue = mock.MagicMock()
    mock_repo.get_issue.return_value = mock_issue
    mock_issue.create_comment.return_value = mock.MagicMock()

    mock_pr = mock.MagicMock()
    mock_pr.create_review.side_effect = GithubException(400, "Bad request")
    mock_repo.get_pull.return_value = mock_pr

    mock_gh = mock.MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    # Mock the context
    ctx = PipelineContext(
        issue=mock.MagicMock(),
        pr_number=123,
        diff="",
        task_spec=None,
        verification_report=None,
        review_results=[],
    )

    result = ReviewResult(
        model="test-model",
        verdict="approved",
        summary="Good code quality",
        inline_comments=[],
        score=0.9,
    )

    # Patch the github client and run
    with mock.patch("devfactory.github.review.gh", mock_gh):
        post_review(ctx, result)

    # Should have fallen back to issue comment
    mock_pr.create_issue_comment.assert_called_once()
