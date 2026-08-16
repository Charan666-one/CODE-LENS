import type { BlastResult, EndpointRef } from "./types";

/** What the impact wave has reached *so far*.
 *
 *  Every number here is recomputed from the wavefront on each tick, so the
 *  readout always describes exactly what is lit on the canvas. A total shown
 *  while the wave is still travelling would be a caption that disagrees with
 *  the picture — and the picture is the argument.
 *
 *  Deliberately **not** included: "critical paths". It appears in every
 *  dashboard of this kind and almost never has a definition; a count that
 *  sounds precise and cannot be derived is worse than one fewer number. What
 *  replaces it is `endpoints` — the HTTP routes a change here actually
 *  reaches — which is both defined and the thing someone deciding whether to
 *  deploy is really asking about.
 */
export interface ImpactCounts {
  files: number;
  modules: number;
  endpoints: number;
  /** How far the wave has travelled, and how far it has left to go. */
  hops: number;
  maxHops: number;
  stage: "direct" | "indirect" | "complete";
}

/** `src/flask/app.py` -> `src/flask`. */
function moduleOf(filePath: string): string | null {
  if (!filePath.includes("/")) return null;
  return filePath.slice(0, filePath.lastIndexOf("/"));
}

export function impactCounts(
  blast: BlastResult | null,
  front: number,
  endpoints: EndpointRef[],
): ImpactCounts {
  if (!blast) {
    return { files: 0, modules: 0, endpoints: 0, hops: 0, maxHops: 0, stage: "direct" };
  }

  const maxHops = Math.max(
    1,
    ...blast.ranked.map((entry) => entry.reasons.distance),
  );

  const reached = blast.ranked.filter((entry) => entry.reasons.distance <= front);

  // Counted by distinct FILE, not by node. Selecting a function returns
  // function-level dependents, and reporting "12 files" when twelve methods
  // in three files are affected is a wrong number stated confidently — the
  // one kind of mistake this product cannot afford.
  const files = new Set<string>();
  const modules = new Set<string>();
  for (const entry of reached) {
    const path = entry.reasons.file_path;
    if (!path) continue;
    files.add(path);
    const module = moduleOf(path);
    if (module) modules.add(module);
  }

  return {
    files: files.size,
    modules: modules.size,
    // Endpoints are a property of the whole blast radius rather than of a
    // ring, so they only count once the wave has actually arrived everywhere.
    endpoints: front >= maxHops ? endpoints.length : 0,
    hops: Math.min(front, maxHops),
    maxHops,
    stage: front <= 1 ? "direct" : front >= maxHops ? "complete" : "indirect",
  };
}

export const STAGE_COPY: Record<ImpactCounts["stage"], string> = {
  direct: "what calls it directly",
  indirect: "and what calls those",
  complete: "everything downstream",
};
