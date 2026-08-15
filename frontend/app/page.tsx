"use client";

import dynamic from "next/dynamic";
import HeroInput from "@/components/HeroInput";
import CommandPalette from "@/components/CommandPalette";
import GraphGuide from "@/components/GraphGuide";
import HUD from "@/components/HUD";
import NodeInspector from "@/components/NodeInspector";
import UnderstandingOverlay from "@/components/UnderstandingOverlay";

// Sigma needs the browser's WebGL context; never render it on the server.
const GraphCanvas = dynamic(() => import("@/components/GraphCanvas"), { ssr: false });

/** One canvas. The graph fills the stage; everything else floats over it and
 *  only when it has something to say. */
export default function Home() {
  return (
    <main className="stage">
      <GraphCanvas />
      <HeroInput />
      <UnderstandingOverlay />
      <HUD />
      <NodeInspector />
      <CommandPalette />
      <GraphGuide />
    </main>
  );
}
