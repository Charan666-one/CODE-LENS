"use client";

import { useEffect } from "react";
import { useGraphStore } from "@/lib/store";

/** The explanation page: why the project needs this file or folder.
 *
 *  Opens over the map (the graph stays behind it, so you keep your place)
 *  and answers, in order, the questions a person actually has when they
 *  click something:
 *
 *    what is this · what would break without it · what does it need ·
 *    what lives inside it · and the evidence for every claim
 *
 *  Every number here is a graph fact the reader can check on the map — the
 *  panel computes nothing (ARCHITECTURE.md §6).
 */
export default function ExplanationPage() {
  const explaining = useGraphStore((s) => s.explaining);
  const explanation = useGraphStore((s) => s.explanation);
  const explainError = useGraphStore((s) => s.explainError);
  const close = useGraphStore((s) => s.closeExplanation);
  const openExplanation = useGraphStore((s) => s.openExplanation);
  const showRipple = useGraphStore((s) => s.showRipple);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && useGraphStore.getState().explaining) close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);

  if (!explaining) return null;

  const identity = explanation?.meta.identity;
  const role = explanation?.meta.role;

  return (
    <div className="explain-scrim" onClick={close}>
      <aside className="explain-page" onClick={(event) => event.stopPropagation()}>
        <header className="explain-head">
          <div>
            <p className="explain-kind">{identity?.kind ?? "loading"}</p>
            <h1>{identity?.name ?? explaining.split(":").slice(1).join(":")}</h1>
            {identity?.file_path && (
              <p className="explain-path">
                {identity.file_path}
                {identity.start_line ? `:${identity.start_line}` : ""}
              </p>
            )}
          </div>
          <button className="explain-close" onClick={close} aria-label="Close">
            ✕
          </button>
        </header>

        {explainError && <p className="explain-error">{explainError}</p>}
        {!explanation && !explainError && <p className="explain-loading">Reading the graph…</p>}

        {explanation && role && identity && (
          <>
            <p className="explain-verdict">{role.verdict}</p>

            {explanation.summary && (
              <p className="explain-summary">{explanation.summary.text}</p>
            )}
            {!explanation.summary && identity.docstring && (
              <p className="explain-summary">{identity.docstring}</p>
            )}

            <div className="explain-stats">
              <Stat label="used by" value={role.direct_dependents} hint="direct" />
              <Stat
                label="ripples to"
                value={role.transitive_dependents}
                hint="downstream"
              />
              <Stat label="depends on" value={role.direct_dependencies} hint="direct" />
              {identity.loc !== null && <Stat label="lines" value={identity.loc} />}
              {identity.complexity !== null && (
                <Stat label="complexity" value={identity.complexity} />
              )}
              {identity.churn_count !== null && (
                <Stat label="commits" value={identity.churn_count} hint="churn" />
              )}
            </div>

            <Section
              title="What breaks without it"
              empty="Nothing depends on this — changes here stay contained."
              items={explanation.meta.used_by}
              onOpen={openExplanation}
            />

            <Section
              title="What it needs to work"
              empty="Depends on nothing else in this project."
              items={explanation.meta.depends_on}
              onOpen={openExplanation}
            />

            {explanation.meta.contains.top.length > 0 && (
              <section className="explain-section">
                <h2>
                  What&apos;s inside{" "}
                  <span className="explain-counts">
                    {Object.entries(explanation.meta.contains.counts)
                      .map(([kind, count]) => `${count} ${kind}`)
                      .join(" · ")}
                  </span>
                </h2>
                <ul className="explain-list">
                  {explanation.meta.contains.top.map((child) => (
                    <li key={child.id}>
                      <button onClick={() => openExplanation(child.id)}>
                        <span className="explain-name">{child.name}</span>
                        <span className="explain-meta">
                          {child.kind}
                          {child.fan_in > 0 ? ` · used ${child.fan_in}×` : ""}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {Object.keys(explanation.paths).length > 0 && (
              <section className="explain-section">
                <h2>
                  Evidence <span className="explain-counts">real dependency chains</span>
                </h2>
                <ul className="explain-paths">
                  {Object.entries(explanation.paths).map(([dependent, path]) => (
                    <li key={dependent}>
                      {path
                        .map((step) => step.split(":").slice(1).join(":"))
                        .join("  →  ")}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <footer className="explain-foot">
              <button
                className="explain-ripple"
                onClick={() => {
                  close();
                  void showRipple(explanation.focus_id);
                }}
              >
                ⟿ Show the ripple on the map
              </button>
              <p className="explain-note">
                Every number here is a fact from the graph — click any name to
                explain it too. Esc to close.
              </p>
            </footer>
          </>
        )}
      </aside>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: number;
  hint?: string;
}) {
  return (
    <div className="explain-stat">
      <dt>{label}</dt>
      <dd>{value}</dd>
      {hint && <span>{hint}</span>}
    </div>
  );
}

function Section({
  title,
  empty,
  items,
  onOpen,
}: {
  title: string;
  empty: string;
  items: { id: string; name: string; file_path: string | null; references: number }[];
  onOpen: (id: string) => void;
}) {
  return (
    <section className="explain-section">
      <h2>{title}</h2>
      {items.length === 0 ? (
        <p className="explain-empty">{empty}</p>
      ) : (
        <ul className="explain-list">
          {items.map((item) => (
            <li key={item.id}>
              <button onClick={() => onOpen(item.id)}>
                <span className="explain-name">{item.name}</span>
                <span className="explain-meta">
                  {item.references} reference{item.references === 1 ? "" : "s"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
