"""CP-1.4 — GraphStore, traversal view, and the checkpointed pipeline.

The gate: build -> persist -> reload -> traverse CodeLens's own graph, and an
unchanged re-run provably skips the work.
"""

from __future__ import annotations

from pathlib import Path

from app.core.pipeline import Stage, StageReport, run_pipeline
from app.graph.schema import EdgeKind, KnowledgeGraph
from app.graph.store import SQLiteGraphStore
from app.graph.traversal import GraphView
from app.parser import parse_repository

BACKEND_DIR = Path(__file__).resolve().parent.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}


def small_graph() -> KnowledgeGraph:
    return parse_repository(TINY_PYTHON)


# ── round trip ────────────────────────────────────────────────────────────


def test_graph_survives_a_round_trip(tmp_path: Path) -> None:
    """What goes in must come out — node for node, edge for edge."""
    graph = small_graph()
    with SQLiteGraphStore(tmp_path / "g.db") as store:
        store.save_graph(graph)
        loaded = store.load_graph(graph.snapshot.repo_url)

    assert loaded is not None
    assert {n.id for n in loaded.nodes} == {n.id for n in graph.nodes}
    assert {
        (e.source_id, e.target_id, e.kind, e.confidence) for e in loaded.edges
    } == {(e.source_id, e.target_id, e.kind, e.confidence) for e in graph.edges}
    assert loaded.snapshot.commit_sha == graph.snapshot.commit_sha


def test_saving_the_same_snapshot_twice_replaces_not_duplicates(tmp_path: Path) -> None:
    graph = small_graph()
    with SQLiteGraphStore(tmp_path / "g.db") as store:
        store.save_graph(graph)
        store.save_graph(graph)
        assert len(store.list_snapshots()) == 1


def test_missing_repo_loads_as_none(tmp_path: Path) -> None:
    with SQLiteGraphStore(tmp_path / "g.db") as store:
        assert store.load_graph("https://github.com/nobody/nothing") is None


def test_store_persists_across_connections(tmp_path: Path) -> None:
    """The file, not the process, is the store."""
    graph = small_graph()
    db = tmp_path / "g.db"
    with SQLiteGraphStore(db) as store:
        store.save_graph(graph)
    with SQLiteGraphStore(db) as reopened:
        loaded = reopened.load_graph(graph.snapshot.repo_url)
    assert loaded is not None and len(loaded.nodes) == len(graph.nodes)


# ── traversal view ────────────────────────────────────────────────────────


def test_reverse_closure_finds_transitive_dependents() -> None:
    """calculator.add's dependents: multiply calls it; shapes/main lead there."""
    view = GraphView(small_graph())
    dependents = view.dependents_of("function:calculator.add", DEPENDENCY_KINDS)
    assert "function:calculator.multiply" in dependents  # direct caller
    assert "function:shapes.Rectangle.area" in dependents  # calls multiply
    assert "function:main.area" in dependents  # calls multiply
    # CONTAINS is not a dependency: the class node must not appear.
    assert "class:shapes.Rectangle" not in dependents


def test_forward_closure_finds_transitive_dependencies() -> None:
    view = GraphView(small_graph())
    dependencies = view.dependencies_of("function:main.main", DEPENDENCY_KINDS)
    assert "function:main.area" in dependencies
    assert "function:calculator.multiply" in dependencies
    assert "function:calculator.add" in dependencies


def test_fan_in_counts_distinct_direct_callers() -> None:
    view = GraphView(small_graph())
    # multiply is called by Rectangle.area and main.area — two distinct callers.
    assert view.fan_in("function:calculator.multiply", {EdgeKind.CALLS}) == 2


def test_contains_hierarchy_walk() -> None:
    view = GraphView(small_graph())
    children = view.children_of("file:shapes.py")
    assert "class:shapes.Shape" in children
    assert "class:shapes.Rectangle" in children


# ── the pipeline gate ─────────────────────────────────────────────────────


