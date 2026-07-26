"use client";

import dynamic from "next/dynamic";
import HeroInput from "@/components/HeroInput";
import HUD from "@/components/HUD";
import ExplanationPage from "@/components/ExplanationPage";
import UnderstandingOverlay from "@/components/UnderstandingOverlay";

// Sigma needs the browser's WebGL context; never render it on the server.
const GraphCanvas = dynamic(() => import("@/components/GraphCanvas"), { ssr: false });

export default function Home() {
  return (
    <main className="stage">
      <GraphCanvas />
      <HeroInput />
      <UnderstandingOverlay />
      <HUD />
      <ExplanationPage />
    </main>
  );
}
