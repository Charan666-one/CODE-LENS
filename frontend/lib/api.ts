import type {
  AnalyzeResponse,
  BlastResult,
  Explanation,
  QueryResult,
  ViewSpec,
} from "./types";

/** Thin fetchers. Components never call fetch directly — they read the
 *  store, and the store calls these. */

async function expectOk(response: Response): Promise<Response> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return response;
}

interface JobStatus {
  job_id: string;
  /** `interrupted` is what a server restart leaves behind. It is separate
   *  from `error` because nothing went wrong with the analysis — the process
   *  running it stopped existing — and "try again" is the right advice
   *  rather than "something failed". */
  status: "pending" | "running" | "done" | "error" | "interrupted";
  error?: string;
}

const ANALYZE_POLL_MS = 800;

/** Analyze returns a job id in milliseconds, always — a large monorepo can
 *  legitimately take a minute or more to clone and parse, and holding that
 *  open as one HTTP request is exactly what broke: Next's rewrite proxy
 *  aborts at 30s by default, and other layers between here and the server
 *  have their own limits. Polling this trivial status endpoint has no such
 *  ceiling, so repo size no longer decides whether analysis works. */
export async function analyzeRepo(source: string): Promise<AnalyzeResponse> {
  const accepted = await expectOk(
    await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    }),
  );
  const { job_id: jobId } = (await accepted.json()) as JobStatus;

  for (;;) {
    const response = await expectOk(
      await fetch(`/api/analyze/${jobId}`, { cache: "no-store" }),
    );
    const body = (await response.json()) as JobStatus & Partial<AnalyzeResponse>;
    if (body.status === "done") return body as AnalyzeResponse;
    if (body.status === "error") throw new Error(body.error ?? "Analysis failed.");
    if (body.status === "interrupted") {
      // Terminal, and it must be handled explicitly: without this branch the
      // loop polls a job that will never progress, forever.
      throw new Error(
        body.error ?? "The server restarted during this analysis. Please try again.",
      );
    }
    await new Promise((resolve) => setTimeout(resolve, ANALYZE_POLL_MS));
  }
}

export async function fetchViewSpec(
  snapshotId: number,
  zoom: number,
): Promise<ViewSpec> {
  const response = await expectOk(
    await fetch(`/api/repos/${snapshotId}/viewspec?zoom=${zoom}`, {
      cache: "no-store",
    }),
  );
  return response.json();
}

export async function fetchExplanation(
  snapshotId: number,
  nodeId: string,
): Promise<Explanation> {
  const response = await expectOk(
    await fetch(
      `/api/repos/${snapshotId}/explain?node_id=${encodeURIComponent(nodeId)}`,
      { cache: "no-store" },
    ),
  );
  return response.json();
}

/** Run any registered query plan.
 *
 *  The backend registry holds eleven plans and this file used to reach three,
 *  so `risk`, `centrality`, `dependencies`, `entrypoints`, `modules`,
 *  `hidden_coupling`, `untested_hubs`, `bus_factor` and `endpoints` all
 *  worked, were tested, and could not be seen. One generic caller is the whole
 *  fix: the endpoint has always been generic
 *  (`POST /repos/{id}/query/{name}`) — only the client was not.
 *
 *  Registering a plan on the backend now makes it reachable here with no
 *  frontend change, which is the property the registry was designed for.
 */
export async function runQuery<T = QueryResult>(
  snapshotId: number,
  name: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  const response = await expectOk(
    await fetch(`/api/repos/${snapshotId}/query/${name}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({ params }),
    }),
  );
  return response.json();
}

export function fetchBlastRadius(
  snapshotId: number,
  nodeId: string,
): Promise<BlastResult> {
  return runQuery<BlastResult>(snapshotId, "blast_radius", { node_id: nodeId });
}

/** Concept search — deterministic, no API key, already on the backend. */
export async function searchRepo(
  snapshotId: number,
  text: string,
  top = 12,
): Promise<QueryResult> {
  const response = await expectOk(
    await fetch(`/api/repos/${snapshotId}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({ text, top }),
    }),
  );
  return response.json();
}
