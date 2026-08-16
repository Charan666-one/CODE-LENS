#!/usr/bin/env bash
#
# Restore a CodeLens backup.
#
#   ops/restore.sh <backup.db>                 # into the live stack
#   ops/restore.sh <backup.db> --verify-only   # into a throwaway container
#
# `--verify-only` is the mode that matters and the reason this script exists
# in two halves. A backup nobody has restored is a hypothesis, so the verify
# path restores into a *clean, disposable* volume and asks a real CodeLens
# process to read it — no live data touched, nothing to undo if it fails.
# Run it after every change to the schema or the backup script.
#
# The live path stops the stack first. Restoring underneath a running process
# means overwriting a file SQLite has open, which produces corruption that
# looks like a CodeLens bug rather than like what it is.

set -euo pipefail

ARCHIVE="${1:-}"
MODE="${2:-}"

say() { printf "\033[36m▸ %s\033[0m\n" "$1"; }
die() { printf "\033[31m✗ %s\033[0m\n" "$1" >&2; exit 1; }

[ -n "$ARCHIVE" ] || die "usage: ops/restore.sh <backup.db> [--verify-only]"
[ -f "$ARCHIVE" ] || die "no such backup: $ARCHIVE"

say "checking the archive before trusting it"
python3 - "$ARCHIVE" <<'PY'
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    raise SystemExit("archive failed integrity_check; refusing to restore it")
tables = {row[0] for row in connection.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
missing = {"snapshots", "nodes", "edges"} - tables
if missing:
    raise SystemExit(f"archive is missing tables: {sorted(missing)}")
print("   archive is a valid CodeLens database")
PY

if [ "$MODE" = "--verify-only" ]; then
  VOLUME="codelens-restore-test-$$"
  say "restoring into a throwaway volume ($VOLUME) — live data untouched"
  docker volume create "$VOLUME" >/dev/null
  # shellcheck disable=SC2064
  trap "docker volume rm -f '$VOLUME' >/dev/null 2>&1 || true" EXIT

  docker run --rm -i -v "$VOLUME:/data" alpine:3 \
    sh -c 'cat > /data/codelens.db' < "$ARCHIVE"

  say "asking a real CodeLens process to read it"
  docker run --rm -v "$VOLUME:/data" \
    -e SQLITE_PATH=/data/codelens.db \
    codelens-backend:latest \
    python -c "
from app.graph.store import SQLiteGraphStore
store = SQLiteGraphStore('/data/codelens.db')
snapshots = store.list_snapshots()
print(f'   {len(snapshots)} snapshot(s) in the restored database')
if not snapshots:
    raise SystemExit('restored database has no snapshots; nothing was verified')
first = snapshots[0]
graph = store.load_graph_by_id(first['snapshot_id'])
if graph is None:
    raise SystemExit('snapshot row exists but its graph would not load')
print(f\"   loaded '{first['repo_url']}': {len(graph.nodes):,} nodes, {len(graph.edges):,} edges\")
from app.graph.traversal import GraphView
from app.queries import run_query
view = GraphView(graph)
# A node that actually HAS dependents. Picking an arbitrary file often finds
# a leaf, and '0 ranked' would then look identical to a restore that silently
# lost every edge — the exact failure this verification exists to catch.
best, best_count = None, 0
for node in graph.nodes:
    count = len(view.dependents_of(node.id, {'calls', 'imports'})) if view.has_node(node.id) else 0
    if count > best_count:
        best, best_count = node.id, count
if best is None:
    raise SystemExit('restored graph has no node with dependents; edges did not survive')
result = run_query('blast_radius', view, node_id=best)
if not result.ranked:
    raise SystemExit('blast_radius returned nothing over restored data')
print(f'   blast_radius on {best}: {len(result.ranked)} ranked (query works on restored data)')
store.close()
"
  printf "\033[32m✓ restore verified: a real CodeLens process read and queried it\033[0m\n"
  exit 0
fi

say "stopping the stack (never restore under a live SQLite handle)"
docker compose down

say "writing the archive into codelens-data"
docker run --rm -i -v codelens-data:/data alpine:3 \
  sh -c 'cat > /data/codelens.db' < "$ARCHIVE"

say "starting the stack"
docker compose up -d --wait --wait-timeout 180

printf "\033[32m✓ restored %s into the live stack\033[0m\n" "$ARCHIVE"
