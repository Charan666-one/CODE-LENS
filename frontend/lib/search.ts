"use client";

import { useEffect, useState } from "react";
import { searchRepo } from "./api";
import { useGraphStore } from "./store";

/** Concept search, as one hook.
 *
 *  The palette had this logic inline, which was fine while ⌘K was the only
 *  door to it. It is no longer: the top bar now carries a visible field, and
 *  two components debouncing the same endpoint in two slightly different ways
 *  is how they drift apart. One request per pause, one shape of result,
 *  cancelled the moment the query moves on.
 */
export interface SearchHit {
  /** The graph's own id — what `goTo` navigates to. */
  id: string;
  name: string;
  path: string;
  /** `file`, `function`, `class`, `module` — decides which level can draw it. */
  kind: string | null;
  line: number | null;
}

/** Below this, every query matches half the repository. */
export const MIN_QUERY = 2;
const DEBOUNCE_MS = 160;

export function useConceptSearch(text: string, active: boolean, top = 10) {
  const snapshotId = useGraphStore((s) => s.snapshotId);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const query = text.trim();
    if (!active || snapshotId === null || query.length < MIN_QUERY) {
      setHits([]);
      setPending(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setPending(true);
    const timer = window.setTimeout(() => {
      void searchRepo(snapshotId, query, top)
        .then((result) => {
          if (cancelled) return;
          setHits(result.ranked.map(toHit));
          setError(null);
        })
        .catch((failure: Error) => {
          if (cancelled) return;
          setHits([]);
          setError(failure.message);
        })
        .finally(() => {
          if (!cancelled) setPending(false);
        });
    }, DEBOUNCE_MS); // one request per pause, not per keystroke

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [text, active, snapshotId, top]);

  return { hits, pending, error };
}

function toHit(entry: { node_id: string; reasons: Record<string, unknown> }): SearchHit {
  const reasons = entry.reasons as {
    name?: string;
    kind?: string | null;
    file_path?: string | null;
    start_line?: number | null;
  };
  return {
    id: entry.node_id,
    // The qualified name is the last resort, not the label: it can be the
    // whole dotted path to a nested method.
    name: reasons.name ?? entry.node_id.split(":").slice(1).join(":"),
    path: reasons.file_path ?? "",
    kind: reasons.kind ?? null,
    line: reasons.start_line ?? null,
  };
}
