"use client";

import Graph from "graphology";
import { useEffect, useRef, useState } from "react";
import Sigma from "sigma";
import {
  ARROW_HEADINGS,
  TRANSITION_MS,
  anchorFor,
  buildFrame,
  easeInOutCubic,
  neighbourToward,
} from "@/lib/choreography";
import { useChoreographyClocks } from "@/lib/clocks";
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
 *
 *  **What to draw is no longer decided here.** The reveal, the ripple, the
 *  overlay and the relevance hierarchy moved to `lib/choreography.ts` when a
 *  second renderer appeared, because they were never Sigma-specific: they are
 *  pure functions from the store's state to a display record. What is left in
 *  this file is Sigma — the instance, the diff, the camera, the events — and
 *  two reducers that hand a node its own attributes and pass the answer
 *  through.
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

interface Placement {
  x: number;
  y: number;
  size: number;
}

export default function GraphCanvas() {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);
  const frameRef = useRef<number | null>(null);
  //: When the level-change tween finishes moving nodes. Anything that reads a
  //: node's *drawn* position has to wait for it: during the tween a node is
  //: still sitting at the previous level's coordinates.
  const settledAtRef = useRef(0);
  //: The node the current ripple has already been framed on, so the camera
  //: moves once per wave rather than once per expanding ring.
  const framedRippleRef = useRef<string | null>(null);
  /// Flips once Sigma exists, so the diff effect below re-runs for a spec
  /// that arrived while the container was still being laid out.
  const [ready, setReady] = useState(false);

  useChoreographyClocks();

  const spec = useGraphStore((s) => s.spec);
  const phase = useGraphStore((s) => s.phase);
  const revealIndex = useGraphStore((s) => s.revealIndex);
  const selectedId = useGraphStore((s) => s.selectedId);
  // Only for `anchorFor`: the owning file of a selection this level cannot
  // draw. It arrives a moment after the selection does, which is why every
  // effect that anchors also depends on it.
  const selectedPath = useGraphStore((s) => s.explanation?.meta.identity.file_path);
  const select = useGraphStore((s) => s.select);
  const skipReveal = useGraphStore((s) => s.skipReveal);
  const blast = useGraphStore((s) => s.blast);
  const rippleFor = useGraphStore((s) => s.rippleFor);
  const rippleFront = useGraphStore((s) => s.rippleFront);
  const clearRipple = useGraphStore((s) => s.clearRipple);
  const setZoom = useGraphStore((s) => s.setZoom);
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
    settledAtRef.current = started + TRANSITION_MS;
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
    // Never zero: sigma divides the elapsed time by the duration, so a
    // duration of 0 makes the first frame's `t` NaN and writes NaN into the
    // camera's x, y and ratio — after which every later camera move
    // interpolates from NaN and the canvas renders nothing. It cost a blank
    // screen on exactly the transitions with no shared nodes: L1 → L3, which
    // is what a search for a symbol from the architecture level does.
    sigma.getCamera().animatedReset({
      duration: survivors.length > 0 ? TRANSITION_MS : 1,
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
    const anchor = anchorFor(sigma.getGraph(), selectedId, selectedPath);
    if (!anchor) return;

    // Search can select at the same moment the level changes — "take me to
    // this function" from L1 arrives at L3 with the node still mid-tween,
    // holding the position it had at the *previous* level. Flying there put
    // the camera in empty space and the graph off screen. Wait for the nodes
    // to stop moving; then the coordinate is the one being drawn.
    const move = () => {
      const position = sigma.getNodeDisplayData(anchor);
      if (!position) return;
      camera.animate(
        // Never zoom *out* to reach something: if the reader has already
        // pushed in closer than this, honour that and only re-centre. A
        // non-finite ratio would poison this and every camera move after it,
        // so it is treated as "no preference to honour".
        {
          x: position.x,
          y: position.y,
          ratio: Number.isFinite(camera.ratio) ? Math.min(camera.ratio, 0.55) : 0.55,
        },
        { duration: 420 },
      );
    };
    const wait = settledAtRef.current - performance.now();
    if (wait <= 0) {
      move();
      return;
    }
    const timer = window.setTimeout(move, wait);
    return () => window.clearTimeout(timer);
  }, [selectedId, selectedPath, phase]);

  // The reveal, the ripple, and the relevance hierarchy are all reducers over
  // precomputed state — the renderer decides nothing, it only choreographs.
  // The deciding moved to `lib/choreography.ts`; what is left here is handing
  // Sigma the answers.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    const graph = sigma.getGraph();

    const frame = buildFrame(graph, {
      phase,
      revealIndex,
      selectedId,
      selectedPath,
      blast,
      rippleFor,
      rippleFront,
      overlay,
    });

    // Frame the wave's origin — but only when nothing else has.
    //
    // `anchorFor` usually gets there first, but it waits on the explanation
    // fetch, and the ripple knows its own origin immediately — `blast.meta`
    // carries the focus file. Without this the first second of an impact on a
    // function was the failure the whole feature exists to avoid: the canvas
    // dimmed, the counters counted, and the file actually lighting up could be
    // anywhere off screen. Framing the source is what makes the dimming read
    // as "look here" rather than "something happened".
    //
    // Once per ripple, not once per ring: this effect re-runs on every tick of
    // the wave, and re-animating the camera under an expanding ripple is
    // motion sickness. `null` when the ripple ends, so the next one frames.
    if (!frame.rippleActive || frame.rippleSource === null) {
      framedRippleRef.current = null;
    } else if (
      framedRippleRef.current !== frame.rippleSource &&
      frame.rippleSource !== frame.anchor
    ) {
      framedRippleRef.current = frame.rippleSource;
      const position = sigma.getNodeDisplayData(frame.rippleSource);
      const camera = sigma.getCamera();
      if (position) {
        camera.animate(
          // Same rule as the selection camera: close in, never pull out.
          { x: position.x, y: position.y, ratio: Math.min(camera.ratio, 0.55) },
          { duration: 420 },
        );
      }
    }

    // Sigma's reducers, in full. A display record is a set of overrides on the
    // node's own attributes, which is exactly what a spread does.
    sigma.setSetting("nodeReducer", (node, data) => ({
      ...data,
      ...frame.node(node, data),
    }));
    sigma.setSetting("edgeReducer", (edge, data) => {
      const [source, target] = graph.extremities(edge);
      return { ...data, ...frame.edge(source, target, data) };
    });

    sigma.refresh();
  }, [phase, revealIndex, selectedId, selectedPath, rippleFor, blast, rippleFront, spec, overlay]);

  // ── the keyboard: the graph without a mouse ─────────────────────────────
  //
  // Escape unwinds one layer at a time, 1/2/3 change depth, and the arrows
  // walk the graph by following edges. Traversal is *spatial* rather than
  // list order — pressing → goes to the neighbour that is actually to the
  // right — because the reader is looking at a map, and a key that jumps to
  // whichever neighbour happens to be first in an adjacency list teaches
  // nothing about the shape of what they are looking at.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const state = useGraphStore.getState();
      if (event.key === "Escape") {
        if (state.phase === "revealing") skipReveal();
        else if (state.rippleFor) clearRipple();
        else if (state.overlay) clearOverlay();
        else select(null);
        return;
      }

      // Anything with its own text cursor owns the keystroke. Without this,
      // typing "3" into ⌘K would silently change the zoom behind the palette.
      const target = event.target as HTMLElement | null;
      if (
        state.paletteOpen ||
        state.guideOpen ||
        target?.isContentEditable ||
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement
      ) {
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      if (event.key === "1" || event.key === "2" || event.key === "3") {
        const level = Number(event.key);
        if (level !== state.zoom) void setZoom(level);
        return;
      }

      const heading = ARROW_HEADINGS[event.key];
      if (!heading) return;
      const sigma = sigmaRef.current;
      if (!sigma) return;
      const graph = sigma.getGraph();
      const from = anchorFor(graph, state.selectedId, selectedPath);
      if (!from) return;
      const next = neighbourToward(
        graph,
        (id) => sigma.getNodeDisplayData(id) ?? null,
        from,
        heading,
      );
      if (next) {
        event.preventDefault(); // arrows would otherwise scroll the page
        select(next);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [skipReveal, select, clearRipple, clearOverlay, setZoom, selectedPath]);

  return <div ref={containerRef} className="graph-canvas" />;
}
