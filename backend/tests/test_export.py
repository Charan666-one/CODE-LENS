"""CP-1.6 — the crude visual dump renders and tells the truth."""

from __future__ import annotations

import json
from pathlib import Path

from app.graph.export import to_dot, to_summary_json
from app.parser import parse_repository

TINY_PYTHON = Path(__file__).resolve().parent.parent / "fixtures" / "tiny_python" / "repo"


def test_dot_output_is_valid_and_file_level() -> None:
    graph = parse_repository(TINY_PYTHON)
    dot = to_dot(graph)
    assert dot.startswith("digraph") and dot.rstrip().endswith("}")
    assert '"file:main.py"' in dot
    assert '"file:main.py" -> "file:calculator.py"' in dot  # the real import
    assert "function:" not in dot  # file-level view only


def test_dot_caps_node_count() -> None:
    graph = parse_repository(TINY_PYTHON)
    dot = to_dot(graph, max_nodes=1)
    assert dot.count("[shape=box") == 1


def test_summary_json_reports_real_counts() -> None:
    graph = parse_repository(TINY_PYTHON)
    summary = json.loads(to_summary_json(graph))
    assert summary["nodes"]["file"] == 3
    assert summary["nodes"]["function"] == 7
    assert summary["edges"]["calls"] == 5
    assert summary["calls_confidence"]["resolved"] == 5
    assert {e["id"] for e in summary["entrypoints"]} == {"function:main.main"}
