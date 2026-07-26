"use client";

import { useGraphStore } from "@/lib/store";

/** Zoom control, the selected node's card, and the repo line.
 *  Reads the one store; computes nothing. */
export default function HUD() {
  const phase = useGraphStore((s) => s.phase);
  const zoom = useGraphStore((s) => s.zoom);
  const setZoom = useGraphStore((s) => s.setZoom);
  const spec = useGraphStore((s) => s.spec);
  const selectedId = useGraphStore((s) => s.selectedId);
  const repoUrl = useGraphStore((s) => s.repoUrl);
  const blast = useGraphStore((s) => s.blast);
  const rippleFor = useGraphStore((s) => s.rippleFor);
  const showRipple = useGraphStore((s) => s.showRipple);
  const clearRipple = useGraphStore((s) => s.clearRipple);
  const openExplanation = useGraphStore((s) => s.openExplanation);

  if (phase !== "exploring" && phase !== "revealing") return null;

  const selected = spec?.nodes.find((node) => node.id === selectedId) ?? null;
  const rippleActive = rippleFor !== null && blast !== null;

  return (
    <>
      <header className="hud-top">
        <span className="brand">CodeLens</span>
        <span className="repo">{repoUrl}</span>
        <nav className="zoom">
          {[1, 2, 3].map((level) => (
            <button
              key={level}
              className={level === zoom ? "active" : ""}
              onClick={() => setZoom(level)}
              title={["Districts", "Files", "Functions"][level - 1]}
            >
              L{level}
            </button>
          ))}
        </nav>
      </header>

      {selected && (
        <aside className="node-card">
          <p className="node-kind">{selected.kind}</p>
          <h2 className="node-name">{selected.label}</h2>
          <dl>
            <div>
              <dt>fan-in</dt>
              <dd>{selected.fan_in}</dd>
            </div>
            <div>
              <dt>risk</dt>
              <dd>{(selected.risk * 100).toFixed(0)}%</dd>
            </div>
            {selected.is_entrypoint && (
              <div>
                <dt>role</dt>
                <dd>entrypoint</dd>
              </div>
            )}
          </dl>
          {selected.file_path && (
            <p className="node-evidence">
              {selected.file_path}
              {selected.start_line ? `:${selected.start_line}` : ""}
            </p>
          )}

          {rippleActive ? (
            <div className="ripple-readout">
              <p className="ripple-count">
                <strong>{blast.meta.total_affected}</strong> modules break if you
                change this
              </p>
              <p className="node-hint">
                The wave is the real blast radius — fading with distance. Esc to release.
              </p>
              <button className="ripple-clear" onClick={clearRipple}>
                Clear ripple
              </button>
            </div>
          ) : (
            <>
              <button
                className="explain-trigger"
                onClick={() => openExplanation(selected.explain_id ?? selected.id)}
              >
                Explain this {selected.kind}
              </button>
              <button className="ripple-trigger" onClick={() => showRipple(selected.explain_id ?? selected.id)}>
                ⟿ What breaks if I change this?
              </button>
            </>
          )}
        </aside>
      )}
    </>
  );
}
