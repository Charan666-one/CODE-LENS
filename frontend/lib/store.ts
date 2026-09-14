"use client";

import { create } from "zustand";
import {
  analyzeRepo,
  fetchBlastRadius,
  fetchExplanation,
  fetchViewSpec,
  runQuery,
} from "./api";
import type { SearchHit } from "./search";
import type {
  BlastResult,
  EndpointRef,
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

export type Dimension = "2d" | "3d";

/** Where the reader's choice of view is kept between visits. */
export const DIMENSION_KEY = "codelens.dimension";

interface GraphState {
  phase: Phase;
  error: string | null;

  /** Something the reader asked for did not happen, in one line.
   *
   *  `error` belongs to the landing page and is only ever seen while idle, so
   *  every failure *after* a graph exists had nowhere to go: a level change, a
   *  dive, a query and an impact all rejected into an unhandled promise and
   *  left the interface looking exactly as if the click had been ignored.
   *  Which is the worst way for software to fail — the reader concludes the
   *  button is broken, or that they imagined pressing it.
   */
  notice: string | null;
  clearNotice: () => void;

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
  /** The HTTP routes this change reaches. Fetched beside the blast radius so
   *  the readout can say `POST /checkout` rather than a file count — the
   *  difference between a number to interpret and a decision. */
  rippleEndpoints: EndpointRef[];

  analyze: (source: string) => Promise<void>;
  /** Resolves `false` when the level could not be loaded, so a caller in the
   *  middle of a longer move — a dive, a search landing — can stop rather
   *  than carry on against a level that never arrived. */
  setZoom: (zoom: number) => Promise<boolean>;
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

  /** Flat or deep.
   *
   *  Not a different product — the same ViewSpec, the same choreography, the
   *  same encodings. The deep view adds one axis the flat one cannot draw:
   *  height is how far down the import stack a file sits. A preference, and
   *  nothing about the data changes with it.
   *
   *  Starts flat on purpose, and **must** start flat rather than reading
   *  storage here: the top bar renders on the server, and a stored value read
   *  at module scope would make the first client render disagree with it.
   *  `HUD` restores the reader's choice in an effect instead.
   */
  dimension: Dimension;
  setDimension: (dimension: Dimension) => void;

  /** The Graph Guide. Shown once unprompted on a first graph, then on
   *  request only — L1/L2/L3 is the least self-explanatory thing here and
   *  the explanation is worth exactly one interruption. */
  guideOpen: boolean;
  setGuide: (open: boolean) => void;

  /** The dive. Going a level deeper *at a place* rather than switching a tab:
   *  the camera holds the district you opened while the new level assembles
   *  around it, so L1 → L2 → L3 reads as travel instead of navigation.
   *
   *  `pendingFocus` is the cluster the next spec should be framed on. The
   *  renderer consumes it once the new level has arrived and clears it. */
  pendingFocus: string | null;
  dive: (nodeId: string) => Promise<void>;
  consumeFocus: () => void;

  /** Search, as navigation.
   *
   *  A hit is a place on the map, so choosing one has to *arrive* — and the
   *  level showing files cannot draw a function, nor L1 a file. Picking a
   *  result therefore moves the camera and, when it has to, the depth: the
   *  shallowest level that can actually draw the thing asked for. Without
   *  this, half of every search silently did nothing.
   */
  goTo: (hit: SearchHit) => Promise<void>;

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

/** Monotonic id of the latest level request; see `setZoom`. */
let zoomRequest = 0;

export const useGraphStore = create<GraphState>((set, get) => ({
  phase: "idle",
  error: null,
  notice: null,
  clearNotice: () => set({ notice: null }),
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
  rippleEndpoints: [],

  analyze: async (source: string) => {
    set({
      phase: "understanding",
      error: null,
      notice: null,
      stages: [],
      stagesShown: 0,
      spec: null,
    });
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
    if (snapshotId === null) return false;
    // Press 3 then 2 quickly and both fetches go out; without this, whichever
    // answered *last* won, so the level on screen could be the one pressed
    // first. Only the most recent request is allowed to land.
    const request = ++zoomRequest;
    let spec: ViewSpec;
    try {
      spec = await fetchViewSpec(snapshotId, zoom);
    } catch (failure) {
      if (request !== zoomRequest) return false; // superseded: say nothing
      // Nothing has changed yet — `zoom` is only committed below, on success —
      // so the level buttons still show where the reader actually is. All that
      // is missing is saying so.
      set({
        notice: `Could not open L${zoom} — ${(failure as Error).message}`,
      });
      return false;
    }
    if (request !== zoomRequest) return false; // a later press has taken over
    set({
      notice: null,
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
    return true;
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
    // Endpoints are a second question about the same change; asking both at
    // once means the wave never arrives at a readout that is still loading.
    let blast: BlastResult;
    let endpoints: QueryResult | null;
    try {
      [blast, endpoints] = await Promise.all([
        fetchBlastRadius(snapshotId, nodeId),
        runQuery(snapshotId, "endpoints", { node_id: nodeId }).catch(() => null),
      ]);
    } catch (failure) {
      set({ notice: `Could not trace the impact — ${(failure as Error).message}` });
      return;
    }
    const maxDistance = Math.max(
      1,
      ...blast.ranked.map((entry) => entry.reasons.distance),
    );
    set({
      notice: null,
      blast,
      rippleFor: nodeId,
      selectedId: nodeId,
      // Starts at 1, not 0. The direct dependents are the answer to
      // "what breaks if I change this" and they should be on screen the
      // instant it is asked; starting at 0 spent the first 320ms showing
      // "0 files" under a question the graph had already answered.
      rippleFront: 1,
      maxRippleDistance: maxDistance,
      rippleEndpoints: (endpoints?.meta.endpoints as EndpointRef[] | undefined) ?? [],
    });
  },

  advanceRipple: () => {
    const { rippleFront, maxRippleDistance } = get();
    if (rippleFront >= maxRippleDistance) return; // wave has reached the edge
    set({ rippleFront: rippleFront + 1 });
  },

  clearRipple: () =>
    set({ blast: null, rippleFor: null, rippleFront: 0, rippleEndpoints: [] }),

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

  guideOpen: false,
  setGuide: (open) => set({ guideOpen: open }),

  dimension: "2d",
  setDimension: (dimension) => {
    set({ dimension });
    try {
      window.localStorage.setItem(DIMENSION_KEY, dimension);
    } catch {
      /* storage disabled — the choice still holds for this session */
    }
  },

  pendingFocus: null,
  consumeFocus: () => set({ pendingFocus: null }),

  dive: async (nodeId: string) => {
    const { zoom, spec } = get();
    if (zoom >= 3) return; // L3 is the floor; there is nothing under a symbol
    const node = spec?.nodes.find((candidate) => candidate.id === nodeId);
    if (!node) return;
    // A district's own key is its cluster; a file's is the district holding
    // it. Either way the next level is framed on the same region of the repo,
    // which is what makes the movement read as going *inward* rather than
    // sideways to another view.
    set({ pendingFocus: node.kind === "cluster" ? node.label : node.cluster });
    // A focus that outlives a failed dive is worse than no focus: the next
    // level change to succeed would silently frame itself on a district the
    // reader asked about minutes ago.
    if (!(await get().setZoom(zoom + 1))) set({ pendingFocus: null });
  },

  goTo: async (hit) => {
    const { spec, zoom } = get();
    // Symbols only exist at L3; files and modules first appear at L2. If the
    // level on screen already draws the hit — or the file holding it, which
    // is how a symbol is represented one level up — stay where the reader is.
    const target = hit.kind === "function" || hit.kind === "class" ? 3 : 2;
    if (!drawable(spec, hit) && zoom !== target) {
      // Selecting into a level that failed to load would light nothing and
      // say nothing. `setZoom` has already explained itself; stop here.
      // `false` also covers being overtaken: if the reader dived or pressed
      // a level key while this was in flight, that later request owns the
      // screen and this landing stands down rather than completing on top.
      if (!(await get().setZoom(target))) return;
    }
    // The graph foregrounds one answer at a time; landing a search on a node
    // an overlay has dimmed is arriving in the dark. And arriving anywhere
    // clears the last thing that did not work.
    set({ paletteOpen: false, overlay: null, overlayError: null, notice: null });
    get().select(hit.id);
  },

  overlay: null,
  overlayError: null,

  runOverlay: async (name, label, params = {}) => {
    const { snapshotId } = get();
    if (snapshotId === null) return;
    set({
      paletteOpen: false,
      overlayError: null,
      // Anything that works clears the last thing that did not: a notice
      // about a failure the reader has already moved past is just noise.
      notice: null,
      selectedId: null,
      blast: null,
      rippleFor: null,
    });
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
      // `overlayError` was set here and rendered by nobody, so a query that
      // failed looked exactly like a query that found nothing.
      const message = (error as Error).message;
      set({ overlayError: message, notice: `Could not run that query — ${message}` });
    }
  },

  clearOverlay: () => set({ overlay: null, overlayError: null }),
}));

/** Can the level currently on screen show this hit at all?
 *
 *  Either as itself, or as the file that contains it — the substitution the
 *  renderer and the ripple both already make for a symbol at L2.
 */
function drawable(spec: ViewSpec | null, hit: SearchHit): boolean {
  if (!spec) return false;
  return spec.nodes.some(
    (node) => node.id === hit.id || (!!hit.path && node.id === `file:${hit.path}`),
  );
}

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
