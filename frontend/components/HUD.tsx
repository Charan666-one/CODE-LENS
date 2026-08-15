"use client";

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

  if (phase !== "exploring" && phase !== "revealing") return null;

  const selected = spec?.nodes.find((node) => node.id === selectedId) ?? null;
  const role = explanation?.meta.role;
  const repoName = repoUrl?.replace(/^https?:\/\/github\.com\//, "") ?? "";

  return (
    <>
      <header className="hud-top">
        <span className="brand">CodeLens</span>
        <span className="repo">{repoName}</span>
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
        {selected ? (
          <span>
            <span className="hud-focus">FOCUS</span> {selected.label}
            {role && (
              <>
                {" · "}
                {role.direct_dependencies} dependencies · {role.direct_dependents} dependents
              </>
            )}
            {rippleFor && blast && <> · {blast.meta.total_affected} affected</>}
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
            <span className="hud-hint"> · Scroll to zoom · Click to inspect</span>
          </span>
        )}
      </footer>
    </>
  );
}
