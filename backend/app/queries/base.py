"""The QueryPlan registry — where CodeLens becomes CodeLens.

ARCHITECTURE.md §5: every user question resolves to a *named query plan* in a
registry, through one uniform pipeline:

    Question -> QueryPlan -> Graph traversal -> ResultGraph -> (view + narration)

The registry is the contract that keeps AI at the edge: free-form chat later
is a router that picks and parameterises plans — the LLM chooses *which query
runs*, never *what the facts are*. Adding a question means registering a plan,
not growing an if-tree.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from app.graph.schema import Edge
from app.graph.traversal import GraphView


class RankedNode(BaseModel):
    """One node in a ranked answer, with the *why* of its rank kept explicit."""

    node_id: str
    score: float
    # Deterministic facts behind the score (distance, fan_in, churn...), so a
    # renderer or narrator can explain the ranking without recomputing it.
    reasons: dict[str, Any] = Field(default_factory=dict)


class ResultGraph(BaseModel):
    """What every query returns: a real subgraph plus an ordering over it.

    Everything downstream — the ViewSpec compiler, the narrator — reads this
    one shape. `nodes` and `edges` are the evidence; `ranked` is the answer;
    `paths` are the receipts (every path is walkable in the evidence subgraph).
    """

    query: str
    params: dict[str, Any] = Field(default_factory=dict)
    focus_id: str | None = None  # the node the question was about, if any
    node_ids: list[str] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    ranked: list[RankedNode] = Field(default_factory=list)
    #: target node id -> the dependency path from focus to it (node id chain)
    paths: dict[str, list[str]] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class QueryError(Exception):
    """A plan was asked something it cannot answer (unknown node, bad params)."""


#: A query plan: pure function of the graph view and its parameters.
QueryPlan = Callable[..., ResultGraph]

_REGISTRY: dict[str, QueryPlan] = {}


def register(name: str) -> Callable[[QueryPlan], QueryPlan]:
    """Register a plan under its public name. Import-time, declarative."""

    def bind(plan: QueryPlan) -> QueryPlan:
        if name in _REGISTRY:
            raise ValueError(f"query plan {name!r} registered twice")
        _REGISTRY[name] = plan
        return plan

    return bind


def run_query(name: str, view: GraphView, **params: Any) -> ResultGraph:
    """The uniform entry point. Raises QueryError for unknown plans."""
    plan = _REGISTRY.get(name)
    if plan is None:
        known = ", ".join(sorted(_REGISTRY))
        raise QueryError(f"no query plan named {name!r} (known: {known})")
    return plan(view, **params)


def registered_queries() -> list[str]:
    return sorted(_REGISTRY)
