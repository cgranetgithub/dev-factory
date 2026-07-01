"""Tests for the KB dashboard leaderboard ranking."""

from devfactory.kb.dashboard import _rank_models


def _rows():
    """Synthetic model_stats() rows mixing quality scores and diagnostic counts."""
    return [
        # Developer model whose only metric is the diagnostic retry_count (0 = good).
        {"model": "coder:7b", "role": "developer", "metric": "retry_count",
         "avg_score": 0.0, "runs": 2, "avg_ms": 5000},
        # Analyst model with a real 0.0–1.0 quality score.
        {"model": "mistral:7b", "role": "analyst", "metric": "tests_pass_rate",
         "avg_score": 1.0, "runs": 1, "avg_ms": 4000},
        {"model": "mistral:7b", "role": "reviewer", "metric": "review_verdict",
         "avg_score": 0.6, "runs": 1, "avg_ms": 3000},
    ]


def test_retry_count_excluded_from_leaderboard():
    """A model with only diagnostic metrics must not appear in the ranking."""
    ranked = _rank_models(_rows(), None, None)
    models = [model for model, _avg, _n in ranked]
    assert "coder:7b" not in models


def test_leaderboard_averages_only_quality_scores():
    """The average must ignore retry_count and cover the quality metrics only."""
    ranked = _rank_models(_rows(), None, None)
    scores = {model: (avg, n) for model, avg, n in ranked}
    # mistral has two quality scores (1.0 and 0.6) → average 0.8 over 2 data points.
    assert scores["mistral:7b"] == (0.8, 2)


def test_metric_filter_restricts_to_one_metric():
    ranked = _rank_models(_rows(), None, "review_verdict")
    scores = {model: avg for model, avg, _n in ranked}
    assert scores == {"mistral:7b": 0.6}
