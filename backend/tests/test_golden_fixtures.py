"""CP-0.2 — the golden-fixture harness.

ARCHITECTURE.md §2: "a fixtures/ directory of small repos with hand-verified
expected graphs. Parser changes run against them in CI. **The parser's
correctness is CodeLens's correctness.**"

Two layers live here:

1. **Manifest integrity** — runs today. Keeps the hand-written golden files
   honest: ids follow the convention, edge endpoints exist, and every declared
   line number actually points at the definition it claims. A hand-verified
   fixture is only worth something if the hand-verification is itself checked.
2. **Parser comparison** — skipped until `app.parser.parse_repository` exists.
   This is the test the parser must earn its way to passing in CP-1.2, and the
   alarm that fires on any graph drift thereafter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.graph.schema import CallConfidence, EdgeKind, NodeKind

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

FIXTURE_DIRS = sorted(
    path
    for path in FIXTURES_DIR.iterdir()
    if path.is_dir() and (path / "expected_graph.json").is_file()
)

try:  # the parser lands in CP-1.2
    from app.parser import parse_repository
except ImportError:  # pragma: no cover - the expected state until CP-1.2
    parse_repository = None  # type: ignore[assignment]


requires_parser = pytest.mark.skipif(
    parse_repository is None,
    reason="parser lands in CP-1.2 — this is the test it must earn its way to passing",
)

fixture_case = pytest.mark.parametrize("fixture_dir", FIXTURE_DIRS, ids=lambda p: p.name)


def _manifest(fixture_dir: Path) -> dict[str, Any]:
    return json.loads((fixture_dir / "expected_graph.json").read_text())


def _source_line(fixture_dir: Path, file_path: str, line_no: int) -> str:
    lines = (fixture_dir / "repo" / file_path).read_text().splitlines()
    assert 1 <= line_no <= len(lines), f"{file_path}:{line_no} is out of range"
    return lines[line_no - 1]


def _token_for(node: dict[str, Any]) -> str:
    """The identifier we expect to see on a line that references this node."""
    return Path(node["name"]).stem if node["kind"] == "file" else node["name"]


# ── Layer 1: manifest integrity (runs today) ──────────────────────────────


def test_at_least_one_fixture_exists() -> None:
    assert FIXTURE_DIRS, "no golden fixtures found — CP-0.2 is not satisfied"


@fixture_case
def test_fixture_repo_has_source(fixture_dir: Path) -> None:
    repo = fixture_dir / "repo"
    assert any(repo.rglob("*.py")) or any(repo.rglob("*.js"))


@fixture_case
def test_node_ids_follow_convention(fixture_dir: Path) -> None:
    for node in _manifest(fixture_dir)["nodes"]:
        assert node["id"] == f"{node['kind']}:{node['qualified_name']}"


@fixture_case
def test_declared_kinds_exist_in_schema(fixture_dir: Path) -> None:
    manifest = _manifest(fixture_dir)
    assert set(manifest["asserted_node_kinds"]) <= {k.value for k in NodeKind}
    assert set(manifest["asserted_edge_kinds"]) <= {k.value for k in EdgeKind}

    for node in manifest["nodes"]:
        assert node["kind"] in manifest["asserted_node_kinds"]
    for edge in manifest["edges"]:
        assert edge["kind"] in manifest["asserted_edge_kinds"]
        assert edge["confidence"] in {c.value for c in CallConfidence}


@fixture_case
def test_edges_reference_known_nodes(fixture_dir: Path) -> None:
    manifest = _manifest(fixture_dir)
    known = {node["id"] for node in manifest["nodes"]}
    for edge in manifest["edges"]:
        assert edge["source_id"] in known, edge
        assert edge["target_id"] in known, edge
    for entrypoint in manifest["entrypoints"]:
        assert entrypoint["id"] in known


@fixture_case
def test_node_line_numbers_point_at_definitions(fixture_dir: Path) -> None:
    """Catches hand-verification slips in the golden file itself."""
    for node in _manifest(fixture_dir)["nodes"]:
        if node.get("start_line") is None:
            continue
        line = _source_line(fixture_dir, node["file_path"], node["start_line"])
        assert node["name"] in line, f"{node['id']} claims line {node['start_line']}: {line!r}"


@fixture_case
def test_edge_line_numbers_point_at_references(fixture_dir: Path) -> None:
    """Every edge is evidence (Constitution 1) — the cited line must show it."""
    manifest = _manifest(fixture_dir)
    by_id = {node["id"]: node for node in manifest["nodes"]}
    for edge in manifest["edges"]:
        line = _source_line(fixture_dir, edge["file_path"], edge["line"])
        token = _token_for(by_id[edge["target_id"]])
        assert token in line, f"{edge['kind']} → {edge['target_id']} cites {line!r}"


# ── Layer 2: parser comparison (unlocks at CP-1.2) ────────────────────────


@requires_parser
@fixture_case
def test_parser_matches_golden_graph(fixture_dir: Path) -> None:
    manifest = _manifest(fixture_dir)
    graph = parse_repository(fixture_dir / "repo")

    node_kinds = set(manifest["asserted_node_kinds"])
    expected_nodes = {node["id"] for node in manifest["nodes"]}
    actual_nodes = {n.id for n in graph.nodes if n.kind.value in node_kinds}
    assert actual_nodes == expected_nodes

    edge_kinds = set(manifest["asserted_edge_kinds"])
    expected_edges = {
        (e["source_id"], e["target_id"], e["kind"], e["confidence"]) for e in manifest["edges"]
    }
    actual_edges = {
        (e.source_id, e.target_id, e.kind.value, e.confidence.value)
        for e in graph.edges
        if e.kind.value in edge_kinds
        and e.source_id in expected_nodes
        and e.target_id in expected_nodes
    }
    assert actual_edges == expected_edges


@requires_parser
@fixture_case
def test_parser_detects_entrypoints(fixture_dir: Path) -> None:
    manifest = _manifest(fixture_dir)
    graph = parse_repository(fixture_dir / "repo")

    expected = {(e["id"], e["entrypoint_kind"]) for e in manifest["entrypoints"]}
    actual = {
        (n.id, n.entrypoint_kind.value if n.entrypoint_kind else None)
        for n in graph.nodes
        if n.is_entrypoint
    }
    assert actual == expected
