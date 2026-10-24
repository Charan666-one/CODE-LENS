"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { fetchOverview, fetchRepoFacts } from "@/lib/api";
import type { NarratedAnswer, RepoFacts } from "@/lib/types";

/** The project, explained — the one page in this product that is prose.
 *
 *  Everything else here refuses to open a page: a query draws its answer on
 *  the graph, and that rule is what keeps the canvas the interface. This is
 *  the deliberate exception, for the one question the canvas cannot answer.
 *  A graph shows what a codebase *is shaped like*; it has no way to say what
 *  it is *for*. That lives in the README, in prose the parser skips, and a
 *  reader who has never seen the repository needs it before any of the
 *  shapes mean anything.
 *
 *  The snapshot id is in the URL rather than read from the store, so the
 *  page survives a reload and can be sent to someone else — which is most of
 *  the point of having a page at all.
 */
export default function OverviewPage({
  params,
}: {
  params: Promise<{ snapshot: string }>;
}) {
  const { snapshot } = use(params);
  const snapshotId = Number(snapshot);

  const [facts, setFacts] = useState<RepoFacts | null>(null);
  const [answer, setAnswer] = useState<NarratedAnswer | null>(null);
  const [factsError, setFactsError] = useState<string | null>(null);
  const [answerError, setAnswerError] = useState<string | null>(null);

  // The two halves load independently and on purpose. The measurements need
  // no model and must appear even when narration is unavailable; showing an
  // apology with nothing behind it would waste the reader's trip.
  useEffect(() => {
    if (!Number.isFinite(snapshotId)) {
      setFactsError("That is not a snapshot id.");
      return;
    }
    let cancelled = false;
    void fetchRepoFacts(snapshotId)
      .then((result) => !cancelled && setFacts(result))
      .catch((failure: Error) => !cancelled && setFactsError(failure.message));
    void fetchOverview(snapshotId)
      .then((result) => !cancelled && setAnswer(result))
      .catch((failure: Error) => !cancelled && setAnswerError(failure.message));
    return () => {
      cancelled = true;
    };
  }, [snapshotId]);

  const repoName = facts?.repo_url.replace(/^https?:\/\/github\.com\//, "") ?? "";
  const unread = Object.entries(facts?.unsupported_languages ?? {});

  return (
    <main className="overview-page">
      <header className="overview-top">
        <Link href="/" className="overview-back">
          ← the graph
        </Link>
        <span className="overview-repo">{repoName}</span>
      </header>

      <article className="overview-sheet">
        <h1 className="overview-title">{repoName || "This project"}</h1>

        {facts && (
          <>
            <p className="overview-lede">
              {facts.primary_language} · {facts.nodes.toLocaleString()} nodes ·{" "}
              {facts.edges.toLocaleString()} relationships
            </p>

            {/* Coverage, stated before the prose rather than after it. An
                explanation of a system half of which was never read is a
                different document, and the reader has to know which one
                they are holding. */}
            <p className="overview-coverage">
              Read <strong>{facts.parsed_file_count.toLocaleString()}</strong> of{" "}
              {facts.file_count.toLocaleString()} files.
              {unread.length > 0 && (
                <span className="overview-gap">
                  {" "}
                  Not read:{" "}
                  {unread.map(([language, count]) => `${language} (${count})`).join(", ")}{" "}
                  — CodeLens reads Python, JavaScript and TypeScript, so that part of
                  this repository is missing from everything below.
                </span>
              )}
              {!facts.has_readme && (
                <span className="overview-gap">
                  {" "}
                  No README was found, so the explanation below is built from the code
                  alone and cannot say what the project is <em>for</em>.
                </span>
              )}
            </p>
          </>
        )}

        {factsError && <p className="overview-error">{factsError}</p>}

        <hr className="overview-rule" />

        {answer ? (
          <>
            <div className="overview-prose">{renderMarkdown(answer.text)}</div>
            <p className="overview-model">
              Written by {answer.model ?? "a model"} from this repository&rsquo;s README
              and {answer.evidence_ids.length} measured facts about its graph.
            </p>
          </>
        ) : answerError ? (
          <div className="overview-unavailable">
            <h2>No model is configured</h2>
            <p>
              The measurements above are real and came from the graph. The written
              explanation needs a language model, and this instance has none:{" "}
              <code>{answerError}</code>
            </p>
            <p>
              Set <code>NARRATION_ENABLED=true</code> and one key —{" "}
              <code>GROQ_API_KEY</code> or <code>OPENROUTER_API_KEY</code> are free, or
              point <code>OLLAMA_BASE_URL</code> at a local model — then reload.
            </p>
          </div>
        ) : (
          <p className="overview-loading">Reading the repository…</p>
        )}
      </article>
    </main>
  );
}

/** The narrowest markdown renderer that serves this page: `## headings`,
 *  blank-line-separated paragraphs, nothing else.
 *
 *  A library would be the reflex, but the model is asked for exactly four
 *  headings and prose beneath them, so everything a full parser adds here is
 *  surface for a prompt to inject HTML through. The output is plain text in
 *  React elements, which cannot execute.
 */
function renderMarkdown(text: string) {
  const blocks = text.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
  return blocks.map((block, index) => {
    const heading = block.match(/^#{1,3}\s+(.*)$/);
    if (heading) {
      return <h2 key={index}>{heading[1]}</h2>;
    }
    return <p key={index}>{block.replace(/^[-*]\s+/gm, "• ")}</p>;
  });
}
