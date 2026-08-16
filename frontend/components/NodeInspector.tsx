"use client";

import { STAGE_COPY, impactCounts } from "@/lib/impact";
import { useGraphStore } from "@/lib/store";
import type { CoChangePartner, EndpointRef, TestFile } from "@/lib/types";

/** The inspector — a microscope, not a panel.
 *
 *  This replaces two surfaces that used to describe the same node: a card in
 *  the corner and a 560px full-height slide-over behind a scrim. Between them
 *  they could occupy most of the screen and hide the graph the answer was
 *  about, which is backwards for a product whose whole argument is that the
 *  graph is the thing.
 *
 *  So: one floating panel, never full height, never with a scrim, showing the
 *  handful of numbers that decide what to do next. The deep detail is one
 *  click away inside the same panel rather than in a second surface — the
 *  information did not get worse, it stopped being permanent.
 *
 *  Every field comes from the `explain` payload the store already fetched on
 *  selection. This component performs no fetch and computes nothing.
 */
export default function NodeInspector() {
  const phase = useGraphStore((s) => s.phase);
  const spec = useGraphStore((s) => s.spec);
  const selectedId = useGraphStore((s) => s.selectedId);
  const explanation = useGraphStore((s) => s.explanation);
  const explainError = useGraphStore((s) => s.explainError);
  const detailOpen = useGraphStore((s) => s.detailOpen);
  const toggleDetail = useGraphStore((s) => s.toggleDetail);
  const close = useGraphStore((s) => s.closeInspector);
  const select = useGraphStore((s) => s.select);
  const showRipple = useGraphStore((s) => s.showRipple);
  const blast = useGraphStore((s) => s.blast);
  const rippleFor = useGraphStore((s) => s.rippleFor);
  const clearRipple = useGraphStore((s) => s.clearRipple);
  const dive = useGraphStore((s) => s.dive);
  const zoom = useGraphStore((s) => s.zoom);
  const rippleFront = useGraphStore((s) => s.rippleFront);
  const rippleEndpoints = useGraphStore((s) => s.rippleEndpoints);

  if (phase !== "exploring" && phase !== "revealing") return null;
  if (!selectedId) return null;

  const viewNode = spec?.nodes.find((node) => node.id === selectedId) ?? null;
  const identity = explanation?.meta.identity;
  const role = explanation?.meta.role;
  const rippleActive = rippleFor !== null && blast !== null;
  const graphId = viewNode?.explain_id ?? selectedId;
  const impact = impactCounts(blast, rippleFront, rippleEndpoints);

  return (
    <aside className="inspector">
      <header className="inspector-head">
        <div className="inspector-title">
          <p className="inspector-kind">{identity?.kind ?? viewNode?.kind ?? "node"}</p>
          <h2>{identity?.name ?? viewNode?.label ?? selectedId.split(":").slice(1).join(":")}</h2>
        </div>
        <button className="inspector-close" onClick={close} aria-label="Close">
          ✕
        </button>
      </header>

      {identity?.file_path && (
        <p className="inspector-path">
          {identity.file_path}
          {identity.start_line ? `:${identity.start_line}` : ""}
        </p>
      )}

      {explainError && <p className="inspector-error">{explainError}</p>}
      {!explanation && !explainError && <p className="inspector-loading">Reading the graph…</p>}

      {explanation && role && identity && (
        <>
          <dl className="inspector-stats">
            <Stat label="dependents" value={role.direct_dependents} />
            <Stat label="dependencies" value={role.direct_dependencies} />
            <Stat label="ripples to" value={role.transitive_dependents} />
            {identity.complexity !== null && (
              <Stat label="complexity" value={identity.complexity} />
            )}
          </dl>

          <p className="inspector-verdict">{role.verdict}</p>

          {rippleActive ? (
            <>
              {/* The counters climb with the wave. The endpoint list is the
                  payoff: a file count is a number to interpret, a URL is a
                  decision about whether to deploy. */}
              <div className="impact-readout">
                <p className="impact-stage">
                  {impact.hops} of {impact.maxHops} hops — {STAGE_COPY[impact.stage]}
                </p>
                <dl className="inspector-stats">
                  <Stat label="files" value={impact.files} />
                  <Stat label="modules" value={impact.modules} />
                </dl>
                {impact.endpoints > 0 && (
                  <section className="inspector-section">
                    <h3>Endpoints affected</h3>
                    <ul className="inspector-list">
                      {rippleEndpoints.slice(0, 5).map((endpoint) => (
                        <li key={endpoint.id}>
                          <div className="inspector-static">
                            <span className="inspector-name">
                              <span className="inspector-method">{endpoint.method}</span>
                              {endpoint.path}
                            </span>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
              </div>
              <div className="inspector-actions">
                <button className="inspector-action" onClick={clearRipple}>
                  Clear impact
                </button>
              </div>
            </>
          ) : (
            <div className="inspector-actions">
              {/* Double-click on the canvas does this too, but a gesture
                  nobody can see is a gesture nobody uses. */}
              {zoom < 3 && (
                <button
                  className="inspector-action"
                  onClick={() => void dive(selectedId)}
                  title="Go a level deeper, here"
                >
                  Dive in
                </button>
              )}
              <button className="inspector-action" onClick={() => void showRipple(graphId)}>
                Impact
              </button>
              <button className="inspector-action" onClick={toggleDetail}>
                {detailOpen ? "Less" : "Explain"}
              </button>
            </div>
          )}

          {detailOpen && (
            <div className="inspector-detail">
              {explanation.summary && (
                <p className="inspector-summary">{explanation.summary.text}</p>
              )}
              {!explanation.summary && identity.docstring && (
                <p className="inspector-summary">{identity.docstring}</p>
              )}

              <Neighbours
                title="What breaks without it"
                empty="Nothing depends on this."
                items={explanation.meta.used_by}
                onOpen={select}
              />
              <Neighbours
                title="What it needs"
                empty="Depends on nothing else here."
                items={explanation.meta.depends_on}
                onOpen={select}
              />

              <Endpoints endpoints={explanation.meta.endpoints ?? []} />
              <Coverage tests={explanation.meta.tested_by ?? []} onOpen={select} />
              <CoChanges partners={explanation.meta.co_changes ?? []} onOpen={select} />

              {Object.keys(explanation.paths).length > 0 && (
                <section className="inspector-section">
                  <h3>Evidence</h3>
                  <ul className="inspector-paths">
                    {Object.entries(explanation.paths).map(([dependent, path]) => (
                      <li key={dependent}>
                        {path.map((step) => step.split(":").slice(1).join(":")).join("  →  ")}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          )}
        </>
      )}
    </aside>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="inspector-stat">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function Neighbours({
  title,
  empty,
  items,
  onOpen,
}: {
  title: string;
  empty: string;
  items: { id: string; name: string; references: number }[];
  onOpen: (id: string) => void;
}) {
  return (
    <section className="inspector-section">
      <h3>{title}</h3>
      {items.length === 0 ? (
        <p className="inspector-empty">{empty}</p>
      ) : (
        <ul className="inspector-list">
          {items.slice(0, 6).map((item) => (
            <li key={item.id}>
              <button onClick={() => onOpen(item.id)}>
                <span className="inspector-name">{item.name}</span>
                <span className="inspector-meta">{item.references}×</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The URLs a change here would reach — the most decision-shaped fact
 *  available, so it leads the detail rather than trailing it. */
function Endpoints({ endpoints }: { endpoints: EndpointRef[] }) {
  if (endpoints.length === 0) return null;
  return (
    <section className="inspector-section">
      <h3>Endpoints affected</h3>
      <ul className="inspector-list">
        {endpoints.slice(0, 6).map((endpoint) => (
          <li key={endpoint.id}>
            <div className="inspector-static">
              <span className="inspector-name">
                <span className="inspector-method">{endpoint.method}</span>
                {endpoint.path}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Empty is the interesting case, so it renders rather than disappearing —
 *  and says what was actually checked, not the stronger thing a reader might
 *  hear. An import graph cannot see a test that exercises code without
 *  importing it. */
function Coverage({ tests, onOpen }: { tests: TestFile[]; onOpen: (id: string) => void }) {
  return (
    <section className="inspector-section">
      <h3>Tested by</h3>
      {tests.length === 0 ? (
        <p className="inspector-empty">No test file imports this.</p>
      ) : (
        <ul className="inspector-list">
          {tests.slice(0, 5).map((test) => (
            <li key={test.id}>
              <button onClick={() => onOpen(test.id)}>
                <span className="inspector-name">{test.name}</span>
                <span className="inspector-meta">
                  {test.named_for_it ? "named for it" : "imports it"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** History says these travel together. `hidden` — no import, no call, yet
 *  they keep shipping in the same commits — is the finding. */
function CoChanges({
  partners,
  onOpen,
}: {
  partners: CoChangePartner[];
  onOpen: (id: string) => void;
}) {
  if (partners.length === 0) return null;
  const hidden = partners.filter((partner) => partner.hidden).length;
  return (
    <section className="inspector-section">
      <h3>Changes together with</h3>
      <ul className="inspector-list">
        {partners.slice(0, 5).map((partner) => (
          <li key={partner.id}>
            <button onClick={() => onOpen(partner.id)}>
              <span className="inspector-name">
                {partner.hidden && <span className="inspector-flag">hidden</span>}
                {partner.name}
              </span>
              <span className="inspector-meta">{Math.round(partner.strength * 100)}%</span>
            </button>
          </li>
        ))}
      </ul>
      {hidden > 0 && (
        <p className="inspector-note">
          Nothing in the code connects the files marked hidden — yet they keep
          changing in the same commits.
        </p>
      )}
    </section>
  );
}
