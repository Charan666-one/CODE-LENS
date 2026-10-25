"""CP-1.4 — GraphStore, traversal view, and the checkpointed pipeline.

The gate: build -> persist -> reload -> traverse CodeLens's own graph, and an
unchanged re-run provably skips the work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.pipeline import Stage, StageReport, UnreadableRepositoryError, run_pipeline
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
    # "N of M" rather than "M": the clone stage reports coverage, because
    # the file count alone never distinguished a repository that was read
    # from one that merely existed.
    assert by_stage[Stage.CLONED].detail == "1 of 1 files · Python"
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

# ── coverage: the product must never claim to have read what it cannot ────


def _write_repo(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def test_a_repository_in_an_unreadable_language_is_refused(tmp_path: Path) -> None:
    """The bug this guards: a Java repository cloned fine, reported "4 files
    · Java", parsed to a graph of one node, and returned `status: done` with
    an empty canvas behind it. A blank screen presented as an answer undoes
    every honest signal the product has."""
    _write_repo(tmp_path, {"src/App.java": "class App { void run() {} }"})
    store = SQLiteGraphStore(tmp_path / "s.db")
    with pytest.raises(UnreadableRepositoryError) as caught:
        run_pipeline(str(tmp_path), store)
    message = str(caught.value)
    assert "Python, JavaScript and TypeScript" in message  # what would work
    assert "Java" in message  # what was actually found
    store.close()


def test_a_repository_with_one_readable_file_among_many_is_refused(
    tmp_path: Path,
) -> None:
    """The case a zero-check let through. `golang/example` is 37 Go files
    and one stray parseable one, so "readable == 0" was false and the reader
    got a canvas holding a single dot for a repository full of code. A graph
    drawn from 3% of the source is a picture of something else."""
    files = {f"svc/handler{i}.go": "package main" for i in range(12)}
    files["scripts/tidy.py"] = "def tidy():\n    return 1\n"
    _write_repo(tmp_path, files)
    store = SQLiteGraphStore(tmp_path / "s.db")
    with pytest.raises(UnreadableRepositoryError) as caught:
        run_pipeline(str(tmp_path), store)
    assert "Go (12 files)" in str(caught.value)
    assert "Only 1 file" in str(caught.value)
    store.close()


def test_a_mostly_readable_repository_is_analysed_and_says_what_it_missed(
    tmp_path: Path,
) -> None:
    """The other side of the same line. A repository that is mostly readable
    must never be refused — the graph is sound, and the gap is reported
    rather than used as a reason to show nothing."""
    files = {f"app/mod{i}.py": f"def f{i}():\n    return {i}\n" for i in range(9)}
    files["tools/legacy.go"] = "package tools"  # not vendor/: that is an ignored dir
    _write_repo(tmp_path, files)
    store = SQLiteGraphStore(tmp_path / "s.db")
    snapshot = run_pipeline(str(tmp_path), store).graph.snapshot  # no refusal
    assert snapshot.unsupported_languages == {"Go": 1}
    store.close()


def test_coverage_counts_only_code_the_parser_cannot_read(tmp_path: Path) -> None:
    """Markdown and YAML are skipped on purpose and must not be reported as
    a gap — crying wolf on every repository would make the number useless."""
    _write_repo(
        tmp_path,
        {
            "main.py": "def go():\n    return 1\n",
            "README.md": "# Thing\n\nIt does the thing.\n",
            "ci.yaml": "on: push\n",
            "Worker.java": "class Worker {}",
        },
    )
    store = SQLiteGraphStore(tmp_path / "s.db")
    snapshot = run_pipeline(str(tmp_path), store).graph.snapshot
    assert snapshot.parsed_file_count == 1
    assert snapshot.file_count == 4
    assert snapshot.unsupported_languages == {"Java": 1}  # not Markdown, not YAML
    store.close()


def test_the_readme_is_captured_for_the_overview(tmp_path: Path) -> None:
    """The graph can say what a project is shaped like and never what it is
    for. That lives in prose the parser skips, so ingestion keeps it."""
    _write_repo(
        tmp_path,
        {"main.py": "def go():\n    return 1\n", "README.md": "# Atlas\n\nRoutes parcels.\n"},
    )
    store = SQLiteGraphStore(tmp_path / "s.db")
    snapshot = run_pipeline(str(tmp_path), store).graph.snapshot
    assert snapshot.readme is not None
    assert "Routes parcels." in snapshot.readme
    store.close()

def test_a_symlinked_readme_is_refused(tmp_path: Path) -> None:
    """A clone is untrusted input, and this one points at the host.

    `README.md` in a hostile repository can be a link to `/etc/passwd`, to
    `~/.aws/credentials`, or to this service's own `.env`. Following it
    would store the backend's secrets in the snapshot and — because the
    overview page exists — hand them to whichever model provider is
    configured. Nothing legitimate needs the root README to be a symlink.
    """
    secret = tmp_path / "host-secret.txt"
    secret.write_text("GROQ_API_KEY=sk-do-not-exfiltrate\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def go():\n    return 1\n", encoding="utf-8")
    (repo / "README.md").symlink_to(secret)

    store = SQLiteGraphStore(tmp_path / "s.db")
    snapshot = run_pipeline(str(repo), store).graph.snapshot
    assert snapshot.readme is None
    assert "do-not-exfiltrate" not in (snapshot.readme or "")
    store.close()


def test_a_huge_readme_is_bounded_not_loaded_whole(tmp_path: Path) -> None:
    """Inventory skips oversized files, but this read is separate from
    inventory — so without a bound a 2GB README under the repository size
    cap would be loaded whole and only then sliced."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def go():\n    return 1\n", encoding="utf-8")
    (repo / "README.md").write_text("x" * 400_000, encoding="utf-8")

    store = SQLiteGraphStore(tmp_path / "s.db")
    snapshot = run_pipeline(str(repo), store).graph.snapshot
    assert snapshot.readme is not None
    assert len(snapshot.readme) <= 12_000
    store.close()

