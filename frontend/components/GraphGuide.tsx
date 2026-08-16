"use client";

import { useEffect } from "react";
import { useGraphStore } from "@/lib/store";

/** The Graph Guide — a short lesson, not a legend.
 *
 *  L1/L2/L3 are the single least self-explanatory thing in the product: three
 *  buttons labelled with a letter and a number, controlling a distinction
 *  nobody has met before. A legend listing what the colours mean would not
 *  fix that, because the question a first-time reader has is not "what is
 *  this colour" — it is "what am I looking at, and what is underneath it".
 *
 *  So this teaches the one idea that makes the rest obvious: the three levels
 *  are one world at three depths. Then it gets out of the way and stays out,
 *  reopenable from the top bar.
 *
 *  **Every line here describes what is actually rendered.** The encodings are
 *  read off `viewspec.py` and `GraphCanvas.tsx`, not off a design document —
 *  a guide that describes an intended visual language rather than the one on
 *  screen is worse than no guide, because it is believed.
 */
const LEVELS = [
  {
    tag: "L1",
    name: "Architecture",
    line: "The districts this repository is built from.",
  },
  {
    tag: "L2",
    name: "Modules",
    line: "The files inside them, and how they wire together.",
  },
  {
    tag: "L3",
    name: "Symbols",
    line: "The functions and classes that do the work.",
  },
];

export default function GraphGuide() {
  const phase = useGraphStore((s) => s.phase);
  const open = useGraphStore((s) => s.guideOpen);
  const setGuide = useGraphStore((s) => s.setGuide);

  // Shown once, unprompted, the first time someone reaches a graph — that is
  // the only moment the explanation is worth interrupting for. After that it
  // is on request only.
  useEffect(() => {
    if (phase !== "exploring") return;
    try {
      if (window.localStorage.getItem("codelens.guide.seen")) return;
    } catch {
      return; // private mode or storage disabled: never nag
    }
    setGuide(true);
  }, [phase, setGuide]);

  if (!open) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem("codelens.guide.seen", "1");
    } catch {
      /* storage disabled — dismissing still works for this session */
    }
    setGuide(false);
  };

  return (
    <div className="guide-scrim" onClick={dismiss}>
      <aside className="guide" onClick={(event) => event.stopPropagation()}>
        <h2 className="guide-title">One map, three depths</h2>
        <p className="guide-lede">
          The same repository, seen closer each time. Zoom out to understand it;
          zoom in to work on it.
        </p>

        <ol className="guide-levels">
          {LEVELS.map((level, index) => (
            <li key={level.tag}>
              <div className="guide-level">
                <span className="guide-tag">{level.tag}</span>
                <div>
                  <p className="guide-level-name">{level.name}</p>
                  <p className="guide-level-line">{level.line}</p>
                </div>
              </div>
              {index < LEVELS.length - 1 && <span className="guide-arrow">↓</span>}
            </li>
          ))}
        </ol>

        <div className="guide-legend">
          <Legend label="Size" meaning="How much the project leans on it." />
          <Legend label="Colour" meaning="Calm blue to hot red as risk rises. Green starts a run." />
          <Legend label="Line brightness" meaning="How close it is to whatever you selected." />
          <Legend
            label="Line warmth"
            meaning="How sure the parser is. Neutral is proven; warm is a guess."
          />
        </div>

        <p className="guide-keys">
          Click to inspect · Double-click to dive in ·{" "}
          <span className="kbd">⌘K</span> for everything else
        </p>
        <p className="guide-keys">
          <span className="kbd">1</span> <span className="kbd">2</span>{" "}
          <span className="kbd">3</span> change depth · arrows follow a
          relationship · <span className="kbd">Esc</span> steps back
        </p>

        <button className="guide-dismiss" onClick={dismiss}>
          Got it
        </button>
      </aside>
    </div>
  );
}

function Legend({ label, meaning }: { label: string; meaning: string }) {
  return (
    <div className="guide-legend-row">
      <span className="guide-legend-label">{label}</span>
      <span className="guide-legend-meaning">{meaning}</span>
    </div>
  );
}
