"use client";

import type Graph from "graphology";
import type { Attributes } from "graphology-types";
import type { Overlay, Phase } from "./store";
import type { BlastResult } from "./types";

/** The visual language, lifted out of the renderer that used to hold it.
 *
 *  The reveal, the ripple, the overlay and the relevance hierarchy were four
 *  reducers inside `GraphCanvas`, and they were never Sigma-specific: each is
 *  a pure function from "what is true right now" to "how this node or edge
 *  should be drawn". Only the *painting* was Sigma's.
 *
 *  They live here because there are now two renderers. A second copy of this
 *  logic would agree with the first exactly until the first time someone
 *  tuned a threshold, and from then on the 2D and 3D views would be telling
 *  the reader slightly different things about the same repository — which is
 *  a worse failure than having no 3D view at all.
 *
 *  Nothing in this file knows what a canvas is. It reads the graph and the
 *  store's state and returns display records; where a camera goes and how a
 *  circle is drawn stays with whoever is drawing it.
 */

/** How long a level change takes to resolve. Long enough to read as travel,
 *  short enough that nobody waits for it. */
export const TRANSITION_MS = 620;

export const easeInOutCubic = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

/** Two dimensions, two channels — **alpha carries relevance, hue carries
 *  confidence.**
 *
 *  The intent was dashed / dotted lines for the confidence ladder, which is
 *  the clearer encoding. Sigma 3.0.3 ships no dash support and no built-in
 *  edge program that accepts one, so a `dashed` attribute would have been
 *  silently dropped: code that looks like it renders the ladder while
 *  rendering nothing. Writing a custom WebGL edge program is the real fix and
 *  is worth doing later.
 *
 *  Until then the ladder rides a single hue ramp — neutral for proven,
 *  increasingly amber for unproven — so "amber-ness is doubt". It reuses the
 *  palette's existing warning colour rather than inventing a meaning, stays
 *  legible at 5% alpha, and leaves alpha entirely to the hierarchy. Width
 *  reinforces it: a guess is drawn thinner than a proof. */
/// The tints are perceptually matched to the slate, not to the palette's
/// warning amber. First attempt used `#fbbf24` directly and the canvas turned
/// gold: only 38% of Flask's edges are uncertain, but saturated amber at the
/// same alpha reads several times louder than low-chroma slate, so a minority
/// looked like an emergency. Same lightness, hue shifted — the uncertainty is
/// legible without shouting.
export const CONFIDENCE_RGB: Record<string, string> = {
  resolved: "148,163,184", // slate — a proven edge needs no colour
  heuristic: "186,168,148", // warm grey — a name match, not a proof
  dynamic_unknown: "205,162,124", // warmer — the target could be any of several
};

export const CONFIDENCE_WIDTH: Record<string, number> = {
  resolved: 1,
  heuristic: 0.75,
  dynamic_unknown: 0.55,
};

export const edgeColor = (confidence: string, alpha: number): string =>
  `rgba(${CONFIDENCE_RGB[confidence] ?? CONFIDENCE_RGB.resolved},${alpha})`;

/** Impact color for the ripple: hot amber at distance 1, fading toward the
 *  wavefront so a dependent three hops away visibly matters less than a direct
 *  one. Fades via alpha over the dark canvas — the falloff IS the severity. */
export function rippleColor(distance: number, front: number): string {
  const span = Math.max(1, front);
  const t = Math.min(1, (distance - 1) / span); // 0 nearest, 1 at the wavefront
  const alpha = 1 - 0.7 * t;
  const green = Math.round(160 - 60 * t);
  return `rgba(249, ${green}, 40, ${alpha.toFixed(2)})`;
}

