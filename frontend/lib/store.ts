"use client";

import { create } from "zustand";
import {
  analyzeRepo,
  fetchBlastRadius,
  fetchExplanation,
  fetchViewSpec,
  runQuery,
} from "./api";
import type {
  BlastResult,
  Explanation,
  PipelineStage,
  QueryResult,
  ViewSpec,
} from "./types";

/** The Graph State Manager (ARCHITECTURE.md: the game-engine model).
 *
 *  One client-side state; every panel reads it; components never fetch on
 *  their own. The phase machine IS the hero moment:
 *
 *    idle -> understanding -> revealing -> exploring
 *
 *  "understanding" replays the pipeline's REAL stages (names and timings come
 *  from the backend's telemetry — honest theater: we replay, never invent).
 *  "revealing" is the assembly animation in real construction order.
 */

export type Phase = "idle" | "understanding" | "revealing" | "exploring";

interface GraphState {
  phase: Phase;
  error: string | null;

  snapshotId: number | null;
  repoUrl: string | null;
  stages: PipelineStage[];
  stagesShown: number; // how many stage lines the overlay has revealed

  zoom: number;
  spec: ViewSpec | null;

  /** Assembly reveal: nodes with assembly_index <= revealIndex are visible. */
  revealIndex: number;
  maxAssemblyIndex: number;

  selectedId: string | null;

  /** The ripple: blast radius felt as an expanding wave. `rippleFront` is the
   *  distance the wave has reached; a node lights when its distance <= front. */
  blast: BlastResult | null;
  rippleFor: string | null;
  rippleFront: number;
  maxRippleDistance: number;

  analyze: (source: string) => Promise<void>;
  setZoom: (zoom: number) => Promise<void>;
  advanceStage: () => void;
  beginReveal: () => void;
  advanceReveal: () => void;
  skipReveal: () => void; // every animation interruptible (EXPERIENCE)
  select: (id: string | null) => void;
  showRipple: (nodeId: string) => Promise<void>;
  advanceRipple: () => void;
  clearRipple: () => void;

  /** Why the project needs the selected file or folder. Deterministic facts,
   *  no API key required.
   *
   *  Fetched by `select` rather than by a button: the inspector shows real
   *  numbers the instant something is selected, and there is no state where a
   *  panel is open but empty waiting for a click. */
  explanation: Explanation | null;
  explaining: string | null;
  explainError: string | null;
  /** Whether the inspector is showing the full detail or just the summary.
   *  The deep content is one click away, never permanently on screen. */
  detailOpen: boolean;
  toggleDetail: () => void;
  closeInspector: () => void;

  /** ⌘K. The whole point of a minimal interface is that power lives here
   *  rather than in permanent chrome. */
  paletteOpen: boolean;
  setPalette: (open: boolean) => void;

  /** A query's answer, drawn ON the graph.
   *
   *  This is the rule that keeps the product one canvas: a query never opens
   *  a table. `cycles` isolates its loops, `risk` lights the risky files,
   *  `untested_hubs` shows the gap. The overlay is just a set of node ids the
   *  renderer treats as "the answer"; everything else recedes. */
  overlay: Overlay | null;
  runOverlay: (name: string, label: string, params?: Record<string, unknown>) => Promise<void>;
  clearOverlay: () => void;
  overlayError: string | null;
}

export interface Overlay {
  query: string;
  label: string;
  /** Every node the answer touches. */
  nodeIds: string[];
  /** For findings made of several distinct groups — each cycle is one group —
   *  so the renderer can tell them apart instead of showing one blob. */
  groups: string[][];
  /** One line of plain English, from the query's own `explanation`. */
  detail: string;
  count: number;
}

