"use client";

import { create } from "zustand";
import { analyzeRepo, fetchViewSpec } from "./api";
import type { PipelineStage, ViewSpec } from "./types";

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

  analyze: (source: string) => Promise<void>;
  setZoom: (zoom: number) => Promise<void>;
  advanceStage: () => void;
  beginReveal: () => void;
  advanceReveal: () => void;
  skipReveal: () => void; // every animation interruptible (EXPERIENCE)
  select: (id: string | null) => void;
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

  select: (id) => set({ selectedId: id }),
}));