/** The node that stands for the selection at *this* zoom level.
 *
 *  A selection does not have to be drawable. ⌘K searches symbols, and picking
 *  a function while the canvas is showing files selects a node the graph has
 *  never heard of. Every consumer then took its "nothing is selected here"
 *  branch: the hierarchy dimmed all 37 files to near-black, the camera refused
 *  to move, and the inspector described a node that was nowhere on screen.
 *  A blank canvas is the worst possible answer to "show me this function".
 *
 *  So the selection falls back to the file that contains it — the same
 *  substitution the ripple already makes for the same reason. Choosing a
 *  file's neighbourhood over an empty screen is not a fudge: at L2 the file
 *  *is* how that function is represented, and lighting it is the honest
 *  drawing of "this is where the thing you asked about lives".
 *
 *  `null` only when even the owning file is absent — at L1 there are no files
 *  at all — and callers treat that as no selection rather than dimming a
 *  canvas nothing will ever light back up.
 */
export function anchorFor(
  graph: Graph,
  selectedId: string | null,
  filePath: string | null | undefined,
): string | null {
  if (!selectedId) return null;
  if (graph.hasNode(selectedId)) return selectedId;
  if (filePath && graph.hasNode(`file:${filePath}`)) return `file:${filePath}`;
  return null;
}

/** Everything the choreography needs to know, and nothing about drawing. */
export interface ChoreographyState {
  phase: Phase;
  revealIndex: number;
  selectedId: string | null;
  /** The owning file of a selection this level cannot draw. It arrives a
   *  moment after the selection does, which is why every caller re-runs on
   *  both. */
  selectedPath: string | null | undefined;
  blast: BlastResult | null;
  rippleFor: string | null;
  rippleFront: number;
  overlay: Overlay | null;
}

/** Overrides to apply on top of a node's own attributes. A field left out is
 *  a field the choreography has no opinion about — the node keeps what the
 *  ViewSpec gave it. */
export interface NodeDisplay {
  hidden?: boolean;
  color?: string;
  size?: number;
  label?: string | null;
  forceLabel?: boolean;
  highlighted?: boolean;
  zIndex?: number;
}

export interface EdgeDisplay {
  hidden?: boolean;
  color?: string;
  size?: number;
}

export interface Frame {
  /** How to draw one node, given its own attributes. */
  node: (id: string, data: Attributes) => NodeDisplay;
  /** How to draw one edge, given its attributes and its two endpoints. */
  edge: (source: string, target: string, data: Attributes) => EdgeDisplay;
  /** The selection as this level can draw it — what a camera should follow. */
  anchor: string | null;
  /** Where the wave radiates from, as drawn at this level. */
  rippleSource: string | null;
  rippleActive: boolean;
}

/** Resolve the whole visual state once, then answer per node and per edge.
 *
 *  The expensive parts — the distance map, the answered set, the two rings of
 *  the hierarchy — are computed here, once per frame, exactly as they were
 *  when this lived inside a `useEffect`. The two returned closures are then
 *  pure lookups, which is what lets Sigma call them as reducers and lets a
 *  three.js renderer call them in a loop over an instance buffer.
 */
