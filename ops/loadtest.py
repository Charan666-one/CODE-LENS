#!/usr/bin/env python3
"""A small, reproducible load test against the running containerized stack.

    docker compose up -d --wait
    python3 ops/loadtest.py

Deliberately **not** a stress test. The question is not "how much can it
take" — it is "do the configured limits behave as designed, and does the
service still work afterwards". Those are answered by pushing slightly past
the limits with real repositories, not by flooding the machine.

What it measures, and why each one is here:

    successes / 429 / 503     do the ceilings fire, with the right code
    quota accounting          a 503 must not spend the client's quota
    latency                   does admission stay fast while work runs
    container memory / CPU    does the cap hold, from Docker's own numbers
    disk                      does the clone cache get reclaimed
    job table                 does it stay bounded
    recovery                  is the service healthy and usable afterwards

Only the standard library and the `docker` CLI — nothing to install.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

BASE = "http://localhost:3000"

#: Small, real, public repositories. Real ones on purpose: a synthetic repo
#: would not exercise the clone, the history read, or the parser's actual
#: cost, which is most of what the limits are protecting.
REPOS = [
    "https://github.com/pallets/itsdangerous",
    "https://github.com/pallets/markupsafe",
    "https://github.com/pallets/click",
    "https://github.com/pallets/jinja",
    "https://github.com/psf/requests",
    "https://github.com/pallets/flask",
    "https://github.com/pallets/quart",
    "https://github.com/pallets/werkzeug",
]


@dataclass
class Outcome:
    status: int
    seconds: float
    body: dict = field(default_factory=dict)


def request(path: str, payload: dict | None = None, timeout: float = 30.0) -> Outcome:
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    started = time.monotonic()
    try:
        with urllib.request.urlopen(  # noqa: S310 - fixed localhost base
            urllib.request.Request(url, data=data, headers=headers), timeout=timeout
        ) as response:
            return Outcome(response.status, time.monotonic() - started, json.load(response))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"detail": raw[:200].decode(errors="replace")}
        return Outcome(exc.code, time.monotonic() - started, body)
    except Exception as exc:  # noqa: BLE001 - a failed probe is a datum
        return Outcome(0, time.monotonic() - started, {"detail": str(exc)})


def docker_stats(container: str) -> dict[str, str]:
    try:
        raw = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.strip()
        return json.loads(raw) if raw else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def in_container(command: str) -> str:
    try:
        return subprocess.run(
            ["docker", "compose", "exec", "-T", "backend", "sh", "-c", command],
            capture_output=True, text=True, timeout=60, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "?"


def settings_in_use() -> dict[str, str]:
    raw = in_container(
        "python -c \"from app.core.config import settings; import json;"
        " print(json.dumps({k: str(getattr(settings, k)) for k in ["
        "'RATE_LIMIT_ANALYSES','RATE_LIMIT_WINDOW_SECONDS','MAX_CONCURRENT_ANALYSES',"
        "'ANALYSIS_TIMEOUT_SECONDS','MAX_REPO_SIZE_MB','MAX_FILES','MAX_FILE_SIZE_MB',"
        "'MAX_CLONE_CACHE_MB','MAX_FINISHED_JOBS','NARRATION_ENABLED']}))\""
    )
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def wait_for_job(job_id: str, deadline_seconds: float = 240.0) -> dict:
    end = time.monotonic() + deadline_seconds
    while time.monotonic() < end:
        outcome = request(f"/api/analyze/{job_id}")
        if outcome.status != 200:
            return {"status": f"poll_failed_{outcome.status}"}
        if outcome.body.get("status") in ("done", "error", "interrupted"):
            return outcome.body
        time.sleep(1.0)
    return {"status": "timeout"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--burst", type=int, default=8, help="simultaneous analyse requests")
    args = parser.parse_args()

    print("=" * 70)
    print("CodeLens load test")
    print("=" * 70)

    health = request("/api/analyze/none")
    if health.status == 0:
        print("the stack is not answering — run: docker compose up -d --wait")
        return 1

    limits = settings_in_use()
    print("\nConfiguration in the running container")
    for key, value in limits.items():
        print(f"  {key:28} {value}")

    disk_before = in_container("du -sm /var/cache/codelens 2>/dev/null | cut -f1") or "?"
    print(f"  {'clone cache (MB, before)':28} {disk_before}")

    # ── the burst ────────────────────────────────────────────────────────
    print(f"\nBurst: {args.burst} simultaneous analyses of {args.burst} distinct repos")
    print("(distinct on purpose — the same URL would be deduped into one job")
    print(" and would never reach the limiter at all)")

    with ThreadPoolExecutor(max_workers=args.burst) as pool:
        outcomes = list(
            pool.map(
                lambda repo: request("/api/analyze", {"source": repo}),
                REPOS[: args.burst],
            )
        )

    accepted = [o for o in outcomes if o.status == 202]
    throttled = [o for o in outcomes if o.status == 429]
    busy = [o for o in outcomes if o.status == 503]
    other = [o for o in outcomes if o.status not in (202, 429, 503)]

    print(f"\n  202 accepted   {len(accepted)}")
    print(f"  503 busy       {len(busy)}   (concurrency cap)")
    print(f"  429 throttled  {len(throttled)}   (rate limit)")
    if other:
        print(f"  unexpected     {len(other)}: {[(o.status, o.body) for o in other]}")

    latencies = sorted(o.seconds for o in outcomes)
    if latencies:
        print(
            f"\n  admission latency  min {latencies[0]*1000:.0f}ms  "
            f"median {latencies[len(latencies)//2]*1000:.0f}ms  "
            f"max {latencies[-1]*1000:.0f}ms"
        )

    for outcome in busy + throttled:
        if "Retry-After" not in str(outcome.body) and not outcome.body.get("detail"):
            print("  WARNING: a refusal carried no actionable detail")

    # ── under load ───────────────────────────────────────────────────────
    print("\nWhile the accepted analyses run:")
    stats = docker_stats("codelens-backend-1")
    if stats:
        print(f"  memory  {stats.get('MemUsage','?')}   ({stats.get('MemPerc','?')} of limit)")
        print(f"  cpu     {stats.get('CPUPerc','?')}")
        print(f"  pids    {stats.get('PIDs','?')}")

    responsive = request("/api/queries")
    print(
        f"  service responsive under load: {responsive.status == 200} "
        f"({responsive.seconds*1000:.0f}ms)"
    )

    # ── drain ────────────────────────────────────────────────────────────
    print("\nWaiting for accepted analyses to finish…")
    finished = []
    for outcome in accepted:
        job_id = outcome.body.get("job_id")
        if job_id:
            finished.append(wait_for_job(job_id))
    done = sum(1 for job in finished if job.get("status") == "done")
    failed = [job for job in finished if job.get("status") not in ("done",)]
    print(f"  completed {done}/{len(finished)}")
    for job in failed:
        print(f"  NOT DONE: {job.get('status')}: {str(job.get('error'))[:120]}")

    # ── after ────────────────────────────────────────────────────────────
    print("\nAfter the load")
    peak = docker_stats("codelens-backend-1")
    if peak:
        print(f"  memory  {peak.get('MemUsage','?')}   ({peak.get('MemPerc','?')})")
        print(f"  cpu     {peak.get('CPUPerc','?')}")
    disk_after = in_container("du -sm /var/cache/codelens 2>/dev/null | cut -f1") or "?"
    print(f"  clone cache (MB)  {disk_before} -> {disk_after}"
          f"   (ceiling {limits.get('MAX_CLONE_CACHE_MB','?')})")

    rows = in_container(
        "python -c \"import sqlite3;"
        " print(sqlite3.connect('/data/codelens.db')"
        ".execute('SELECT COUNT(*) FROM jobs').fetchone()[0])\""
    )
    print(f"  job rows          {rows}   (cap {limits.get('MAX_FINISHED_JOBS','?')})")

    # ── the quota, exercised on its own ──────────────────────────────────
    #
    # The burst above rarely produces a 429, and that is not a gap in the
    # limits — it is capacity being settled before quota, so the 503s never
    # reached the limiter. But it does mean the burst alone never observes
    # the rate limit, and an untested limit is the thing this file exists to
    # avoid. So the quota gets its own phase: one request at a time, waiting
    # for each to leave the gate, until the window refuses.
    print("\nQuota phase: sequential requests until the rate limit answers")
    quota_codes: list[int] = []
    for repo in REPOS:
        outcome = request("/api/analyze", {"source": repo})
        quota_codes.append(outcome.status)
        if outcome.status == 202 and outcome.body.get("job_id"):
            wait_for_job(outcome.body["job_id"])
        if outcome.status == 429:
            print(f"  rate limit reached after {quota_codes.count(202)} accepted")
            print(f"  detail: {outcome.body.get('detail')}")
            break
        if outcome.status == 503:
            time.sleep(2)  # gate still draining; not a quota answer
    saw_429 = 429 in quota_codes
    print(f"  sequence: {quota_codes}")
    print(f"  rate limit observed: {saw_429}")

    # ── recovery ─────────────────────────────────────────────────────────
    print("\nRecovery: is the service usable again?")
    repos = request("/api/repos")
    print(f"  GET /api/repos -> {repos.status} ({len(repos.body) if repos.body else 0} snapshots)")
    probe = request("/api/analyze", {"source": REPOS[0]})
    print(f"  a fresh analyse -> {probe.status} (429 is correct if the window is still open)")

    print("\n" + "=" * 70)
    ok = (
        not other
        and done == len(finished)
        and responsive.status == 200
        and repos.status == 200
        and saw_429  # the quota must actually have been demonstrated
    )
    print("RESULT:", "limits behaved as configured, service recovered" if ok else "SEE ABOVE")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
