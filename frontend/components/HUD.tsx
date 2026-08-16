"use client";

import { STAGE_COPY, impactCounts } from "@/lib/impact";
import { useGraphStore } from "@/lib/store";

/** The top bar and the one-line readout at the bottom. Nothing else.
 *
 *  The node card that used to live here moved into the inspector — two panels
 *  describing the same node is one more than the screen can spare, and the
 *  card was competing with the graph for the right-hand edge.
 */
const LEVEL_NAMES = ["Architecture", "Modules", "Symbols"];

export default function HUD() {
  const phase = useGraphStore((s) => s.phase);
  const zoom = useGraphStore((s) => s.zoom);
  const setZoom = useGraphStore((s) => s.setZoom);
  const spec = useGraphStore((s) => s.spec);
  const repoUrl = useGraphStore((s) => s.repoUrl);
  const selectedId = useGraphStore((s) => s.selectedId);
  const explanation = useGraphStore((s) => s.explanation);
  const blast = useGraphStore((s) => s.blast);
  const rippleFor = useGraphStore((s) => s.rippleFor);
  const overlay = useGraphStore((s) => s.overlay);
  const clearOverlay = useGraphStore((s) => s.clearOverlay);
  const setPalette = useGraphStore((s) => s.setPalette);
  const setGuide = useGraphStore((s) => s.setGuide);
  const rippleFront = useGraphStore((s) => s.rippleFront);
  const rippleEndpoints = useGraphStore((s) => s.rippleEndpoints);

  if (phase !== "exploring" && phase !== "revealing") return null;

  const selected = spec?.nodes.find((node) => node.id === selectedId) ?? null;
  const role = explanation?.meta.role;
  // Recomputed every tick from the wavefront, so the caption always matches
  // the picture. A total printed while the wave is still moving would be a
  // line that disagrees with the thing it is describing.
  const impact = impactCounts(blast, rippleFront, rippleEndpoints);
  const repoName = repoUrl?.replace(/^https?:\/\/github\.com\//, "") ?? "";

  return (
    <>
      <header className="hud-top">
        <span className="brand">CodeLens</span>
        <span className="repo">{repoName}</span>
        <button
          className="guide-open"
          onClick={() => setGuide(true)}
          title="What am I looking at?"
          aria-label="Graph guide"
        >
          ⓘ
        </button>
        <button className="palette-open" onClick={() => setPalette(true)}>
          <span className="kbd">⌘K</span>
        </button>
        <nav className="zoom">
          {[1, 2, 3].map((level) => (
            <button
              key={level}
              className={level === zoom ? "active" : ""}
              onClick={() => setZoom(level)}
              title={LEVEL_NAMES[level - 1]}
            >
              L{level}
            </button>
          ))}
        </nav>
      </header>

      {/* A query's answer lives on the graph; this only names it. */}
      {overlay && (
        <div className="overlay-bar">
          <span className="overlay-label">
            {overlay.label} · {overlay.count}
          </span>
          {overlay.detail && <span className="overlay-detail">{overlay.detail}</span>}
          <button className="overlay-clear" onClick={clearOverlay} aria-label="Clear">
            ✕
          </button>
        </div>
      )}

      {/* The one-line readout. Says where you are, or what you are focused on
          — never both, and never more than a line. */}
      <footer className="hud-bottom">
        {rippleFor && blast ? (
          <span>
            <span className="hud-impact">IMPACT</span> {impact.files} files ·{" "}
            {impact.modules} modules
            {impact.endpoints > 0 && <> · {impact.endpoints} endpoints</>} ·{" "}
            {impact.hops} of {impact.maxHops} hops
            <span className="hud-hint"> — {STAGE_COPY[impact.stage]}</span>
          </span>
        ) : selected ? (
          <span>
            <span className="hud-focus">FOCUS</span> {selected.label}
            {role && (
              <>
                {" · "}
                {role.direct_dependencies} dependencies · {role.direct_dependents} dependents
              </>
            )}
          </span>
        ) : (
          <span>
            <span className="hud-level">
              L{zoom} · {LEVEL_NAMES[zoom - 1].toUpperCase()}
            </span>
            {spec && (
              <>
                {" "}
                {spec.nodes.length} nodes · {spec.edges.length} relationships
              </>
            )}
            <span className="hud-hint"> · Click to inspect · Double-click to dive in</span>
          </span>
        )}
      </footer>
    </>
  );
}
