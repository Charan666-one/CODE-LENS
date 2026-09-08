"use client";

import { useEffect, useRef, useState } from "react";
import { MIN_QUERY, useConceptSearch } from "@/lib/search";
import { useGraphStore } from "@/lib/store";

/** The search field — the one piece of permanent chrome that earns its space.
 *
 *  Everything else in this product hides behind ⌘K, and that trade works for
 *  *commands*: you go looking for a command already knowing it might exist.
 *  Finding a file does not work that way. Once the graph has a few hundred
 *  nodes, "where is the thing I came here for" is the first question anyone
 *  asks, and answering it with an unlabelled keyboard shortcut means most
 *  readers pan around instead. The field is visible from the moment the graph
 *  exists, and it is the shortest path from a name to a place on the map.
 *
 *  It never opens a results page. A hit moves the camera — changing depth
 *  first if the level on screen cannot draw what was asked for.
 */
const KIND_LABEL: Record<string, string> = {
  file: "file",
  function: "fn",
  class: "class",
  module: "module",
};

export default function SearchBar() {
  const snapshotId = useGraphStore((s) => s.snapshotId);
  const goTo = useGraphStore((s) => s.goTo);
  const paletteOpen = useGraphStore((s) => s.paletteOpen);

  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  /** Bumped by the "/" key. The focusing itself happens in an effect, because
   *  focus opens the list and React restores the caret across that render —
   *  selecting the old query inside the key handler is undone by the commit
   *  that follows it. */
  const [focusRequest, setFocusRequest] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  const { hits, pending, error } = useConceptSearch(query, open, 10);

  useEffect(() => setCursor(0), [hits]);

  // `/` is the search key everywhere text is searched; nothing else on this
  // canvas claims it. Guarded the same way the canvas guards 1/2/3 — anything
  // with its own text cursor owns the keystroke.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const state = useGraphStore.getState();
      if (state.paletteOpen || state.guideOpen) return;
      const target = event.target as HTMLElement | null;
      if (
        target?.isContentEditable ||
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement
      ) {
        return;
      }
      event.preventDefault(); // otherwise the "/" lands in the field
      setFocusRequest((count) => count + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (focusRequest === 0) return;
    const input = inputRef.current;
    if (!input) return;
    input.focus();
    // The last query is still sitting there, so select it: the next keystroke
    // replaces it. Pressing "/" is a new question, not an edit to the old one.
    input.select();
  }, [focusRequest]);

  // Clicking the graph should dismiss the list, not leave it hovering over
  // whatever was just clicked.
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  if (snapshotId === null) return null;

  const short = query.trim().length < MIN_QUERY;
  const listOpen = open && !paletteOpen;

  const choose = (index: number) => {
    const hit = hits[index];
    if (!hit) return;
    void goTo(hit);
    // The name stays in the field — the reader may want the next hit down —
    // but the list closes so it is not sitting on top of the arrival, and
    // focus goes back to the canvas where the depth and arrow keys live.
    setOpen(false);
    inputRef.current?.blur();
  };

  return (
    <div className="search" ref={rootRef}>
      <span className="search-icon" aria-hidden>
        ⌕
      </span>
      <input
        ref={inputRef}
        className="search-input"
        type="search"
        role="combobox"
        aria-expanded={listOpen}
        aria-controls="search-results"
        aria-label="Search files and symbols"
        placeholder="Search files, symbols…"
        value={query}
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setCursor((current) => Math.min(current + 1, hits.length - 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setCursor((current) => Math.max(current - 1, 0));
          } else if (event.key === "Enter") {
            event.preventDefault();
            choose(cursor);
          } else if (event.key === "Escape") {
            event.preventDefault();
            // The canvas takes Escape as "let go of what I selected", and it
            // reads that one before it checks whether a text field owns the
            // keystroke. Leaving the selection alone is the point of pressing
            // Escape *here*: it dismisses the search, not the reader's place.
            event.stopPropagation();
            // First Escape puts the list away; a second clears the field.
            if (listOpen) setOpen(false);
            else {
              setQuery("");
              inputRef.current?.blur();
            }
          }
        }}
      />
      {!query && <span className="kbd search-kbd">/</span>}

      {listOpen && (
        <ul className="search-results" id="search-results" role="listbox">
          {short ? (
            <li className="search-note">
              Type at least {MIN_QUERY} characters — a name, or a concept like
              “rate limiting”.
            </li>
          ) : error ? (
            <li className="search-note">{error}</li>
          ) : hits.length === 0 ? (
            <li className="search-note">{pending ? "Searching…" : "Nothing matches."}</li>
          ) : (
            hits.map((hit, index) => (
              <li key={hit.id} role="option" aria-selected={index === cursor}>
                <button
                  className={index === cursor ? "search-row active" : "search-row"}
                  onMouseEnter={() => setCursor(index)}
                  onClick={() => choose(index)}
                >
                  <span className="search-name">{hit.name}</span>
                  {hit.kind && (
                    <span className="search-kind">{KIND_LABEL[hit.kind] ?? hit.kind}</span>
                  )}
                  <span className="search-path">{hit.path}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
