"use client";

import Graph from "graphology";
import { useEffect, useRef, useState } from "react";
import Sigma from "sigma";
import { useGraphStore } from "@/lib/store";

/** The renderer — and only the renderer (ARCHITECTURE.md §6).
 *
 *  Positions, sizes, colors, and assembly order all arrive precomputed in the
 *  ViewSpec. This component's whole job: put them on a WebGL canvas, animate
 *  the transitions, and resolve the relevance hierarchy. It computes nothing.
 *
 *  **One Sigma instance, for the life of the session.** It used to be built
 *  and killed on every spec change, which is what made L1/L2/L3 feel like
 *  three separate pages: with no object surviving the switch there was
 *  nothing to animate, and the canvas visibly tore down and reassembled.
 *  Now the graph is *diffed* — nodes present at both levels keep their
 *  identity and glide to their new position, arrivals fade in, departures
 *  fade out. Same data, same ViewSpec contract; the continuity is what makes
 *  it read as one world seen at three depths.
 */

/** A camera bounding box that frames the bulk of the graph, not its outliers.
 *  Centre on the centroid; size to a high percentile of each axis's spread so
 *  a couple of stray nodes don't shrink everything. Uses the same y-flip the
 *  graph is built with, so the box matches what's on screen. */
function massBBox(
  nodes: { x: number; y: number }[],
  pad = 1,
): { x: [number, number]; y: [number, number] } {
  const xs = nodes.map((n) => n.x);
  const ys = nodes.map((n) => -n.y); // graph stores y flipped; match it
  const cx = xs.reduce((a, b) => a + b, 0) / xs.length;
  const cy = ys.reduce((a, b) => a + b, 0) / ys.length;

  const percentile = (values: number[], centre: number, p: number): number => {
    const spread = values.map((v) => Math.abs(v - centre)).sort((a, b) => a - b);
    const idx = Math.min(spread.length - 1, Math.floor(p * (spread.length - 1)));
    return Math.max(spread[idx], 1);
  };

  // 88th percentile keeps ~1-2 outliers out of the frame; ×1.25 adds margin.
  const halfW = percentile(xs, cx, 0.88) * 1.25;
  const halfH = percentile(ys, cy, 0.88) * 1.25;
  const half = Math.max(halfW, halfH) * pad; // square box → no axis distortion
  return { x: [cx - half, cx + half], y: [cy - half, cy + half] };
}

/** Impact color for the ripple: hot amber at distance 1, fading toward the
 *  wavefront so a dependent three hops away visibly matters less than a direct
 *  one. Fades via alpha over the dark canvas — the falloff IS the severity. */
function rippleColor(distance: number, front: number): string {
  const span = Math.max(1, front);
  const t = Math.min(1, (distance - 1) / span); // 0 nearest, 1 at the wavefront
  const alpha = 1 - 0.7 * t;
  const green = Math.round(160 - 60 * t);
  return `rgba(249, ${green}, 40, ${alpha.toFixed(2)})`;
}

/** How long a level change takes to resolve. Long enough to read as travel,
 *  short enough that nobody waits for it. */
const TRANSITION_MS = 620;

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
const CONFIDENCE_RGB: Record<string, string> = {
  resolved: "148,163,184", // slate — a proven edge needs no colour
  heuristic: "186,168,148", // warm grey — a name match, not a proof
  dynamic_unknown: "205,162,124", // warmer — the target could be any of several
};

const CONFIDENCE_WIDTH: Record<string, number> = {
  resolved: 1,
  heuristic: 0.75,
  dynamic_unknown: 0.55,
};

const edgeColor = (confidence: string, alpha: number): string =>
  `rgba(${CONFIDENCE_RGB[confidence] ?? CONFIDENCE_RGB.resolved},${alpha})`;

const easeInOutCubic = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

interface Placement {
  x: number;
  y: number;
  size: number;
}

