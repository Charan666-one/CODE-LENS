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
      else state.select(null);
    });

    sigmaRef.current = sigma;
    // Frame the whole city on load. Sigma's default camera doesn't fit custom
    // coordinates to the viewport on its own, which left the graph small and
    // low; refresh + reset centers the bounding box and fills the screen.
    sigma.refresh();
    sigma.getCamera().animatedReset({ duration: 0 });
    return () => {
      sigma.kill();
      sigmaRef.current = null;
    };
  }, [spec]);

  // The assembly reveal + focus dimming are reducers over precomputed state.
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    const graph = sigma.getGraph();

    const neighbourhood = new Set<string>();
    if (selectedId && graph.hasNode(selectedId)) {
      neighbourhood.add(selectedId);
      for (const neighbour of graph.neighbors(selectedId)) neighbourhood.add(neighbour);
    }

    sigma.setSetting("nodeReducer", (node, data) => {
      const assemblyIndex = data.assemblyIndex as number;
      if (phase === "revealing" && assemblyIndex > revealIndex) {
        return { ...data, hidden: true };
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
      if (phase === "revealing") {
        const [source, target] = graph.extremities(edge);
        const sourceIn =
          (graph.getNodeAttribute(source, "assemblyIndex") as number) <= revealIndex;
        const targetIn =
          (graph.getNodeAttribute(target, "assemblyIndex") as number) <= revealIndex;
        if (!sourceIn || !targetIn) return { ...data, hidden: true };
      }
      if (selectedId) {
        const [source, target] = graph.extremities(edge);
        if (!neighbourhood.has(source) || !neighbourhood.has(target)) {
          return { ...data, hidden: true };
        }
        return { ...data, color: "rgba(125,211,252,0.6)", size: (data.size as number) * 1.5 };
      }
      return data;
    });

    sigma.refresh();
  }, [phase, revealIndex, selectedId]);

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
        if (useGraphStore.getState().phase === "revealing") skipReveal();
        else select(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [skipReveal, select]);

  return <div ref={containerRef} className="graph-canvas" />;
}
