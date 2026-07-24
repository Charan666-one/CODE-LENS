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

export async function analyzeRepo(source: string): Promise<AnalyzeResponse> {
  const response = await expectOk(
    await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    }),
  );
  return response.json();
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
