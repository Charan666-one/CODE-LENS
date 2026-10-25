import Graph from "graphology";
import { describe, expect, it } from "vitest";
import { anchorFor, buildFrame, type ChoreographyState } from "./choreography";
import type { BlastResult } from "./types";

/** The visual language, tested.
 *
 *  This module is the one place both renderers read, so a mistake here is a
 *  mistake the flat and deep views make identically and agree about — which
 *  is exactly the kind that survives a look at the screen. The browser
 *  checks that have stood in for this until now cannot tell a dimmed node
 *  from a hidden one, or `#0d1524` from `#334155`.
 */

/** Two files, one importing the other, plus a symbol inside the first. */
function fixture(): Graph {
  const graph = new Graph({ multi: true, type: "directed" });
  const node = (id: string, size: number, assemblyIndex: number) =>
    graph.addNode(id, { size, assemblyIndex, color: "#60a5fa", label: id });
  node("file:a.py", 10, 0);
  node("file:b.py", 6, 1);
  node("file:c.py", 4, 2);
  graph.addEdge("file:a.py", "file:b.py", { baseSize: 1, confidence: "resolved" });
  graph.addEdge("file:b.py", "file:c.py", { baseSize: 1, confidence: "heuristic" });
  return graph;
}

const RESTING: ChoreographyState = {
  phase: "exploring",
  revealIndex: 99,
  selectedId: null,
  selectedPath: null,
  blast: null,
  rippleFor: null,
  rippleFront: 0,
  overlay: null,
};

const frameOf = (graph: Graph, state: Partial<ChoreographyState> = {}) =>
  buildFrame(graph, { ...RESTING, ...state });

const nodeOf = (graph: Graph, frame: ReturnType<typeof buildFrame>, id: string) =>
  frame.node(id, graph.getNodeAttributes(id));

describe("the assembly reveal", () => {
  it("hides what has not been built yet, and only while revealing", () => {
    const graph = fixture();
    const mid = frameOf(graph, { phase: "revealing", revealIndex: 1 });
    expect(nodeOf(graph, mid, "file:a.py").hidden).toBeUndefined();
    expect(nodeOf(graph, mid, "file:b.py").hidden).toBeUndefined();
    expect(nodeOf(graph, mid, "file:c.py").hidden).toBe(true);

    // Same index, no longer revealing: nothing is hidden.
    const done = frameOf(graph, { phase: "exploring", revealIndex: 1 });
    expect(nodeOf(graph, done, "file:c.py").hidden).toBeUndefined();
  });

  it("hides an edge until both of its ends exist", () => {
    const graph = fixture();
    const frame = frameOf(graph, { phase: "revealing", revealIndex: 1 });
    const attrs = graph.getEdgeAttributes(graph.edges()[1]); // b -> c
    expect(frame.edge("file:b.py", "file:c.py", attrs).hidden).toBe(true);
    const first = graph.getEdgeAttributes(graph.edges()[0]); // a -> b
    expect(frame.edge("file:a.py", "file:b.py", first).hidden).toBeUndefined();
  });
});

describe("the relevance hierarchy", () => {
  it("separates anchor, direct, second degree and the rest", () => {
    const graph = fixture();
    const frame = frameOf(graph, { selectedId: "file:a.py" });

    const anchor = nodeOf(graph, frame, "file:a.py");
    expect(anchor.color).toBe("#ffffff");
    expect(anchor.forceLabel).toBe(true);
    expect(anchor.size).toBeGreaterThan(10); // emphasis is added, then capped

    const direct = nodeOf(graph, frame, "file:b.py");
    expect(direct.forceLabel).toBe(true);
    expect(direct.color).toBeUndefined(); // keeps its own risk colour

    const second = nodeOf(graph, frame, "file:c.py");
    expect(second.color).toBe("#334155");
    expect(second.label).toBeNull();
  });

  it("caps the anchor's growth so a district does not swallow the screen", () => {
    const graph = fixture();
    graph.setNodeAttribute("file:a.py", "size", 40); // an L1 district
    const frame = frameOf(graph, { selectedId: "file:a.py" });
    expect(nodeOf(graph, frame, "file:a.py").size).toBe(49); // 40 + min(48, 9)
  });
});

