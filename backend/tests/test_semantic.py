"""Stage 3 — the semantic layer, tested with a counting fake.

The two gates that matter:
* CP-3.2: an unchanged file provably costs ZERO second LLM calls.
* CP-3.3: a concept query finds the right node when nothing is named for it.

Every test runs the real code paths — assembler, cache, index, narration —
with `CountingFakeLLM` standing in for the model. The Anthropic client is the
same protocol; plugging it in changes no logic.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from app.graph.store import SQLiteGraphStore
from app.graph.traversal import GraphView
from app.parser import parse_repository
from app.queries import run_query
from app.semantic import (
    ConceptIndex,
    CountingFakeLLM,
    assemble,
    learning_path,
    narrate_blast_radius,
    narrate_project,
    summarize_graph,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"


@pytest.fixture()
def tiny() -> GraphView:
    return GraphView(parse_repository(TINY_PYTHON))


# ── CP-3.1: the context assembler enforces the RAG rule ───────────────────


def test_context_reads_real_source_lines(tiny: GraphView) -> None:
    bundle = assemble(tiny, TINY_PYTHON, ["function:calculator.add"])
    assert len(bundle.items) == 1
    assert "def add(a, b):" in bundle.items[0].text  # the actual source
    assert bundle.included_ids == ["function:calculator.add"]


def test_token_budget_is_a_hard_ceiling(tiny: GraphView) -> None:
    """Never stuffed: what doesn't fit is dropped and named, not squeezed."""
    everything = sorted(tiny.nodes_by_id)
    bundle = assemble(tiny, TINY_PYTHON, everything, token_budget=30)
    assert bundle.tokens_used <= 30
    assert bundle.dropped_ids  # the budget visibly cut something
    assert set(bundle.included_ids).isdisjoint(bundle.dropped_ids)


def test_context_never_includes_unknown_nodes(tiny: GraphView) -> None:
    bundle = assemble(tiny, TINY_PYTHON, ["function:calculator.add", "function:ghost.f"])
    assert "function:ghost.f" not in bundle.included_ids


# ── CP-3.2: summaries and THE caching gate ────────────────────────────────


def test_summaries_carry_evidence_and_model(tmp_path: Path, tiny: GraphView) -> None:
    llm = CountingFakeLLM()
    with SQLiteGraphStore(tmp_path / "s.db") as store:
        snapshot_id = store.save_graph(parse_repository(TINY_PYTHON))
        annotations, report = summarize_graph(
            tiny, TINY_PYTHON, store, snapshot_id, llm
        )
    assert annotations, "the fixture has public functions to summarise"
    for annotation in annotations:
        assert annotation.derived_from, "an unsourced claim must not exist"
        assert annotation.model == "fake-llm"
        assert annotation.content_hash
    assert report.llm_calls == len(annotations)


def test_unchanged_source_costs_zero_second_llm_calls(tmp_path: Path) -> None:
    """THE GATE. Same repository, second run: the counter must stay flat."""
    with SQLiteGraphStore(tmp_path / "s.db") as store:
        graph = parse_repository(TINY_PYTHON)
        snapshot_id = store.save_graph(graph)
        view = GraphView(graph)

        first_llm = CountingFakeLLM()
        _, first_report = summarize_graph(view, TINY_PYTHON, store, snapshot_id, first_llm)
        assert first_report.llm_calls > 0

        second_llm = CountingFakeLLM()
        _, second_report = summarize_graph(view, TINY_PYTHON, store, snapshot_id, second_llm)
        assert second_llm.calls == 0  # zero. not "few" — zero.
        assert second_report.llm_calls == 0
        assert len(second_report.from_cache) == first_report.llm_calls


def test_cache_transfers_between_snapshots_of_identical_source(tmp_path: Path) -> None:
    """The cache keys on content, not on the analysis run."""
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(TINY_PYTHON, copy)

    with SQLiteGraphStore(tmp_path / "s.db") as store:
        first_graph = parse_repository(TINY_PYTHON)
        first_id = store.save_graph(first_graph)
        llm = CountingFakeLLM()
        summarize_graph(GraphView(first_graph), TINY_PYTHON, store, first_id, llm)
        paid = llm.calls

        second_graph = parse_repository(copy)  # different path, same bytes
        second_id = store.save_graph(second_graph)
        summarize_graph(GraphView(second_graph), copy, store, second_id, llm)
        assert llm.calls == paid  # not one token spent again


def test_edited_source_pays_for_exactly_the_change(tmp_path: Path) -> None:
    """Constitution 4: cost scales with the diff, not the repo."""
    import shutil

    repo = tmp_path / "repo"
    shutil.copytree(TINY_PYTHON, repo)

    with SQLiteGraphStore(tmp_path / "s.db") as store:
        graph = parse_repository(repo)
        snapshot_id = store.save_graph(graph)
        llm = CountingFakeLLM()
        summarize_graph(GraphView(graph), repo, store, snapshot_id, llm)
        paid_first = llm.calls

        # Touch ONE function's body.
        calculator = repo / "calculator.py"
        calculator.write_text(
            calculator.read_text().replace("return a + b", "return b + a")
        )
        graph2 = parse_repository(repo)
        snapshot2 = store.save_graph(graph2)
        summarize_graph(GraphView(graph2), repo, store, snapshot2, llm)
        newly_paid = llm.calls - paid_first

        # add's body changed; calculator.py's file hash changed with it.
        # Everything else — multiply, shapes, main — must ride the cache.
        assert 1 <= newly_paid <= 2


