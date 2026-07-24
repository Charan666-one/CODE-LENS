#!/usr/bin/env bash
#
# CodeLens — one command to run the whole thing (CP-5.1 demo launcher).
#
#   ./run.sh          production build, the way a real user should see it
#   ./run.sh --dev    dev servers with hot reload (for hacking, NOT for demos:
#                     Next's Fast Refresh resets the page mid-analysis)
#
# Starts the FastAPI backend and the Next.js frontend, waits until both answer,
# opens the browser, and cleans both up on Ctrl+C. No arguments, no fuss —
# because the person you are demoing to should never see a terminal.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
API_PORT=8000
WEB_PORT=3000
DEV_MODE=false
[[ "${1:-}" == "--dev" ]] && DEV_MODE=true

say() { printf "\033[36m▸ %s\033[0m\n" "$1"; }
die() { printf "\033[31m✗ %s\033[0m\n" "$1" >&2; exit 1; }

# ── preflight ──────────────────────────────────────────────────────────────
[[ -x "$BACKEND/.venv/bin/uvicorn" ]] || die "backend venv missing — see RUNBOOK.md §1"
command -v npm >/dev/null || die "npm not found — install Node.js"
[[ -d "$FRONTEND/node_modules" ]] || { say "installing frontend deps (first run)…"; (cd "$FRONTEND" && npm install); }

PIDS=()
cleanup() {
  say "shutting down…"
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  # belt and suspenders: our two servers by name
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "next start" 2>/dev/null || true
  pkill -f "next dev" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# free the ports if a previous run left something behind
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "next start" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
sleep 1

# ── backend ────────────────────────────────────────────────────────────────
say "starting backend on :$API_PORT"
( cd "$BACKEND" && .venv/bin/uvicorn app.main:app --port "$API_PORT" >/tmp/codelens-api.log 2>&1 ) &
PIDS+=($!)

for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 || die "backend did not come up — see /tmp/codelens-api.log"
say "backend ready"

# ── frontend ───────────────────────────────────────────────────────────────
if $DEV_MODE; then
  say "starting frontend (dev, hot reload) on :$WEB_PORT"
  ( cd "$FRONTEND" && npm run dev >/tmp/codelens-web.log 2>&1 ) &
else
  say "building frontend (production)…"
  ( cd "$FRONTEND" && npm run build >/tmp/codelens-build.log 2>&1 ) || die "frontend build failed — see /tmp/codelens-build.log"
  say "starting frontend on :$WEB_PORT"
  ( cd "$FRONTEND" && npm run start >/tmp/codelens-web.log 2>&1 ) &
fi
PIDS+=($!)

for _ in $(seq 1 40); do
  curl -fsS "http://127.0.0.1:$WEB_PORT" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS "http://127.0.0.1:$WEB_PORT" >/dev/null 2>&1 || die "frontend did not come up — see /tmp/codelens-web.log"

URL="http://localhost:$WEB_PORT"
printf "\n\033[32m✓ CodeLens is live:  %s\033[0m\n" "$URL"
$DEV_MODE && printf "\033[33m  (dev mode — for demos use ./run.sh without --dev)\033[0m\n"
printf "  Ctrl+C to stop.\n\n"
command -v open >/dev/null && open "$URL" 2>/dev/null || true

# Hold the terminal until Ctrl+C, or until a server dies — then the trap
# cleans up. Written as a poll loop, not `wait -n`, because macOS still ships
# bash 3.2 (from 2007), which has no `wait -n`.
while true; do
  for pid in "${PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null || { say "a server exited — shutting down the other"; exit 1; }
  done
  sleep 2
done