describe("the ripple", () => {
  const blast = (entries: [string, number][]): BlastResult => ({
    focus_id: "file:a.py",
    node_ids: entries.map(([id]) => id),
    ranked: entries.map(([id, distance]) => ({
      node_id: id,
      score: 1,
      reasons: {
        distance,
        fan_in: 0,
        path_confidence: "resolved",
        file_path: id.replace("file:", ""),
        name: id,
        kind: "file",
      },
    })),
    paths: {},
    meta: { total_affected: entries.length },
  });

  it("lights what the wave has reached and darkens what it has not", () => {
    const graph = fixture();
    const frame = frameOf(graph, {
      rippleFor: "file:a.py",
      rippleFront: 1,
      blast: blast([
        ["file:b.py", 1],
        ["file:c.py", 2],
      ]),
    });

    expect(frame.rippleActive).toBe(true);
    expect(nodeOf(graph, frame, "file:a.py").color).toBe("#f8fafc"); // the source

    const reached = nodeOf(graph, frame, "file:b.py");
    expect(reached.color).toMatch(/^rgba\(249,/); // the impact ramp
    expect(reached.size).toBeCloseTo(6 * 1.4); // direct dependents grow most

    const notYet = nodeOf(graph, frame, "file:c.py");
    expect(notYet.color).toBe("#1e293b"); // distance 2, front 1
    expect(notYet.label).toBeNull();
  });

  it("suppresses an overlay while a wave is running", () => {
    const graph = fixture();
    const frame = frameOf(graph, {
      rippleFor: "file:a.py",
      rippleFront: 1,
      blast: blast([["file:b.py", 1]]),
      overlay: {
        query: "cycles",
        label: "Circular dependencies",
        nodeIds: ["file:c.py"],
        groups: [],
        detail: "",
        count: 1,
      },
    });
    // The wave owns the canvas: nothing is lit overlay-cyan while it runs.
    const inside = graph.getEdgeAttributes(graph.edges()[0]); // a -> b, both reached
    const impact = frame.edge("file:a.py", "file:b.py", inside);
    expect(impact.color).toBe("rgba(251,146,60,0.5)");
    expect(impact.color).not.toContain("125,211,252");

    // And the wiring the wave has not reached stays dark rather than showing
    // the overlay answer early.
    const ahead = graph.getEdgeAttributes(graph.edges()[1]); // b -> c, c is distance 2
    expect(frame.edge("file:b.py", "file:c.py", ahead)).toEqual({ hidden: true });
  });
});

describe("a query overlay", () => {
  it("keeps only the wiring between answered nodes", () => {
    const graph = fixture();
    const frame = frameOf(graph, {
      overlay: {
        query: "cycles",
        label: "Circular dependencies",
        nodeIds: ["file:a.py", "file:b.py"],
        groups: [],
        detail: "",
        count: 2,
      },
    });
    const answered = graph.getEdgeAttributes(graph.edges()[0]); // a -> b
    expect(frame.edge("file:a.py", "file:b.py", answered).color).toContain("125,211,252");
    const other = graph.getEdgeAttributes(graph.edges()[1]); // b -> c
    expect(frame.edge("file:b.py", "file:c.py", other).hidden).toBe(true);
  });
});

describe("edge encodings", () => {
  it("carries confidence in hue and width, never in alpha alone", () => {
    const graph = fixture();
    const frame = frameOf(graph);
    const proven = frame.edge(
      "file:a.py",
      "file:b.py",
      graph.getEdgeAttributes(graph.edges()[0]),
    );
    const guess = frame.edge(
      "file:b.py",
      "file:c.py",
      graph.getEdgeAttributes(graph.edges()[1]),
    );

    expect(proven.color).toContain("148,163,184"); // slate: a proven edge
    expect(guess.color).toContain("186,168,148"); // warm: a name match
    // A guess is drawn thinner than a proof at the same relevance.
    expect(guess.size!).toBeLessThan(proven.size!);
  });
});

describe("anchorFor", () => {
  it("falls back to the file holding a symbol this level cannot draw", () => {
    const graph = fixture();
    expect(anchorFor(graph, "function:a.helper", "a.py")).toBe("file:a.py");
  });

  it("gives up rather than dimming a canvas nothing will light", () => {
    const graph = fixture();
    expect(anchorFor(graph, "function:z.gone", "z.py")).toBeNull();
    expect(anchorFor(graph, null, null)).toBeNull();
  });
});
