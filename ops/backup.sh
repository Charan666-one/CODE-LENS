#!/usr/bin/env bash
#
# Back up CodeLens's durable state.
#
#   ops/backup.sh [destination-directory]     # default: ./backups
#
# ## What is backed up, and what deliberately is not
#
# `codelens-data` holds one SQLite file with everything that would be
# expensive or impossible to recreate:
#
#   snapshots      analysed repositories (the graph, as JSON per row)
#   nodes/edges    the graph itself
#   annotations    LLM summaries — paid for, keyed by content hash
#   summary_cache  the same, outside the snapshot cascade so re-analysis
#                  never throws away output someone paid for
#   jobs           analysis job state (see backend/app/core/job_store.py)
#
# `codelens-clones` is NOT backed up. It is a cache of public repositories:
# every byte can be re-fetched from GitHub, it is the larger of the two
# volumes by far, and restoring a stale working tree would be worse than
# having none. Backing it up would cost gigabytes to save nothing.
#
# ## Why `sqlite3 .backup` and not `cp`
#
# The database is being written while this runs. Copying the file gives you
# whatever bytes happened to be on disk mid-transaction, which restores as a
# "database disk image is malformed" at the worst possible moment. `.backup`
# takes a consistent snapshot of a live database — that is the whole reason
# it exists. The result is then verified with `PRAGMA integrity_check` before
# this script will call it a backup.
#
# No credentials are read or written here. The database contains no secrets:
# API keys live in the environment, and author identities are stored as
# digests (backend/app/graph/ownership.py).

set -euo pipefail

DEST="${1:-./backups}"
SERVICE="backend"
DB_IN_CONTAINER="/data/codelens.db"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$DEST/codelens-$STAMP.db"

say() { printf "\033[36m▸ %s\033[0m\n" "$1"; }
die() { printf "\033[31m✗ %s\033[0m\n" "$1" >&2; exit 1; }

command -v docker >/dev/null || die "docker not found"
docker compose ps --status running --format '{{.Service}}' | grep -qx "$SERVICE" \
  || die "the $SERVICE container is not running (docker compose up -d)"

mkdir -p "$DEST"
# 0700: a backup is a full copy of every analysed repository's structure.
# World-readable backups are how a private thing becomes a public one.
chmod 700 "$DEST"

say "snapshotting $DB_IN_CONTAINER (consistent, while live)"
# The container is read-only apart from its volumes, so the snapshot is
# written into /tmp — which is a tmpfs and therefore never persisted.
docker compose exec -T "$SERVICE" python -c "
import sqlite3
source = sqlite3.connect('$DB_IN_CONTAINER')
target = sqlite3.connect('/tmp/backup.db')
with target:
    source.backup(target)
problem = target.execute('PRAGMA integrity_check').fetchone()[0]
target.close(); source.close()
if problem != 'ok':
    raise SystemExit('integrity_check failed: ' + problem)
print('integrity_check ok')
"

say "copying out to $ARCHIVE"
docker compose exec -T "$SERVICE" cat /tmp/backup.db > "$ARCHIVE"
docker compose exec -T "$SERVICE" rm -f /tmp/backup.db
chmod 600 "$ARCHIVE"

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
say "verifying the copy on this side"
python3 - "$ARCHIVE" <<'PY'
import sqlite3, sys
path = sys.argv[1]
connection = sqlite3.connect(path)
if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    raise SystemExit("restored file failed integrity_check")
counts = {
    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    for table in ("snapshots", "nodes", "edges", "annotations", "jobs")
}
connection.close()
print("   " + "  ".join(f"{name}={value:,}" for name, value in counts.items()))
PY

printf "\033[32m✓ backup complete: %s (%s)\033[0m\n" "$ARCHIVE" "$SIZE"
printf "  restore with: ops/restore.sh %s\n" "$ARCHIVE"
