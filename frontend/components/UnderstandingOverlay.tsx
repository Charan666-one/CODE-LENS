"use client";

import { useEffect } from "react";
import { useGraphStore } from "@/lib/store";

/** The pipeline copy, in understanding-language (EXPERIENCE.md).
 *
 *  Honest theater: the lines below are the backend's REAL stages, with their
 *  REAL measured durations, replayed. Nothing here invents progress — if the
 *  pipeline had three stages, three lines appear.
 */
const STAGE_COPY: Record<string, string> = {
  cloned: "Reading repository…",
  parsed: "Parsing structure…",
  metrics: "Learning its history…",
  graph_built: "Building Repository Brain…",
};

export default function UnderstandingOverlay() {
  const phase = useGraphStore((s) => s.phase);
  const stages = useGraphStore((s) => s.stages);
  const stagesShown = useGraphStore((s) => s.stagesShown);
  const advanceStage = useGraphStore((s) => s.advanceStage);
  const beginReveal = useGraphStore((s) => s.beginReveal);

  useEffect(() => {
    if (phase !== "understanding" || stages.length === 0) return;
    if (stagesShown >= stages.length) {
      const done = window.setTimeout(beginReveal, 500); // beat of stillness
      return () => window.clearTimeout(done);
    }
    // Replay each real stage; compress its real duration into 250–900ms.
    const real = stages[stagesShown]?.seconds ?? 0.3;
    const delay = Math.min(900, Math.max(250, real * 400));
    const timer = window.setTimeout(advanceStage, delay);
    return () => window.clearTimeout(timer);
  }, [phase, stages, stagesShown, advanceStage, beginReveal]);

  if (phase !== "understanding") return null;

  return (
    <div className="overlay">
      <div className="understanding">
        <p className="understanding-title">Understanding repository…</p>
        {stages.length === 0 && <p className="stage-line pending">Contacting the pipeline…</p>}
        {stages.slice(0, stagesShown + 1).map((stage, index) => (
          <p
            key={stage.stage}
            className={`stage-line ${index < stagesShown ? "done" : "pending"}`}
          >
            {STAGE_COPY[stage.stage] ?? stage.stage}
            {index < stagesShown && (
              <span className="tick">
                {" "}
                ✓<span className="ms"> {(stage.seconds * 1000).toFixed(0)}ms</span>
              </span>
            )}
          </p>
        ))}
      </div>
    </div>
  );
}