export const useGraphStore = create<GraphState>((set, get) => ({
  phase: "idle",
  error: null,
  snapshotId: null,
  repoUrl: null,
  stages: [],
  stagesShown: 0,
  zoom: 2,
  spec: null,
  revealIndex: -1,
  maxAssemblyIndex: 0,
  selectedId: null,
  blast: null,
  rippleFor: null,
  rippleFront: 0,
  maxRippleDistance: 0,

  analyze: async (source: string) => {
    set({ phase: "understanding", error: null, stages: [], stagesShown: 0, spec: null });
    try {
      const result = await analyzeRepo(source);
      const spec = await fetchViewSpec(result.snapshot_id, get().zoom);
      const maxAssembly = Math.max(0, ...spec.nodes.map((n) => n.assembly_index));
      set({
        snapshotId: result.snapshot_id,
        repoUrl: result.repo_url,
        stages: result.stages,
        spec,
        maxAssemblyIndex: maxAssembly,
        revealIndex: -1,
        selectedId: null,
        blast: null,
        rippleFor: null,
      });
      // The overlay now replays the real stages; it calls beginReveal() when
      // the last line has landed.
    } catch (error) {
      set({ phase: "idle", error: (error as Error).message });
    }
  },

  setZoom: async (zoom: number) => {
    const { snapshotId } = get();
    if (snapshotId === null) return;
    const spec = await fetchViewSpec(snapshotId, zoom);
    set({
      zoom,
      spec,
      // Zoom switches are instant: the reveal belongs to the first arrival.
      revealIndex: Math.max(0, ...spec.nodes.map((n) => n.assembly_index)),
      maxAssemblyIndex: Math.max(0, ...spec.nodes.map((n) => n.assembly_index)),
      // A ripple is tied to one zoom's node ids; drop it on a level change.
      blast: null,
      rippleFor: null,
      selectedId: null,
    });
  },

  advanceStage: () => set((s) => ({ stagesShown: Math.min(s.stagesShown + 1, s.stages.length) })),

  beginReveal: () => set({ phase: "revealing", revealIndex: 0 }),

  advanceReveal: () => {
    const { revealIndex, maxAssemblyIndex } = get();
    if (revealIndex >= maxAssemblyIndex) {
      set({ phase: "exploring" });
      return;
    }
    set({ revealIndex: revealIndex + 1 });
  },

  skipReveal: () =>
    set((s) => ({ phase: "exploring", revealIndex: s.maxAssemblyIndex })),

  // Selecting a different node ends any ripple in progress, and immediately
  // asks the graph what this node is. A cluster is a view-layer invention
  // with no node of its own, so the question goes to its `explain_id`.
  select: (id) => {
    set({
      selectedId: id,
      blast: null,
      rippleFor: null,
      explanation: null,
      explainError: null,
      detailOpen: false,
      explaining: id,
    });
    if (id === null) return;
    const { snapshotId, spec } = get();
    if (snapshotId === null) return;
    const node = spec?.nodes.find((candidate) => candidate.id === id);
    const target = node?.explain_id ?? id;
    void fetchExplanation(snapshotId, target)
      .then((explanation) => {
        // Ignore a stale response if the user moved on to another node.
        if (get().selectedId === id) set({ explanation });
      })
      .catch((error: Error) => {
        if (get().selectedId === id) set({ explainError: error.message });
      });
  },

  showRipple: async (nodeId: string) => {
    const { snapshotId } = get();
    if (snapshotId === null) return;
    const blast = await fetchBlastRadius(snapshotId, nodeId);
    const maxDistance = Math.max(
      1,
      ...blast.ranked.map((entry) => entry.reasons.distance),
    );
    set({
      blast,
      rippleFor: nodeId,
      selectedId: nodeId,
      rippleFront: 0, // the wave starts at the source and expands outward
      maxRippleDistance: maxDistance,
    });
  },

  advanceRipple: () => {
    const { rippleFront, maxRippleDistance } = get();
    if (rippleFront >= maxRippleDistance) return; // wave has reached the edge
    set({ rippleFront: rippleFront + 1 });
  },

  clearRipple: () => set({ blast: null, rippleFor: null, rippleFront: 0 }),

  explanation: null,
  explaining: null,
  explainError: null,
  detailOpen: false,

  toggleDetail: () => set((s) => ({ detailOpen: !s.detailOpen })),

  closeInspector: () =>
    set({
      selectedId: null,
      explanation: null,
      explaining: null,
      explainError: null,
      detailOpen: false,
      blast: null,
      rippleFor: null,
    }),

  paletteOpen: false,
  setPalette: (open) => set({ paletteOpen: open }),

  overlay: null,
  overlayError: null,

  runOverlay: async (name, label, params = {}) => {
    const { snapshotId } = get();
    if (snapshotId === null) return;
    set({ paletteOpen: false, overlayError: null, selectedId: null, blast: null, rippleFor: null });
    try {
      const result = await runQuery(snapshotId, name, params);
      const groups = groupsFor(name, result);
      const nodeIds = groups.length > 0 ? [...new Set(groups.flat())] : result.node_ids;
      set({
        overlay: {
          query: name,
          label,
          nodeIds,
          groups,
          detail: typeof result.meta.explanation === "string" ? result.meta.explanation : "",
          count:
            typeof result.meta.total === "number" ? result.meta.total : nodeIds.length,
        },
      });
      // Findings are about files, and L1 draws districts. Dropping to the
      // level where the answer is actually visible is the difference between
      // an overlay and a shrug.
      if (nodeIds.length > 0 && get().zoom === 1) await get().setZoom(2);
    } catch (error) {
      set({ overlayError: (error as Error).message });
    }
  },

  clearOverlay: () => set({ overlay: null, overlayError: null }),
}));

/** Some answers are made of distinct groups rather than one set. A cycle is
 *  only meaningful as a loop, so the renderer needs them kept apart. */
function groupsFor(name: string, result: QueryResult): string[][] {
  if (name !== "cycles") return [];
  const cycles = result.meta.cycles;
  if (!Array.isArray(cycles)) return [];
  return cycles.map((cycle) =>
    ((cycle as { files?: { id: string }[] }).files ?? []).map((file) => file.id),
  );
}
