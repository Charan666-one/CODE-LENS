"use client";

import dynamic from "next/dynamic";
import HeroInput from "@/components/HeroInput";
import { useGraphStore } from "@/lib/store";
import CommandPalette from "@/components/CommandPalette";
import GraphGuide from "@/components/GraphGuide";
import HUD from "@/components/HUD";
import NodeInspector from "@/components/NodeInspector";
import UnderstandingOverlay from "@/components/UnderstandingOverlay";

// Both renderers need the browser's WebGL context; never render either on
// the server. Splitting them also keeps three.js out of the bundle a reader
// who never opens the deep view has to download.
const GraphCanvas = dynamic(() => import("@/components/GraphCanvas"), { ssr: false });
const Graph3DCanvas = dynamic(() => import("@/components/Graph3DCanvas"), { ssr: false });

/** One canvas. The graph fills the stage; everything else floats over it and
 *  only when it has something to say. */
export default function Home() {
  // Only one canvas is ever mounted, so only one WebGL context is ever live.
  // Everything that survives the swap — phase, spec, selection, the ripple in
  // progress — lives in the store, which is why switching mid-wave continues
  // the wave instead of restarting it.
  const dimension = useGraphStore((s) => s.dimension);

  return (
    <main className="stage">
      {dimension === "3d" ? <Graph3DCanvas /> : <GraphCanvas />}
      <HeroInput />
      <UnderstandingOverlay />
      <HUD />
      <NodeInspector />
      <CommandPalette />
      <GraphGuide />
    </main>
  );
}
