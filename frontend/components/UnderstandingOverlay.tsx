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

  // The percentage is of *measured work*, not of a timer.
  //
  // A progress bar that animates 0 → 100 on a guess is the standard way to
  // do this and it is a lie: it says "72% done" while knowing nothing about
  // what remains. These stages have already run, and each one reported the
  // seconds it actually cost, so the share of total time that the replay has
  // reached is a real fraction of real work. When every stage was a cache hit
  // the total is zero, there is no work to be a fraction of, and the readout
  // says so instead of dividing by nothing.
  const total = stages.reduce((sum, stage) => sum + stage.seconds, 0);
  const elapsed = stages
    .slice(0, stagesShown)
    .reduce((sum, stage) => sum + stage.seconds, 0);
  // Rounding is not allowed to reach 100 while a stage is still running. On
  // itsdangerous the git pass is 8.4s of 10.4s, so the last stage begins at
  // 99.6% and `Math.round` turned that into a readout claiming the work was
  // finished under a line that said it was not. 99 is the honest ceiling
  // until the last stage has actually landed.
  const finished = stagesShown >= stages.length;
  const raw = total > 0 ? (elapsed / total) * 100 : null;
  const percent =
    raw === null ? null : finished ? 100 : Math.min(99, Math.round(raw));

  return (
    <div className="overlay">
      <div className="understanding">
        <p className="understanding-title">Understanding repository…</p>
        {stages.length === 0 && (
          // Same three-part shape as a real stage line. A bare text node in
          // this grid becomes an anonymous item in the 1rem marker column and
          // wraps one word per line.
          <p className="stage-line pending">
            <span className="stage-mark">●</span>
            <span className="stage-name">Contacting the pipeline…</span>
          </p>
        )}
        {stages.slice(0, stagesShown + 1).map((stage, index) => {
          const done = index < stagesShown;
          return (
            <p key={stage.stage} className={`stage-line ${done ? "done" : "pending"}`}>
              <span className="stage-mark">{done ? "✓" : "●"}</span>
              <span className="stage-name">{STAGE_COPY[stage.stage] ?? stage.stage}</span>
              {done && (
                <span className="stage-detail">
                  {stage.detail}
                  <span className="ms">
                    {/* A cache hit took no time, and "0ms" reads as a
                        measurement failure rather than as work not redone. */}
                    {stage.skipped ? "cached" : `${(stage.seconds * 1000).toFixed(0)}ms`}
                  </span>
                </span>
              )}
            </p>
          );
        })}
        {stages.length > 0 && (
          <p className="stage-percent">
            {percent === null ? "already understood" : `${percent}%`}
          </p>
        )}
      </div>
    </div>
  );
}
