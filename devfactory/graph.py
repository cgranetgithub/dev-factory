"""
The developer → gates loop, as a graph.

It was a ``while True`` with three counters and four near-identical rejection
blocks. The flow is genuinely cyclic — every gate can send the change back, and a
shared budget has to stop it eventually — so it is expressed here as a graph with
conditional edges rather than approximated by hand.

**What lives in the graph state.** Only orchestration: the counters, the budget,
and which gate last spoke. Nothing an agent produced. That is deliberate and it is
what makes the checkpoint useful — after a crash, a node re-runs and re-reads the
world (the issue, the specification issue, the checkout) instead of needing a
serialised copy of what the previous node was holding. State that can be
re-derived should not be persisted.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

if TYPE_CHECKING:
    from devfactory.orchestrator import Pipeline

logger = logging.getLogger(__name__)

# Node names, used as both graph labels and routing targets.
DEVELOPER = "developer"
SCOPE = "scope"
VERIFICATION = "verification"
REVIEW = "review"


class LoopState(TypedDict):
    """Orchestration state, and nothing else.

    Small and made of plain values on purpose: it is what gets checkpointed, and a
    checkpoint that carries half the pipeline's objects is a serialisation problem
    waiting to happen.
    """

    # One counter per gate rather than a single total: they are different gates,
    # and the record of *why* a change kept coming back is worth keeping.
    verification_attempts: int
    review_rejections: int
    scope_rejections: int
    # Set when the budget ran out with the reviewer still unsatisfied, so the PR
    # can say so instead of presenting the change as agreed.
    review_unresolved: bool


def build(pipeline: Pipeline, max_iterations: int):
    """Wire the loop for ``pipeline``.

    The nodes are thin: each runs one stage and reports what it decided. Every
    routing decision lives in the ``_after_*`` functions, so the flow can be read
    in one place rather than inferred from where the ``continue`` statements are.
    """
    graph = StateGraph(LoopState)

    graph.add_node(DEVELOPER, pipeline.node_developer)
    graph.add_node(SCOPE, pipeline.node_scope)
    graph.add_node(VERIFICATION, pipeline.node_verification)
    graph.add_node(REVIEW, pipeline.node_review)

    graph.add_edge(START, DEVELOPER)
    graph.add_edge(DEVELOPER, SCOPE)

    # Cheapest gate first: a set comparison must not queue behind a container, and
    # neither should spend a model call on a change that has already been refused.
    graph.add_conditional_edges(
        SCOPE,
        _router(pipeline, VERIFICATION, max_iterations),
        {DEVELOPER: DEVELOPER, VERIFICATION: VERIFICATION, END: END},
    )
    graph.add_conditional_edges(
        VERIFICATION,
        _router(pipeline, REVIEW, max_iterations),
        {DEVELOPER: DEVELOPER, REVIEW: REVIEW, END: END},
    )
    graph.add_conditional_edges(
        REVIEW,
        _router(pipeline, END, max_iterations),
        {DEVELOPER: DEVELOPER, END: END},
    )

    return graph


def _router(pipeline: Pipeline, on_pass: str, max_iterations: int):
    """Build the decision taken after a gate: forward, back, or stop.

    One function for all three because the decision is the same shape every time —
    which is exactly what four hand-written copies of it kept getting subtly
    different.
    """

    def route(state: LoopState) -> Literal["developer"] | str:
        if pipeline.last_gate_passed:
            return on_pass

        if _iterations_used(state) >= max_iterations:
            # The budget is spent. The pipeline decides whether that ends the run
            # or opens the pull request with the gate unsatisfied — a verification
            # failure is fatal, an unconvinced reviewer is for a human to arbitrate.
            pipeline.on_budget_exhausted(state)
            return END

        return DEVELOPER

    return route


def _iterations_used(state: LoopState) -> int:
    """Developer iterations consumed, whichever gate sent the change back.

    Every gate draws on one budget: a change alternating between them must still
    terminate.
    """
    return state["verification_attempts"] + state["review_rejections"] + state["scope_rejections"]


def initial_state() -> LoopState:
    return LoopState(
        verification_attempts=0,
        review_rejections=0,
        scope_rejections=0,
        review_unresolved=False,
    )
