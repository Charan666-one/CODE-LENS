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

  if (phase !== "exploring" && phase !== "revealing") return null;

  const selected = spec?.nodes.find((node) => node.id === selectedId) ?? null;

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
          <p className="node-hint">Lit nodes are its direct world. Esc to release.</p>
        </aside>
      )}
    </>
  );
}
