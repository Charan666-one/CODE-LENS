/** Mirrors of the backend's ViewSpec contract (app/views/viewspec.py).
 *  The frontend renders these; it never computes truth. */

export interface ViewNode {
  id: string;
  label: string;
  kind: string;
  x: number;
  y: number;
  size: number;
  color: string;
  cluster: string;
  assembly_index: number;
  risk: number;
  fan_in: number;
  is_entrypoint: boolean;
  file_path: string | null;
  start_line: number | null;
}

export interface ViewEdge {
  source: string;
  target: string;
  kind: string;
  weight: number;
  confidence: "resolved" | "heuristic" | "dynamic_unknown";
}

export interface ViewCluster {
  id: string;
  label: string;
  x: number;
  y: number;
  radius: number;
  color: string;
  members: number;
}

export interface ViewSpec {
  zoom: number;
  repo_url: string;
  commit_sha: string;
  nodes: ViewNode[];
  edges: ViewEdge[];
  clusters: ViewCluster[];
  meta: Record<string, unknown>;
}

export interface PipelineStage {
  stage: string;
  seconds: number;
  skipped: boolean;
}

export interface AnalyzeResponse {
  snapshot_id: number;
  repo_url: string;
  commit_sha: string;
  skipped: boolean;
  stages: PipelineStage[];
  nodes: number;
  edges: number;
}

export interface RankedNode {
  node_id: string;
  score: number;
  reasons: {
    distance: number;
    fan_in: number;
    path_confidence: "resolved" | "heuristic" | "dynamic_unknown";
  };
}

/** The blast_radius ResultGraph — the facts the ripple animates. */
export interface BlastResult {
  focus_id: string;
  node_ids: string[];
  ranked: RankedNode[];
  paths: Record<string, string[]>;
  meta: { total_affected: number };
}
