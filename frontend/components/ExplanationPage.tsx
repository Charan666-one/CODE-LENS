"use client";

import { useEffect } from "react";
import { useGraphStore } from "@/lib/store";
import type {
  CoChangePartner,
  EndpointRef,
  Ownership as OwnershipFacts,
  TestFile,
} from "@/lib/types";

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

            <Endpoints endpoints={explanation.meta.endpoints ?? []} />

            <Coverage
              tests={explanation.meta.tested_by ?? []}
              isTestFile={identity.file_path?.includes("test") ?? false}
              onOpen={openExplanation}
            />

            <Ownership ownership={explanation.meta.ownership ?? null} />

            <CoChanges
              partners={explanation.meta.co_changes ?? []}
              onOpen={openExplanation}
            />

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

/** The URLs a change here would reach.
 *
 *  Placed directly under the stats because it is the most decision-shaped
 *  fact on the page: a dependent count is a number to interpret, and a list
 *  of routes is something you can hold against a deploy.
 */
function Endpoints({ endpoints }: { endpoints: EndpointRef[] }) {
  if (endpoints.length === 0) return null;

  return (
    <section className="explain-section">
      <h2>
        Endpoints affected{" "}
        <span className="explain-counts">
          changing this changes what these serve
        </span>
      </h2>
      <ul className="explain-list">
        {endpoints.map((endpoint) => (
          <li key={endpoint.id}>
            <div className="explain-static">
              <span className="explain-name">
                <span className="explain-method">{endpoint.method}</span>
                {endpoint.path}
              </span>
              <span className="explain-meta">
                {endpoint.file_path?.split("/").pop()}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Which tests reach this file — and the honest version of "none".
 *
 *  The empty case is the whole reason this section exists, so it renders
 *  rather than disappearing. It says what was actually checked (no test file
 *  imports this) instead of the stronger thing a reader might hear
 *  ("untested"), because an import graph cannot see a test that exercises
 *  code without importing it.
 */
function Coverage({
  tests,
  isTestFile,
  onOpen,
}: {
  tests: TestFile[];
  isTestFile: boolean;
  onOpen: (id: string) => void;
}) {
  if (isTestFile) return null; // "is this test tested?" is not a question

  return (
    <section className="explain-section">
      <h2>
        Tested by{" "}
        <span className="explain-counts">
          {tests.length === 0 ? "nothing imports this in a test" : `${tests.length} test file${tests.length === 1 ? "" : "s"}`}
        </span>
      </h2>
      {tests.length === 0 ? (
        <p className="explain-empty">
          No test file imports this. That is not proof it is untested — a test
          can exercise code without importing it — but nothing here reaches it.
        </p>
      ) : (
        <ul className="explain-list">
          {tests.map((test) => (
            <li key={test.id}>
              <button onClick={() => onOpen(test.id)}>
                <span className="explain-name">{test.name}</span>
                <span className="explain-meta">
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

/** Who knows this code. The finding is concentration, not identity. */
function Ownership({ ownership }: { ownership: OwnershipFacts | null }) {
  if (!ownership) return null;

  return (
    <section className="explain-section">
      <h2>
        Who works on this{" "}
        <span className="explain-counts">
          {ownership.bus_factor_one
            ? "one person, effectively"
            : `${ownership.authors.length} contributor${ownership.authors.length === 1 ? "" : "s"}`}
        </span>
      </h2>
      <ul className="explain-list">
        {ownership.authors.map((author) => (
          <li key={author.name}>
            <div className="explain-static">
              <span className="explain-name">
                {ownership.bus_factor_one && author.name === ownership.primary && (
                  <span className="explain-flag">bus factor 1</span>
                )}
                {author.name}
              </span>
              <span className="explain-meta">
                {Math.round(author.share * 100)}% of commits
              </span>
            </div>
          </li>
        ))}
      </ul>
      {ownership.bus_factor_one && (
        <p className="explain-note-inline">
          {ownership.primary} wrote {Math.round(ownership.primary_share * 100)}% of
          the commits here. If that knowledge is only in one head, this is where
          it hurts. Measured over the history that was cloned, not all time.
        </p>
      )}
    </section>
  );
}

/** What history says travels with this file.
 *
 *  The section only appears when there is something to say, and it leads with
 *  the undeclared partners — a file that also imports this one co-changing
 *  with it is unremarkable, while one that does not is the finding. The
 *  wording stays literal about what the evidence is (commits) so nobody reads
 *  a correlation as a call.
 */
function CoChanges({
  partners,
  onOpen,
}: {
  partners: CoChangePartner[];
  onOpen: (id: string) => void;
}) {
  if (partners.length === 0) return null;
  const hiddenCount = partners.filter((p) => p.hidden).length;

  return (
    <section className="explain-section">
      <h2>
        Changes together with{" "}
        <span className="explain-counts">
          {hiddenCount > 0
            ? `${hiddenCount} with no import between them`
            : "all declared in code"}
        </span>
      </h2>
      <ul className="explain-list">
        {partners.map((partner) => (
          <li key={partner.id}>
            <button onClick={() => onOpen(partner.id)}>
              <span className="explain-name">
                {partner.hidden && <span className="explain-flag">hidden</span>}
                {partner.name}
              </span>
              <span className="explain-meta">
                {Math.round(partner.strength * 100)}% of changes
              </span>
            </button>
          </li>
        ))}
      </ul>
      {hiddenCount > 0 && (
        <p className="explain-note-inline">
          Nothing in the code connects the files marked hidden — yet they keep
          changing in the same commits. Usually a shared format, a duplicated
          rule, or an invariant only one person remembers.
        </p>
      )}
    </section>
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
