"use client";

import Graph from "graphology";
import { useEffect, useRef } from "react";
import Sigma from "sigma";
import { useGraphStore } from "@/lib/store";

/** The renderer — and only the renderer (ARCHITECTURE.md §6).
 *
 *  Positions, sizes, colors, and assembly order all arrive precomputed in
 *  the ViewSpec. This component's whole job: put them on a WebGL canvas,
 *  animate the reveal, dim the world in focus mode. It computes nothing.
 */
/** A camera bounding box that frames the bulk of the graph, not its outliers.
 *  Centre on the centroid; size to a high percentile of each axis's spread so
 *  a couple of stray nodes don't shrink everything. Uses the same y-flip the
 *  graph is built with, so the box matches what's on screen. */
function massBBox(nodes: { x: number; y: number }[]): { x: [number, number]; y: [number, number] } {
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
  const half = Math.max(halfW, halfH); // square box → no axis distortion
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

export default function GraphCanvas() {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma | null>(null);

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

  // Build/rebuild the sigma instance when a new spec arrives.
  useEffect(() => {
    if (!containerRef.current || !spec) return;

    const graph = new Graph({ multi: true, type: "directed" });
    for (const node of spec.nodes) {
      graph.addNode(node.id, {
        label: node.label,
        x: node.x,
        y: -node.y, // screen y grows downward; keep the server's geometry
        size: node.size,
        color: node.color,
        assemblyIndex: node.assembly_index,
        kind: node.kind,
        filePath: node.file_path,
        startLine: node.start_line,
      });
    }
    for (const edge of spec.edges) {
      if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
      graph.addEdge(edge.source, edge.target, {
        size: Math.min(3, 0.4 + Math.log1p(edge.weight) * 0.6),
        // Uncertainty rendered, not hidden: guessed edges are fainter.
        color:
          edge.confidence === "resolved"
            ? "rgba(148,163,184,0.35)"
            : edge.confidence === "heuristic"
              ? "rgba(148,163,184,0.18)"
              : "rgba(148,163,184,0.10)",
      });
    }

    const sigma = new Sigma(graph, containerRef.current, {
      renderLabels: true,
      labelColor: { color: "#cbd5e1" },
      labelSize: 11,
      labelRenderedSizeThreshold: 7,
      defaultEdgeType: "line",
      minCameraRatio: 0.05,
      maxCameraRatio: 4,
    });

    sigma.on("clickNode", ({ node }) => {
      const state = useGraphStore.getState();
      if (state.phase === "revealing") {
        state.skipReveal(); // one click and you're exploring — no forced sit-through
        return;
      }
      state.select(state.selectedId === node ? null : node);
    });
    sigma.on("clickStage", () => {
      const state = useGraphStore.getState();
      if (state.phase === "revealing") state.skipReveal();
      else if (state.rippleFor) state.clearRipple();
      else state.select(null);
    });

    sigmaRef.current = sigma;
    // Frame the MASS, not the bounding box. Sigma fits the camera to the full
    // node extent, so a single far-flung file (a lone docs/example script)
    // drags the dense districts off-centre and shrinks them. Instead we hand
    // sigma a custom bbox built from the centroid and a robust radius that
    // ignores outliers — the bulk fills the screen; a stray node just sits
    // near the edge. Honest: no node is moved or hidden, only the camera frames.
    sigma.setCustomBBox(massBBox(spec.nodes));
    sigma.refresh();
    sigma.getCamera().animatedReset({ duration: 0 });
    return () => {
      sigma.kill();
      sigmaRef.current = null;
    };
  }, [spec]);

  // The reveal, the ripple, and focus dimming are all reducers over
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

    const neighbourhood = new Set<string>();
    if (!rippleActive && selectedId && graph.hasNode(selectedId)) {
      neighbourhood.add(selectedId);
      for (const neighbour of graph.neighbors(selectedId)) neighbourhood.add(neighbour);
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

      if (selectedId && !neighbourhood.has(node)) {
        // Focus mode: the rest of the city recedes (EXPERIENCE §signature).
        return { ...data, color: "#1e293b", label: null, zIndex: 0 };
      }
      if (selectedId && node === selectedId) {
        return { ...data, highlighted: true, zIndex: 2 };
      }
      return { ...data, zIndex: 1 };
    });

    sigma.setSetting("edgeReducer", (edge, data) => {
      const [source, target] = graph.extremities(edge);

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
        return { ...data, color: "rgba(251,146,60,0.5)", size: (data.size as number) * 1.4 };
      }

      if (selectedId) {
        if (!neighbourhood.has(source) || !neighbourhood.has(target)) {
          return { ...data, hidden: true };
        }
        return { ...data, color: "rgba(125,211,252,0.6)", size: (data.size as number) * 1.5 };
      }
      return data;
    });

    sigma.refresh();
  }, [phase, revealIndex, selectedId, rippleFor, blast, rippleFront]);

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
        else select(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [skipReveal, select, clearRipple]);

  return <div ref={containerRef} className="graph-canvas" />;
}