export function buildFrame(graph: Graph, state: ChoreographyState): Frame {
  const { phase, revealIndex, selectedId, selectedPath, blast, rippleFor, rippleFront, overlay } =
    state;

  const anchor = anchorFor(graph, selectedId, selectedPath);

  // Ripple: distance-per-node from the real blast-radius result, kept only
  // for nodes that exist at this zoom level (the wave lights what's on screen).
  const distanceOf = new Map<string, number>();
  if (rippleFor && blast) {
    for (const entry of blast.ranked) {
      if (graph.hasNode(entry.node_id)) {
        distanceOf.set(entry.node_id, entry.reasons.distance);
        continue;
      }
      // The affected node is not drawn at this level — asking about a
      // function while looking at files is the common case, and it used to
      // light nothing at all: real counters over a dead canvas, which is
      // exactly the disagreement between caption and picture this feature
      // exists to avoid. Fall back to the file that contains it, keeping
      // the nearest distance when several of its symbols are hit.
      const path = entry.reasons.file_path;
      if (!path) continue;
      const owner = `file:${path}`;
      if (!graph.hasNode(owner)) continue;
      const existing = distanceOf.get(owner);
      if (existing === undefined || entry.reasons.distance < existing) {
        distanceOf.set(owner, entry.reasons.distance);
      }
    }
  }

  // The node the wave radiates from, as drawn at *this* level. Asking about
  // a function while looking at files is the common case, so the source
  // falls back to the file that holds it — otherwise the whole ripple was
  // skipped and the readout described a wave nobody could see.
  let rippleSource: string | null = null;
  if (rippleFor && graph.hasNode(rippleFor)) {
    rippleSource = rippleFor;
  } else if (rippleFor && blast) {
    const focusPath = blast.meta.focus?.file_path;
    if (focusPath && graph.hasNode(`file:${focusPath}`)) {
      rippleSource = `file:${focusPath}`;
    }
  }
  const rippleActive = rippleFor !== null && (rippleSource !== null || distanceOf.size > 0);

  // A query's answer, drawn on the graph. Node ids come from the graph, but
  // a cluster at L1 is a view-layer invention, so `explainId` is checked too
  // — otherwise an answer about files would light nothing at district level.
  const answered = new Set<string>();
  if (overlay && !rippleActive) {
    const wanted = new Set(overlay.nodeIds);
    graph.forEachNode((id, data) => {
      if (wanted.has(id) || wanted.has(data.explainId as string)) answered.add(id);
    });
  }
  const overlayActive = answered.size > 0;

  // The relevance hierarchy: direct neighbours, then everything one hop
  // further out. Second degree is what turns a selection from a star into a
  // readable neighbourhood — it shows the shape of what the change touches.
  const direct = new Set<string>();
  const second = new Set<string>();
  if (!rippleActive && anchor) {
    for (const neighbour of graph.neighbors(anchor)) direct.add(neighbour);
    for (const neighbour of direct) {
      for (const outer of graph.neighbors(neighbour)) {
        if (outer !== anchor && !direct.has(outer)) second.add(outer);
      }
    }
  }

  const node = (id: string, data: Attributes): NodeDisplay => {
    const assemblyIndex = data.assemblyIndex as number;
    if (phase === "revealing" && assemblyIndex > revealIndex) {
      return { hidden: true };
    }

    if (rippleActive) {
      if (id === rippleSource) {
        // The source of the change: the eye of the storm.
        return { color: "#f8fafc", size: (data.size as number) * 1.6, zIndex: 3 };
      }
      const distance = distanceOf.get(id);
      if (distance === undefined) {
        return { color: "#0f172a", label: null, zIndex: 0 }; // untouched
      }
      if (distance > rippleFront) {
        return { color: "#1e293b", label: null, zIndex: 1 }; // wave not here yet
      }
      // Reached: hot near the source, fading with distance (real severity).
      return {
        color: rippleColor(distance, rippleFront),
        size: (data.size as number) * (distance === 1 ? 1.4 : 1.1),
        zIndex: distance === 1 ? 2 : 1,
      };
    }

    if (anchor) {
      // 100 / 60 / 20 / 5. On a dense graph a slightly dimmer neighbour is
      // invisible, so the falloff has to be steep enough to read instantly.
      // Emphasis is added, then capped. A flat multiplier cannot serve both
      // levels: ×2.2 is the minimum that reads on an L2 file node of 4px,
      // and turns an L1 district of 40px into a disc that swallows the
      // screen. Growing by a bounded amount lifts the small case and leaves
      // the large one recognisable — selection is carried by colour and
      // label anyway, with size only reinforcing it.
      const base = data.size as number;
      if (id === anchor) {
        return {
          color: "#ffffff",
          size: base + Math.min(base * 1.2, 9),
          highlighted: true,
          forceLabel: true,
          zIndex: 4,
        };
      }
      if (direct.has(id)) {
        return { size: base + Math.min(base * 0.5, 4), forceLabel: true, zIndex: 3 };
      }
      if (second.has(id)) {
        return { color: "#334155", label: null, zIndex: 2 };
      }
      return { color: "#0d1524", label: null, zIndex: 0 };
    }
    return { zIndex: 1 };
  };

  const edge = (source: string, target: string, data: Attributes): EdgeDisplay => {
    const confidence = data.confidence as string;
    // Width is confidence's second channel; relevance never touches it, so
    // a faint distant edge and a faint guess stay distinguishable.
    const baseSize = (data.baseSize as number) * (CONFIDENCE_WIDTH[confidence] ?? 1);

    if (phase === "revealing") {
      const sourceIn = (graph.getNodeAttribute(source, "assemblyIndex") as number) <= revealIndex;
      const targetIn = (graph.getNodeAttribute(target, "assemblyIndex") as number) <= revealIndex;
      if (!sourceIn || !targetIn) return { hidden: true };
    }

    if (rippleActive) {
      const sourceReached =
        source === rippleSource || (distanceOf.get(source) ?? Infinity) <= rippleFront;
      const targetReached =
        target === rippleSource || (distanceOf.get(target) ?? Infinity) <= rippleFront;
      if (!sourceReached || !targetReached) return { hidden: true };
      // The wave owns the colour here — impact is the message, not provenance.
      return { size: baseSize * 1.4, color: "rgba(251,146,60,0.5)" };
    }

    if (overlayActive) {
      // Only the wiring between answered nodes — for `cycles` this is what
      // turns a set of files into a visible loop.
      if (!answered.has(source) || !answered.has(target)) {
        return { hidden: true };
      }
      return { color: "rgba(125,211,252,0.9)", size: baseSize * 2.2 };
    }

    if (anchor) {
      const touchesSelection = source === anchor || target === anchor;
      const withinDirect =
        (direct.has(source) || source === anchor) && (direct.has(target) || target === anchor);
      const touchesSecond = second.has(source) || second.has(target);

      // 100 / 60 / 20 / 5 — the hierarchy, in alpha.
      if (touchesSelection) {
        return { color: edgeColor(confidence, 1), size: baseSize * 2.4 };
      }
      if (withinDirect) {
        return { color: edgeColor(confidence, 0.6), size: baseSize * 1.2 };
      }
      if (touchesSecond) {
        return { color: edgeColor(confidence, 0.2), size: baseSize };
      }
      return { color: edgeColor(confidence, 0.05), size: baseSize };
    }

    // Resting state: quiet, so that selecting anything is a visible event.
    return { color: edgeColor(confidence, 0.22), size: baseSize };
  };

  return { node, edge, anchor, rippleSource, rippleActive };
}

