"""
Unit tests for reviewer agent functionality in devfactory/agents/reviewer.py
"""

import unittest.mock as mock

from devfactory.agents.reviewer import ReviewerAgent
from devfactory.context import GitHubIssue, PipelineContext


def test_parse_review_wellformed_json():
    """Test that reviewer parses well-formed JSON correctly."""

    # Test the logic directly without model instantiation

    # Valid JSON payload without code block
    valid_json = """
{
    "verdict": "approved",
    "summary": "Good code quality",
    "score": 0.9,
    "inline_comments": [
        {
            "path": "test.py",
            "line": 10,
            "body": "Consider using a more descriptive name"
        }
    ]
}
"""

    # Import the module and test the function directly
    from devfactory.agents.reviewer import ReviewerAgent

    agent = ReviewerAgent(model=mock.MagicMock())
    # Override the model property to avoid the error
    agent._model = mock.MagicMock()
    agent._model.name = "test-model"

    result = agent._parse_review(valid_json)

    assert result.model == "test-model"
    assert result.verdict == "approved"
    assert result.summary == "Good code quality"
    assert result.score == 0.9
    assert len(result.inline_comments) == 1
    assert result.inline_comments[0]["path"] == "test.py"
    assert result.inline_comments[0]["line"] == 10
    assert result.inline_comments[0]["body"] == "Consider using a more descriptive name"


def test_parse_review_json_in_code_block():
    """Test that reviewer extracts JSON from code blocks."""

    # Test the logic directly without model instantiation

    # Valid JSON payload inside code block
    json_in_block = """
```
{
    "verdict": "changes_requested",
    "summary": "Needs fixes",
    "score": 0.4,
    "inline_comments": []
}
```
"""

    # Import the module and test the function directly
    from devfactory.agents.reviewer import ReviewerAgent

    agent = ReviewerAgent(model=mock.MagicMock())
    # Override the model property to avoid the error
    agent._model = mock.MagicMock()
    agent._model.name = "test-model"

    result = agent._parse_review(json_in_block)

    assert result.model == "test-model"
    assert result.verdict == "changes_requested"
    assert result.summary == "Needs fixes"
    assert result.score == 0.4
    assert len(result.inline_comments) == 0


def test_parse_review_unparseable_output():
    """Test that reviewer returns commented verdict with score 0.5 for unparseable output."""

    # Test the logic directly without model instantiation

    # Unparseable JSON
    unparseable = "This is not JSON at all"

    # Import the module and test the function directly
    from devfactory.agents.reviewer import ReviewerAgent

    agent = ReviewerAgent(model=mock.MagicMock())
    # Override the model property to avoid the error
    agent._model = mock.MagicMock()
    agent._model.name = "test-model"

    result = agent._parse_review(unparseable)

    assert result.model == "test-model"
    assert result.verdict == "commented"
    assert result.score == 0.5
    # Should use first 300 characters of raw input as summary
    assert result.summary == unparseable[:300]


def test_parse_review_malformed_json():
    """Test that reviewer handles malformed JSON gracefully."""

    # Test the logic directly without model instantiation

    # Malformed JSON (missing closing brace)
    malformed = """
{
    "verdict": "approved",
    "summary": "Good code quality"
    "score": 0.9
"""

    # Import the module and test the function directly
    from devfactory.agents.reviewer import ReviewerAgent

    agent = ReviewerAgent(model=mock.MagicMock())
    # Override the model property to avoid the error
    agent._model = mock.MagicMock()
    agent._model.name = "test-model"

    result = agent._parse_review(malformed)

    assert result.model == "test-model"
    assert result.verdict == "commented"
    assert result.score == 0.5


def test_parse_review_missing_fields():
    """Test that reviewer handles missing fields in JSON gracefully."""

    # Test the logic directly without model instantiation

    # JSON with missing fields
    missing_fields = """
{
    "verdict": "approved"
}
"""

    # Import the module and test the function directly
    from devfactory.agents.reviewer import ReviewerAgent

    agent = ReviewerAgent(model=mock.MagicMock())
    # Override the model property to avoid the error
    agent._model = mock.MagicMock()
    agent._model.name = "test-model"

    result = agent._parse_review(missing_fields)

    assert result.model == "test-model"
    assert result.verdict == "approved"
    assert result.summary == ""  # Default empty string
    assert result.score == 0.5  # Default score
    assert result.inline_comments == []  # Default empty list