export default function GraphCanvas() {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const frameRef = useRef<number | null>(null);
  /// Flips once Sigma exists, so the diff effect below re-runs for a spec
  /// that arrived while the container was still being laid out.
  const [ready, setReady] = useState(false);

  const spec = useGraphStore((s) => s.spec);
  const phase = useGraphStore((s) => s.phase);
  const revealIndex = useGraphStore((s) => s.revealIndex);
  const selectedId = useGraphStore((s) => s.selectedId);
  const select = useGraphStore((s) => s.select);
  const advanceReveal = useGraphStore((s) => s.advanceReveal);
  const skipReveal = useGraphStore((s) => s.skipReveal);
  const blast = useGraphStore((s) => s.blast);
  const rippleFor = useGraphStore((s) => s.rippleFor);
  const rippleFront = useGraphStore((s) => s.rippleFront);
  const advanceRipple = useGraphStore((s) => s.advanceRipple);
  const clearRipple = useGraphStore((s) => s.clearRipple);
  const overlay = useGraphStore((s) => s.overlay);
  const clearOverlay = useGraphStore((s) => s.clearOverlay);

  // ── the instance: created once, never rebuilt ───────────────────────────
  //
  // Creation waits for the container to have real dimensions. Sigma throws
  // "Container has no width" if it does not, and now that the instance is
  // built at mount rather than on first spec, mount can land before the
  // stylesheet that gives `.graph-canvas` its size — the old code got away
  // with it only because it constructed Sigma much later.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let cancelled = false;

    const create = () => {
      if (cancelled || sigmaRef.current) return;
      if (container.offsetWidth === 0 || container.offsetHeight === 0) return;

      const graph = new Graph({ multi: true, type: "directed" });
      const sigma = new Sigma(graph, container, {
        renderLabels: true,
        labelColor: { color: "#cbd5e1" },
        labelSize: 11,
        labelRenderedSizeThreshold: 7,
        defaultEdgeType: "line",
        minCameraRatio: 0.05,
        maxCameraRatio: 4,
      });

      // Handlers read the store through getState(), so they never go stale and
      // never need rebinding — which is what lets the instance outlive every
      // spec, selection and level change.
      sigma.on("clickNode", ({ node }) => {
        const state = useGraphStore.getState();
        if (state.phase === "revealing") {
          state.skipReveal(); // one click and you're exploring — no forced sit-through
          return;
        }
        state.select(state.selectedId === node ? null : node);
      });
      // Double-click dives. Sigma's own double-click zooms the camera, which
      // would fight the level change, so its default is suppressed.
      sigma.on("doubleClickNode", ({ node, event }) => {
        event.preventSigmaDefault();
        const state = useGraphStore.getState();
        if (state.phase === "revealing") return;
        void state.dive(node);
      });
      sigma.on("clickStage", () => {
        const state = useGraphStore.getState();
        if (state.phase === "revealing") state.skipReveal();
        else if (state.rippleFor) state.clearRipple();
        else if (state.overlay) state.clearOverlay();
        else state.select(null);
      });

      sigmaRef.current = sigma;
      setReady(true);
    };

    create();
    const observer = new ResizeObserver(create);
    observer.observe(container);

    return () => {
      cancelled = true;
      observer.disconnect();
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      sigmaRef.current?.kill();
      sigmaRef.current = null;
    };
  }, []);

  // ── the diff: same instance, new level ──────────────────────────────────
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma || !spec) return;
    const graph = sigma.getGraph();

    const incoming = new Map(spec.nodes.map((node) => [node.id, node]));
    const survivors: string[] = [];
    const departing: string[] = [];
    // Collect before mutating: dropping a node inside forEachNode would
    // invalidate the iteration.
    graph.forEachNode((id) => {
      (incoming.has(id) ? survivors : departing).push(id);
    });

    const from = new Map<string, Placement>();
    for (const id of survivors) {
      from.set(id, {
        x: graph.getNodeAttribute(id, "x") as number,
        y: graph.getNodeAttribute(id, "y") as number,
        size: graph.getNodeAttribute(id, "size") as number,
      });
    }

    for (const id of departing) graph.dropNode(id);
    graph.clearEdges();

    const to = new Map<string, Placement>();
    for (const node of spec.nodes) {
      const target: Placement = { x: node.x, y: -node.y, size: node.size };
      to.set(node.id, target);
      const attributes = {
        label: node.label,
        size: node.size,
        color: node.color,
        assemblyIndex: node.assembly_index,
        kind: node.kind,
        filePath: node.file_path,
        startLine: node.start_line,
        cluster: node.cluster,
        explainId: node.explain_id,
      };
      if (graph.hasNode(node.id)) {
        // A survivor keeps the position it is currently drawn at; the tween
        // below carries it to the new one.
        const start = from.get(node.id) ?? target;
        graph.mergeNodeAttributes(node.id, { ...attributes, x: start.x, y: start.y, size: start.size });
      } else {
        // An arrival starts small at its destination and grows in, so a new
        // level assembles rather than pops.
        graph.addNode(node.id, { ...attributes, x: target.x, y: target.y, size: 0.1 });
      }
    }

    for (const edge of spec.edges) {
      if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
      graph.addEdge(edge.source, edge.target, {
        baseSize: Math.min(3, 0.4 + Math.log1p(edge.weight) * 0.6),
        confidence: edge.confidence,
        kind: edge.kind,
      });
    }

    sigma.setCustomBBox(massBBox(spec.nodes));

    // Tween survivors to their new places. The first spec of a session has no
    // survivors, so this is a no-op there and the reveal animation owns the
    // entrance.
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    const started = performance.now();
    const step = () => {
      const t = Math.min(1, (performance.now() - started) / TRANSITION_MS);
      const eased = easeInOutCubic(t);
      graph.forEachNode((id) => {
        const start = from.get(id);
        const end = to.get(id);
        if (!end) return;
        const origin = start ?? end;
        graph.setNodeAttribute(id, "x", origin.x + (end.x - origin.x) * eased);
        graph.setNodeAttribute(id, "y", origin.y + (end.y - origin.y) * eased);
        graph.setNodeAttribute(
          id,
          "size",
          (start?.size ?? 0.1) + (end.size - (start?.size ?? 0.1)) * eased,
        );
      });
      if (t < 1) frameRef.current = requestAnimationFrame(step);
      else frameRef.current = null;
    };
    frameRef.current = requestAnimationFrame(step);

    // Where the camera lands after a level change. A plain reset would snap
    // back to the whole repository every time, which is what made L1/L2/L3
    // feel like three separate views: you dive into `src` and arrive looking
    // at everything. If a dive named a region, frame that instead — the
    // district you opened stays under the camera while its contents assemble
    // around it.
    const focus = useGraphStore.getState().pendingFocus;
    const framed = focus
      ? spec.nodes.filter((node) => node.cluster === focus || node.label === focus)
      : [];
    if (framed.length > 0) {
      // Padded, because arriving with the district edge-to-edge reads as
      // being dumped somewhere rather than as having gone *into* it. At 2x
      // the region fills the middle of the screen and its surroundings stay
      // visible at the margins, which is what makes the move feel like
      // travel with a destination.
      sigma.setCustomBBox(massBBox(framed, 2));
      useGraphStore.getState().consumeFocus();
    }
    // Refresh BEFORE moving the camera. Camera coordinates are relative to the
    // current bounding box, so animating first interprets `{0.5, 0.5}` against
    // the *previous* level's extent and lands nowhere near the new nodes — a
    // level switch rendered a blank canvas until this ordering was fixed.
    sigma.refresh();
    // `animatedReset`, never a hand-written {0.5, 0.5, ratio 1}. Those look
    // like the home position and are not: the camera's default is derived
    // from the current bounding box, so writing the coordinates by hand
    // framed the *previous* level's extent and left L1 rendering a blank
    // canvas while L2 looked fine. Sigma knows where home is; ask it.
    sigma.getCamera().animatedReset({
      duration: survivors.length > 0 ? TRANSITION_MS : 0,
    });
  }, [spec, ready]);

  // ── the camera follows the selection ────────────────────────────────────
  //
  // Clicking should feel like *entering* a part of the codebase, not like
  // ticking a checkbox on a circle. The camera moves to the node and closes
  // in; releasing the selection pulls back out to the whole level.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma || phase !== "exploring") return;
    const camera = sigma.getCamera();

    // Deliberately no auto-pull-back on deselect. Two things argued for it:
    // yanking the camera home every time a selection is released is jarring
    // when the reader is mid-exploration, and every attempt to script the
    // "home" position fought the custom bounding box a level change had just
    // installed — L1 rendered blank while L2 looked fine. Releasing a
    // selection now leaves the view exactly where it is; the level buttons
    // and a dive are what move the camera.
    if (!selectedId || !sigma.getGraph().hasNode(selectedId)) return;
    const position = sigma.getNodeDisplayData(selectedId);
    if (!position) return;
    camera.animate(
      // Never zoom *out* to reach something: if the reader has already pushed
      // in closer than this, honour that and only re-centre.
      { x: position.x, y: position.y, ratio: Math.min(camera.ratio, 0.55) },
      { duration: 420 },
    );
  }, [selectedId, phase]);

  // The reveal, the ripple, and the relevance hierarchy are all reducers over
  // precomputed state — the renderer decides nothing, it only choreographs.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    const graph = sigma.getGraph();

    // Ripple: distance-per-node from the real blast-radius result, kept only
    // for nodes that exist at this zoom level (the wave lights what's on screen).
    const distanceOf = new Map<string, number>();
    if (rippleFor && blast) {
      for (const entry of blast.ranked) {
        if (graph.hasNode(entry.node_id)) {
          distanceOf.set(entry.node_id, entry.reasons.distance);
        }
      }
    }
    const rippleActive = rippleFor !== null && graph.hasNode(rippleFor);

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
    if (!rippleActive && selectedId && graph.hasNode(selectedId)) {
      for (const neighbour of graph.neighbors(selectedId)) direct.add(neighbour);
      for (const neighbour of direct) {
        for (const outer of graph.neighbors(neighbour)) {
          if (outer !== selectedId && !direct.has(outer)) second.add(outer);
        }
      }
    }

    sigma.setSetting("nodeReducer", (node, data) => {
      const assemblyIndex = data.assemblyIndex as number;
      if (phase === "revealing" && assemblyIndex > revealIndex) {
        return { ...data, hidden: true };
      }

      if (rippleActive) {
        if (node === rippleFor) {
          // The source of the change: the eye of the storm.
          return { ...data, color: "#f8fafc", size: (data.size as number) * 1.6, zIndex: 3 };
        }
        const distance = distanceOf.get(node);
        if (distance === undefined) {
          return { ...data, color: "#0f172a", label: null, zIndex: 0 }; // untouched
        }
        if (distance > rippleFront) {
          return { ...data, color: "#1e293b", label: null, zIndex: 1 }; // wave not here yet
        }
        // Reached: hot near the source, fading with distance (real severity).
        return {
          ...data,
          color: rippleColor(distance, rippleFront),
          size: (data.size as number) * (distance === 1 ? 1.4 : 1.1),
          zIndex: distance === 1 ? 2 : 1,
        };
      }

      if (selectedId) {
        // 100 / 60 / 20 / 5. On a dense graph a slightly dimmer neighbour is
        // invisible, so the falloff has to be steep enough to read instantly.
        // Emphasis is added, then capped. A flat multiplier cannot serve both
        // levels: ×2.2 is the minimum that reads on an L2 file node of 4px,
        // and turns an L1 district of 40px into a disc that swallows the
        // screen. Growing by a bounded amount lifts the small case and leaves
        // the large one recognisable — selection is carried by colour and
        // label anyway, with size only reinforcing it.
        const base = data.size as number;
        if (node === selectedId) {
          return {
            ...data,
            color: "#ffffff",
            size: base + Math.min(base * 1.2, 9),
            highlighted: true,
            forceLabel: true,
            zIndex: 4,
          };
        }
        if (direct.has(node)) {
          return {
            ...data,
            size: base + Math.min(base * 0.5, 4),
            forceLabel: true,
            zIndex: 3,
          };
        }
        if (second.has(node)) {
          return { ...data, color: "#334155", label: null, zIndex: 2 };
        }
        return { ...data, color: "#0d1524", label: null, zIndex: 0 };
      }
      return { ...data, zIndex: 1 };
    });

    sigma.setSetting("edgeReducer", (edge, data) => {
      const [source, target] = graph.extremities(edge);
      const confidence = data.confidence as string;
      // Width is confidence's second channel; relevance never touches it, so
      // a faint distant edge and a faint guess stay distinguishable.
      const baseSize =
        (data.baseSize as number) * (CONFIDENCE_WIDTH[confidence] ?? 1);

      if (phase === "revealing") {
        const sourceIn =
          (graph.getNodeAttribute(source, "assemblyIndex") as number) <= revealIndex;
        const targetIn =
          (graph.getNodeAttribute(target, "assemblyIndex") as number) <= revealIndex;
        if (!sourceIn || !targetIn) return { ...data, hidden: true };
      }

      if (rippleActive) {
        const sourceReached =
          source === rippleFor || (distanceOf.get(source) ?? Infinity) <= rippleFront;
        const targetReached =
          target === rippleFor || (distanceOf.get(target) ?? Infinity) <= rippleFront;
        if (!sourceReached || !targetReached) return { ...data, hidden: true };
        // The wave owns the colour here — impact is the message, not provenance.
        return { ...data, size: baseSize * 1.4, color: "rgba(251,146,60,0.5)" };
      }

      if (overlayActive) {
        // Only the wiring between answered nodes — for `cycles` this is what
        // turns a set of files into a visible loop.
        if (!answered.has(source) || !answered.has(target)) {
          return { ...data, hidden: true };
        }
        return { ...data, color: "rgba(125,211,252,0.9)", size: baseSize * 2.2 };
      }

      if (selectedId) {
        const touchesSelection = source === selectedId || target === selectedId;
        const withinDirect =
          (direct.has(source) || source === selectedId) &&
          (direct.has(target) || target === selectedId);
        const touchesSecond = second.has(source) || second.has(target);

        // 100 / 60 / 20 / 5 — the hierarchy, in alpha.
        if (touchesSelection) {
          return { ...data, color: edgeColor(confidence, 1), size: baseSize * 2.4 };
        }
        if (withinDirect) {
          return { ...data, color: edgeColor(confidence, 0.6), size: baseSize * 1.2 };
        }
        if (touchesSecond) {
          return { ...data, color: edgeColor(confidence, 0.2), size: baseSize };
        }
        return { ...data, color: edgeColor(confidence, 0.05), size: baseSize };
      }

      // Resting state: quiet, so that selecting anything is a visible event.
      return { ...data, color: edgeColor(confidence, 0.22), size: baseSize };
    });

    sigma.refresh();
  }, [phase, revealIndex, selectedId, rippleFor, blast, rippleFront, spec, overlay]);

  // Drive the ripple clock: the wave expands one distance ring at a time.
  useEffect(() => {
    if (!rippleFor) return;
    const timer = window.setInterval(advanceRipple, 320);
    return () => window.clearInterval(timer);
  }, [rippleFor, advanceRipple]);

  // Drive the reveal clock.
  useEffect(() => {
    if (phase !== "revealing") return;
    const timer = window.setInterval(advanceReveal, 90);
    return () => window.clearInterval(timer);
  }, [phase, advanceReveal]);

  // Escape hatch on keyboard too.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        const state = useGraphStore.getState();
        if (state.phase === "revealing") skipReveal();
        else if (state.rippleFor) clearRipple();
        else if (state.overlay) clearOverlay();
        else select(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [skipReveal, select, clearRipple, clearOverlay]);

  return <div ref={containerRef} className="graph-canvas" />;
}
