"""Full-system verification — every subsystem exercised, end to end, for real.

Run:  cd backend && .venv/bin/python scripts/verify_system.py [repo_url]

What it does (and prints):
  1. Pipeline: clone a real repository, build + persist the graph; re-run and
     prove the content-digest skip.
  2. Queries: blast radius (with paths + confidence), centrality, risk,
     entrypoints — the deterministic answers.
  3. ViewSpec: compile all three zoom levels, check the invariants.
  4. Semantic: summaries with the counting fake (cache proven: second run is
     zero calls), concept search, deterministic learning path.
  5. API: the same flows over HTTP via TestClient, including the trust
     boundary and the honest 503 for narration without a key.

Exit code 0 only if every check passes. No API key needed; no tokens spent.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_REPO = "https://github.com/psf/requests"

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    CHECKS.append((name, passed, detail))
    mark = "✓" if passed else "✗ FAIL"
    print(f"  {mark}  {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    repo_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REPO
    print(f"CodeLens full-system verification · {repo_url}\n")

    with tempfile.TemporaryDirectory(prefix="codelens-verify-") as scratch:
        workdir = Path(scratch)

        # ── 1. pipeline ───────────────────────────────────────────────────
        print("1. PIPELINE")
        from app.core.pipeline import run_pipeline
        from app.graph.store import SQLiteGraphStore

        store = SQLiteGraphStore(workdir / "verify.db")
        started = time.monotonic()
        first = run_pipeline(repo_url, store, workdir=workdir / "clones", max_size_mb=2000)
        build_seconds = time.monotonic() - started
        graph = first.graph
        check(
            "clone → parse → metrics → persist",
            not first.skipped and len(graph.nodes) > 50,
            f"{len(graph.nodes)} nodes, {len(graph.edges)} edges in {build_seconds:.1f}s",
        )

        second = run_pipeline(repo_url, store, workdir=workdir / "clones", max_size_mb=2000)
        check("unchanged re-run skips via content digest", second.skipped)

        ids = {n.id for n in graph.nodes}
        dangling = [e for e in graph.edges if e.source_id not in ids or e.target_id not in ids]
        check("graph invariants", not dangling and len(ids) == len(graph.nodes),
              "no dangling edges, no duplicate ids")

        # ── 2. queries ────────────────────────────────────────────────────
        print("\n2. DETERMINISTIC QUERIES")
        from app.graph.schema import NodeKind
        from app.graph.traversal import GraphView
        from app.queries import run_query

        view = GraphView(graph)
        central = run_query("centrality", view, kind="file", top=5)
        check("centrality", len(central.ranked) == 5,
              "top file: " + central.ranked[0].node_id.split(":", 1)[1])

        target = central.ranked[0].node_id
        blast = run_query("blast_radius", view, node_id=target)
        paths_ok = all(
            path[0] == dependent and path[-1] == target
            for dependent, path in blast.paths.items()
        )
        check("blast radius with paths", blast.meta["total_affected"] > 0 and paths_ok,
              f"{blast.meta['total_affected']} dependents, every path walkable")

        risk = run_query("risk", view, top=5)
        check("risk ranking", bool(risk.ranked) and risk.ranked[0].score == 1.0,
              "riskiest: " + risk.ranked[0].node_id.split(":", 1)[1])

        entry = run_query("entrypoints", view)
        check("entrypoints", "by_kind" in entry.meta,
              f"{len(entry.node_ids)} found")

        # ── 3. viewspec ───────────────────────────────────────────────────
        print("\n3. VIEWSPEC COMPILER")
        from app.views.viewspec import compile_viewspec

        sizes = []
        for zoom in (1, 2, 3):
            spec = compile_viewspec(graph, zoom=zoom)
            sizes.append(len(spec.nodes))
        check("semantic zoom L1<L2<L3", sizes[0] < sizes[1] < sizes[2],
              f"nodes per level: {sizes}")
        spec2 = compile_viewspec(graph, zoom=2)
        det = compile_viewspec(graph, zoom=2).model_dump() == spec2.model_dump()
        check("deterministic layout", det)

        # ── 4. semantic layer (counting fake — zero tokens) ───────────────
        print("\n4. SEMANTIC LAYER (fake model, $0)")
        from app.semantic import ConceptIndex, CountingFakeLLM, learning_path, summarize_graph

        clone_root = workdir / "clones" / repo_url.rstrip("/").rpartition("/")[2]
        llm = CountingFakeLLM()
        snapshot_id = first.snapshot_id
        _, report1 = summarize_graph(view, clone_root, store, snapshot_id, llm, max_nodes=25)
        _, report2 = summarize_graph(view, clone_root, store, snapshot_id, llm, max_nodes=25)
        check("summaries generated with evidence", report1.llm_calls > 0,
              f"{report1.llm_calls} paid calls")
        check("unchanged source: ZERO second calls", report2.llm_calls == 0,
              f"{len(report2.from_cache)} served from cache")

        index = ConceptIndex()
        indexed = index.build(view, store.load_annotations(snapshot_id))
        hits = index.search("session cookies authentication", top=3)
        check("concept search returns graph nodes", indexed > 0 and bool(hits),
              hits[0][0] if hits else "")

        path_answer = learning_path(view, store.load_annotations(snapshot_id))
        check("learning path is deterministic (model: none)",
              path_answer.model is None and bool(path_answer.evidence_ids),
              f"{len(path_answer.evidence_ids)} stops")

        files = [n for n in graph.nodes if n.kind is NodeKind.FILE]
        with_churn = sum(1 for n in files if n.churn_count is not None)
        # A fresh clone has (shallow) git history, so churn must be recorded.
        check("temporal facts present on files", with_churn > 0,
              f"{with_churn}/{len(files)} files carry churn")
        store.close()

    # ── 5. API over HTTP ──────────────────────────────────────────────────
    print("\n5. API (TestClient)")
    from fastapi.testclient import TestClient

    import app.api.routes as routes
    from app.main import app

    with tempfile.TemporaryDirectory(prefix="codelens-api-") as scratch:
        routes._STORE = SQLiteGraphStore(Path(scratch) / "api.db")
        with TestClient(app) as client:
            check("health", client.get("/health").status_code == 200)
            hostile = client.post("/api/analyze", json={"source": "ext::sh -c 'id'"})
            check("trust boundary rejects hostile source", hostile.status_code == 400)
            local = client.post("/api/analyze", json={"source": "/etc"})
            check("local paths disabled by default", local.status_code == 400)
            fixture = Path(__file__).resolve().parent.parent / "fixtures/tiny_python/repo"
            import os

            os.environ["CODELENS_ALLOW_LOCAL_ANALYSIS"] = "1"
            accepted = client.post("/api/analyze", json={"source": str(fixture)})
            check(
                "analyze returns a job id immediately",
                accepted.status_code == 202 and "job_id" in accepted.json(),
            )
            job_id = accepted.json()["job_id"]
            analyzed = None
            for _ in range(200):  # polls the status endpoint, never blocks on the pipeline
                status = client.get(f"/api/analyze/{job_id}")
                if status.json()["status"] in ("done", "error"):
                    analyzed = status
                    break
                time.sleep(0.02)
            os.environ.pop("CODELENS_ALLOW_LOCAL_ANALYSIS")
            ok = analyzed is not None and analyzed.json()["status"] == "done"
            check("analyze completes over HTTP (polled)", ok)
            if ok:
                assert analyzed is not None
                sid = analyzed.json()["snapshot_id"]
                vs = client.get(f"/api/repos/{sid}/viewspec", params={"zoom": 2})
                check("viewspec over HTTP", vs.status_code == 200,
                      f"{len(vs.json()['nodes'])} nodes")
                br = client.post(
                    f"/api/repos/{sid}/query/blast_radius",
                    json={"params": {"node_id": "function:calculator.add"}},
                )
                check("blast radius over HTTP",
                      br.status_code == 200 and br.json()["meta"]["total_affected"] == 5)
                # Both outcomes are correct, and which one you get depends on
                # whether a key happens to be configured. Asserting only the
                # 503 branch reported a red FAIL on a perfectly working
                # machine — the fastest way to teach someone to ignore this
                # script. What is actually being verified is that the answer
                # is never a crash: prose with a key, an honest 503 without.
                narrated = client.post(f"/api/repos/{sid}/answers/project")
                keyed = narrated.status_code == 200
                check(
                    "narration answers or 503s, never crashes",
                    keyed or narrated.status_code == 503,
                    "narrated (key configured)" if keyed else "503, no key configured",
                )
                lp = client.get(f"/api/repos/{sid}/answers/learning_path")
                check("learning path over HTTP, no key needed",
                      lp.status_code == 200 and lp.json()["model"] is None)
        routes._STORE.close()
        routes._STORE = None

    # ── verdict ───────────────────────────────────────────────────────────
    failed = [name for name, passed, _ in CHECKS if not passed]
    print(f"\n{'=' * 60}")
    print(f"CHECKS: {len(CHECKS) - len(failed)}/{len(CHECKS)} passed", end="")
    if failed:
        print(f"  — FAILED: {', '.join(failed)}")
        return 1
    print("  — the whole system works, end to end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