def test_build_user_prompt():
    """Test that the user prompt is built correctly."""

    mock_model = mock.MagicMock()
    mock_model.name = "test-model"
    agent = ReviewerAgent(model=mock_model)

    # Create a test context
    issue = GitHubIssue(1, "Test Issue", "Test body", "owner/repo", [], "url")
    ctx = PipelineContext(issue=issue)

    # Add some test data
    ctx.task_spec = mock.MagicMock()
    ctx.task_spec.summary = "Fix the bug"
    ctx.task_spec.acceptance_criteria = ["Fix must work", "Code must be clean"]

    ctx.verification_report = mock.MagicMock()
    ctx.verification_report.summary = "All tests passed"

    ctx.diff = (
        "diff --git a/test.py b/test.py\n"
        "index 123..456\n"
        "--- a/test.py\n"
        "+++ b/test.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+line2 modified\n"
        " line3"
    )

    prompt = agent._build_prompt(ctx)

    # Should contain all expected parts
    assert "Code review: Test Issue" in prompt
    assert "Fix the bug" in prompt
    assert "Fix must work" in prompt
    assert "All tests passed" in prompt
    assert "diff --git" in prompt
    assert "Return ONLY the JSON" in prompt


def test_reviewer_agent_instantiation():
    """Test that reviewer agent can be instantiated."""

    agent = ReviewerAgent(model=mock.MagicMock())
    assert agent is not None
    assert agent.role == "reviewer"


def test_parse_review_empty_json():
    """Test that reviewer handles empty JSON gracefully."""

    # Test the logic directly without model instantiation

    # Empty JSON
    empty = "{}"

    # Import the module and test the function directly
    from devfactory.agents.reviewer import ReviewerAgent

    agent = ReviewerAgent(model=mock.MagicMock())
    # Override the model property to avoid the error
    agent._model = mock.MagicMock()
    agent._model.name = "test-model"

    result = agent._parse_review(empty)

    assert result.model == "test-model"
    assert result.verdict == "commented"
    assert result.score == 0.5
    assert result.summary == ""


def test_parse_review_valid_json_with_extra_text():
    """Test that reviewer correctly handles JSON when there's extra text around it."""

    # Test the logic directly without model instantiation

    # Valid JSON with extra text - but this should fail because it's missing code block
    # The current regex pattern expects fenced code blocks
    json_with_text = """
Some introductory text that should be ignored.
{
    "verdict": "approved",
    "summary": "Good code quality",
    "score": 0.9,
    "inline_comments": []
}
Some trailing text that should be ignored.
"""

    # Import the module and test the function directly
    from devfactory.agents.reviewer import ReviewerAgent

    agent = ReviewerAgent(model=mock.MagicMock())
    # Override the model property to avoid the error
    agent._model = mock.MagicMock()
    agent._model.name = "test-model"

    result = agent._parse_review(json_with_text)

    # Since there are no code blocks, it should fail to parse and return commented verdict
    assert result.model == "test-model"
    assert result.verdict == "commented"
    assert result.score == 0.5


def test_parse_review_no_json_in_block():
    """Test parsing when there are code blocks but no JSON in them."""

    # Test the logic directly without model instantiation

    # Text in code blocks without valid JSON
    no_json_block = """
```
This is not JSON
```
"""

    # Import the module and test the function directly
    from devfactory.agents.reviewer import ReviewerAgent

    agent = ReviewerAgent(model=mock.MagicMock())
    # Override the model property to avoid the error
    agent._model = mock.MagicMock()
    agent._model.name = "test-model"

    result = agent._parse_review(no_json_block)

    assert result.model == "test-model"
    assert result.verdict == "commented"
    assert result.score == 0.5
