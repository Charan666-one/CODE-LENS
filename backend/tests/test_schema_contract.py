"""The frozen contract, enforced as tests.

FOUNDATION.md §Design principle 1 and ROADMAP.md §Constitution are prose until
something checks them. These tests are that check: facts and annotations never
mix, edges are honest about certainty, and every annotation carries evidence.
"""

import pytest
from pydantic import ValidationError

from app.graph.schema import (
    CallConfidence,
    Edge,
    EdgeKind,
    Node,
    NodeKind,
    SemanticAnnotation,
)


def test_node_carries_no_ai_derived_fields() -> None:
    """Constitution 2: deterministic first. Facts never carry interpretation."""
    ai_fields = {"summary", "embedding", "risk_score", "explanation"}
    assert ai_fields.isdisjoint(Node.model_fields)


def test_calls_edge_defaults_to_resolved() -> None:
    edge = Edge(source_id="function:a.f", target_id="function:b.g", kind=EdgeKind.CALLS)
    assert edge.confidence is CallConfidence.RESOLVED


def test_edge_can_declare_uncertainty() -> None:
    """Constitution 5: confidence is visible. The graph tells the truth about itself."""
    edge = Edge(
        source_id="function:a.f",
        target_id="function:b.g",
        kind=EdgeKind.CALLS,
        confidence=CallConfidence.DYNAMIC_UNKNOWN,
    )
    assert edge.confidence is CallConfidence.DYNAMIC_UNKNOWN


def test_annotation_requires_evidence_pointers() -> None:
    """Constitution 1: evidence before explanation. No orphan claims, ever."""
    with pytest.raises(ValidationError):
        SemanticAnnotation(  # type: ignore[call-arg]
            node_id="function:a.f",
            summary="Does a thing.",
            # derived_from omitted — an unsourced claim must not be constructible
            content_hash="abc",
            model="claude-opus-4-8",
            generated_at="2026-07-21T00:00:00Z",
        )


def test_node_id_convention_is_kind_colon_qualified_name() -> None:
    node = Node(
        id="function:app.services.auth.login",
        kind=NodeKind.FUNCTION,
        name="login",
        qualified_name="app.services.auth.login",
    )
    assert node.id == f"{node.kind.value}:{node.qualified_name}"
