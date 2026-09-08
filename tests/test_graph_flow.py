"""
Tests for the graph that owns the developer → gates loop.

`tests/test_build_loop.py` asserts the behaviour through `Pipeline._build_loop`
and still passes unchanged — that is the real regression check for this move.
These cover the graph itself: its routing, its budget, and the fact that the
counters live in one place.
"""

from __future__ import annotations

from devfactory import graph


class _FakePipeline:
    """Only what the router reads."""

    def __init__(self, passed: bool):
        self.last_gate_passed = passed
        self.exhausted_with: list = []

    def on_budget_exhausted(self, state):
        self.exhausted_with.append(state)


def _state(verification=0, review=0, scope=0) -> graph.LoopState:
    return graph.LoopState(
        verification_attempts=verification,
        review_rejections=review,
        scope_rejections=scope,
        review_unresolved=False,
    )


def test_a_run_starts_with_a_clean_budget():
    assert graph._iterations_used(graph.initial_state()) == 0


def test_every_gate_draws_on_the_same_budget():
    """A change alternating between gates must still terminate, so the counters
    add up rather than each getting three attempts of its own."""
    assert graph._iterations_used(_state(verification=1, review=1, scope=1)) == 3


def test_a_passing_gate_moves_forward():
    route = graph._router(_FakePipeline(passed=True), graph.VERIFICATION, max_iterations=3)

    assert route(_state()) == graph.VERIFICATION


def test_a_failing_gate_sends_the_change_back():
    route = graph._router(_FakePipeline(passed=False), graph.VERIFICATION, max_iterations=3)

    assert route(_state(scope=1)) == graph.DEVELOPER


def test_the_budget_stops_the_loop():
    pipeline = _FakePipeline(passed=False)
    route = graph._router(pipeline, graph.VERIFICATION, max_iterations=3)

    assert route(_state(verification=2, review=1)) == graph.END
    assert pipeline.exhausted_with, "the pipeline must be told, so it can decide what that means"


def test_the_budget_is_not_spent_one_iteration_early():
    """Off-by-one here costs a whole attempt, and a reviewer once caught exactly
    this mistake in a hand-written version."""
    pipeline = _FakePipeline(passed=False)
    route = graph._router(pipeline, graph.VERIFICATION, max_iterations=3)

    assert route(_state(verification=2)) == graph.DEVELOPER
    assert pipeline.exhausted_with == []


def test_the_graph_has_the_four_nodes_and_compiles():
    class _P:
        last_gate_passed = True

        def node_developer(self, s):
            return s

        node_scope = node_verification = node_review = node_developer

        def on_budget_exhausted(self, state):
            pass

    compiled = graph.build(_P(), max_iterations=3).compile()

    assert set(compiled.get_graph().nodes) >= {
        graph.DEVELOPER,
        graph.SCOPE,
        graph.VERIFICATION,
        graph.REVIEW,
    }