/** Which way each arrow points, in screen coordinates (y grows downward). */
export const ARROW_HEADINGS: Record<string, [number, number]> = {
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
};

/** Where a node is drawn, in whatever units the renderer measures the screen
 *  in. Both renderers can answer this; only they know how. */
export type Project = (id: string) => { x: number; y: number } | null;

/** The neighbour of `from` that lies most nearly in `heading`.
 *
 *  Only actual neighbours are candidates: the arrows follow relationships, so
 *  every press is a step along an edge and the reader ends up tracing real
 *  structure rather than sweeping a region. Among those, direction decides —
 *  scored by the cosine of the angle to the heading, with ties broken by
 *  distance so the nearest of two equally-rightward neighbours wins.
 *
 *  The 45° cone (`cos > 0.5`) is what makes it feel like a direction rather
 *  than a shuffle. A neighbour that is mostly upward should not answer →,
 *  even when it is the only candidate; refusing to move is honest, and the
 *  reader immediately tries another key.
 *
 *  "To the right" is a question about the picture, not about the data, which
 *  is why the projection is a parameter. In the deep view it depends on where
 *  the camera is standing — orbit, and → answers differently. That is the
 *  same rule, honestly applied to a scene you can walk around.
 */
export function neighbourToward(
  graph: Graph,
  project: Project,
  from: string,
  [hx, hy]: [number, number],
): string | null {
  const origin = project(from);
  if (!origin) return null;

  let best: string | null = null;
  let bestScore = -Infinity;
  for (const candidate of graph.neighbors(from)) {
    const position = project(candidate);
    if (!position) continue;
    const dx = position.x - origin.x;
    const dy = position.y - origin.y;
    const distance = Math.hypot(dx, dy);
    if (distance === 0) continue;
    const cosine = (dx * hx + dy * hy) / distance;
    if (cosine <= 0.5) continue; // outside the 45° cone
    const score = cosine - distance / 100_000; // direction first, then nearness
    if (score > bestScore) {
      bestScore = score;
      best = candidate;
    }
  }
  return best;
}