def test_failed_llm_calls_skip_gracefully(tmp_path: Path, tiny: GraphView) -> None:
    class ExplodingLLM(CountingFakeLLM):
        def complete(self, *, system: str, prompt: str, max_tokens: int = 300) -> str:
            super().complete(system=system, prompt=prompt, max_tokens=max_tokens)
            from app.semantic import LLMError

            raise LLMError("simulated outage")

    with SQLiteGraphStore(tmp_path / "s.db") as store:
        snapshot_id = store.save_graph(parse_repository(TINY_PYTHON))
        annotations, report = summarize_graph(
            tiny, TINY_PYTHON, store, snapshot_id, ExplodingLLM()
        )
    assert annotations == []  # nothing invented
    assert report.failed  # and the failure is visible, not swallowed


# ── CP-3.3: concept search finds the unnamed ──────────────────────────────


@pytest.fixture()
def concept_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "concept"
    repo.mkdir()
    (repo / "throttle.py").write_text(
        dedent(
            '''
            """Request throttling utilities."""


            def check_request_budget(client_id):
                """Rate limiting: reject clients that exceed their request budget."""
                return client_id


            def reset_budgets():
                """Clear all rate limiting counters at the top of the window."""
                return True
            '''
        ).lstrip()
    )
    (repo / "orders.py").write_text(
        dedent(
            '''
            """Order handling."""


            def submit_order(order):
                """Validate and persist a customer order."""
                return order
            '''
        ).lstrip()
    )
    return repo


def test_concept_search_finds_nodes_not_named_for_the_concept(concept_repo: Path) -> None:
    """'rate limiting' appears in no file or function NAME — only docstrings.
    The right node must still surface, and as a graph node, not a snippet."""
    view = GraphView(parse_repository(concept_repo))
    index = ConceptIndex()
    indexed = index.build(view, annotations=[])
    assert indexed > 0

    result = run_query("concept_search", view, index=index, text="rate limiting", top=3)
    top_ids = [r.node_id for r in result.ranked]
    assert "function:throttle.check_request_budget" in top_ids
    assert "function:orders.submit_order" not in top_ids[:2]
    assert all(view.has_node(node_id) for node_id in top_ids)


def test_concept_search_uses_summaries_when_present(concept_repo: Path) -> None:
    """An annotation's words become findable — the semantic layer compounds."""
    from app.graph.schema import SemanticAnnotation

    view = GraphView(parse_repository(concept_repo))
    index = ConceptIndex()
    index.build(
        view,
        annotations=[
            SemanticAnnotation(
                node_id="function:orders.submit_order",
                summary="Handles checkout payment capture for the storefront.",
                derived_from=["function:orders.submit_order"],
                content_hash="x",
                model="fake-llm",
                generated_at="2026-07-22T00:00:00Z",
            )
        ],
    )
    result = run_query("concept_search", view, index=index, text="checkout payment", top=2)
    assert result.ranked[0].node_id == "function:orders.submit_order"


# ── CP-3.4: narration carries receipts ────────────────────────────────────


def test_project_narration_carries_evidence(tiny: GraphView) -> None:
    llm = CountingFakeLLM()
    answer = narrate_project(tiny, annotations=[], llm=llm)
    assert llm.calls == 1
    assert answer.evidence_ids, "no orphan claims"
    assert answer.model == "fake-llm"
    # The prompt the model saw was graph facts, not repository dumps.
    assert "Central file:" in llm.prompts[0]


def test_learning_path_is_deterministic_and_needs_no_model(tiny: GraphView) -> None:
    answer = learning_path(tiny, annotations=[])
    again = learning_path(tiny, annotations=[])
    assert answer.text == again.text
    assert answer.model is None  # the ordering IS the answer; nobody narrated it
    assert "function:main.main" in answer.evidence_ids  # the entrypoint leads
    assert answer.evidence_ids[0] == "function:main.main"


def test_blast_radius_story_names_the_dependents(tiny: GraphView) -> None:
    llm = CountingFakeLLM()
    result = run_query("blast_radius", tiny, node_id="function:calculator.add")
    answer = narrate_blast_radius(tiny, result, annotations=[], llm=llm)

    assert answer.meta["total_affected"] == 5
    assert set(answer.evidence_ids) >= {r.node_id for r in result.ranked}
    prompt = llm.prompts[0]
    assert "calculator.multiply" in prompt  # the facts went in by name
    assert "path:" in prompt  # with their receipts
