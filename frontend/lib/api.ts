import type { AnalyzeResponse, BlastResult, ViewSpec } from "./types";

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
  status: "pending" | "running" | "done" | "error";
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

export async function fetchBlastRadius(
  snapshotId: number,
  nodeId: string,
): Promise<BlastResult> {
  const response = await expectOk(
    await fetch(`/api/repos/${snapshotId}/query/blast_radius`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({ params: { node_id: nodeId } }),
    }),
  );
  return response.json();
}