def test_pipeline_builds_persists_and_skips_unchanged(tmp_path: Path) -> None:
    """The CP-1.4 gate, on CodeLens itself."""
    events: list[tuple[Stage, bool]] = []

    def watch(report: StageReport) -> None:
        events.append((report.stage, report.skipped))

    with SQLiteGraphStore(tmp_path / "codelens.db") as store:
        first = run_pipeline(
            BACKEND_DIR, store, max_size_mb=5_000, on_progress=watch
        )
        assert not first.skipped
        assert [s for s, _ in events] == [
            Stage.CLONED,
            Stage.PARSED,
            Stage.METRICS,
            Stage.GRAPH_BUILT,
        ]
        assert all(not skipped for _, skipped in events)

        # Re-run on the unchanged tree: parse and build must be skipped.
        events.clear()
        second = run_pipeline(
            BACKEND_DIR, store, max_size_mb=5_000, on_progress=watch
        )
        assert second.skipped
        assert (Stage.PARSED, True) in events
        assert (Stage.GRAPH_BUILT, True) in events
        assert second.snapshot_id == first.snapshot_id

        # And the reloaded graph is traversable end to end.
        view = GraphView(second.graph)
        target = "function:app.ingestion.snapshot_directory"
        assert view.has_node(target)
        assert view.dependents_of(target, DEPENDENCY_KINDS)


def test_pipeline_rebuilds_when_content_changes(tmp_path: Path) -> None:
    """Same HEAD, edited file: the content digest must force a rebuild."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def one():\n    return 1\n")

    with SQLiteGraphStore(tmp_path / "g.db") as store:
        first = run_pipeline(repo, store)
        assert not first.skipped

        (repo / "a.py").write_text("def one():\n    return 2\n")
        second = run_pipeline(repo, store)
        assert not second.skipped  # a sha of "unknown" must never justify a skip


def test_progress_events_are_the_real_stages(tmp_path: Path) -> None:
    """Honest theater: the UI's progress feed IS the pipeline's own telemetry."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def one():\n    return 1\n")

    seen: list[Stage] = []
    with SQLiteGraphStore(tmp_path / "g.db") as store:
        result = run_pipeline(repo, store, on_progress=lambda r: seen.append(r.stage))
    assert seen == [Stage.CLONED, Stage.PARSED, Stage.METRICS, Stage.GRAPH_BUILT]
    assert [entry.stage for entry in result.stages] == seen

    # Every detail is a count the stage actually took, so each one has to
    # appear somewhere in the graph it describes. A stage that measured
    # nothing says nothing rather than guessing.
    by_stage = {entry.stage: entry for entry in result.stages}
    assert by_stage[Stage.CLONED].detail == "1 files \u00b7 Python"
    assert by_stage[Stage.PARSED].detail == f"{len(result.graph.nodes):,} nodes"
    assert by_stage[Stage.GRAPH_BUILT].detail == f"{len(result.graph.edges):,} relationships"


# ── concurrency: one repo, one pipeline ───────────────────────────────────


def test_two_pipelines_on_one_source_do_not_overlap(tmp_path) -> None:
    """A repository clones to a path derived from its name, so two runs share
    a working tree — and a clone starts by deleting it. Observed in the app:
    a double-click left a 172KB half-clone and a job stuck on "running".

    This asserts the runs serialise rather than interleave.
    """
    import threading

    from app.core import pipeline

    source = tmp_path / "repo"
    source.mkdir()
    (source / "a.py").write_text("def a():\n    return 1\n")

    inside = 0
    overlapped = False
    guard = threading.Lock()
    real = pipeline._run_pipeline_locked

    def watched(*args, **kwargs):
        nonlocal inside, overlapped
        with guard:
            inside += 1
            if inside > 1:
                overlapped = True
        try:
            return real(*args, **kwargs)
        finally:
            with guard:
                inside -= 1

    pipeline._run_pipeline_locked = watched  # type: ignore[assignment]
    try:
        store = SQLiteGraphStore(tmp_path / "graph.db")
        threads = [
            threading.Thread(target=lambda: pipeline.run_pipeline(source, store))
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    finally:
        pipeline._run_pipeline_locked = real  # type: ignore[assignment]

    assert not overlapped, "two pipelines ran against the same clone directory at once"


def test_asking_twice_for_the_same_repo_returns_the_same_job() -> None:
    """The double-click that caused the race. One question, one job."""
    from app.core import jobs

    registry = jobs.JobRegistry()
    first = registry.create(key="https://github.com/pallets/flask")
    registry.update(first.id, status="running")

    assert registry.find_active("https://github.com/pallets/flask") is first
    # A different repo is a different question.
    assert registry.find_active("https://github.com/psf/requests") is None
    # And a finished job never captures a fresh request.
    registry.update(first.id, status="done")
    assert registry.find_active("https://github.com/pallets/flask") is None
