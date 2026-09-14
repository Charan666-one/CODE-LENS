"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useConceptSearch } from "@/lib/search";
import { useGraphStore } from "@/lib/store";

/** ⌘K — where the power lives so the chrome doesn't have to hold it.
 *
 *  A minimal interface is only worth having if the product stays capable, and
 *  this is the trade: nothing permanent on screen, everything one keystroke
 *  away. Fourteen query plans are reachable from here; none of them opens a
 *  page. Every command either transforms the graph or moves the camera.
 */
interface Command {
  id: string;
  label: string;
  hint: string;
  run: () => void;
  /** Needs something selected first. */
  needsSelection?: boolean;
}

export default function CommandPalette() {
  const phase = useGraphStore((s) => s.phase);
  const open = useGraphStore((s) => s.paletteOpen);
  const setPalette = useGraphStore((s) => s.setPalette);
  const setZoom = useGraphStore((s) => s.setZoom);
  const runOverlay = useGraphStore((s) => s.runOverlay);
  const goTo = useGraphStore((s) => s.goTo);
  const showRipple = useGraphStore((s) => s.showRipple);
  const toggleDetail = useGraphStore((s) => s.toggleDetail);
  const selectedId = useGraphStore((s) => s.selectedId);
  const spec = useGraphStore((s) => s.spec);

  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const { hits: matches } = useConceptSearch(query, open, 6);

  const commands = useMemo<Command[]>(() => {
    const graphId =
      spec?.nodes.find((node) => node.id === selectedId)?.explain_id ?? selectedId;
    return [
      {
        id: "cycles",
        label: "Find circular dependencies",
        hint: "isolates each loop on the graph",
        run: () => void runOverlay("cycles", "Circular dependencies"),
      },
      {
        id: "health",
        label: "Architecture health",
        hint: "lights the modules dragging the score",
        run: () => void runOverlay("architecture_health", "Architecture health"),
      },
      {
        id: "risk",
        label: "Riskiest files",
        hint: "complexity × fan-in × churn",
        run: () => void runOverlay("risk", "Riskiest files"),
      },
      {
        id: "untested",
        label: "Untested hubs",
        hint: "no test reaches these, and other code does",
        run: () => void runOverlay("untested_hubs", "Untested hubs"),
      },
      {
        id: "bus",
        label: "Bus factor",
        hint: "important files one person wrote",
        run: () => void runOverlay("bus_factor", "Bus factor"),
      },
      {
        id: "coupling",
        label: "Hidden coupling",
        hint: "change together, but no import between them",
        run: () => void runOverlay("hidden_coupling", "Hidden coupling"),
      },
      {
        id: "central",
        label: "Most connected files",
        hint: "what the project leans on",
        run: () => void runOverlay("centrality", "Most connected"),
      },
      {
        id: "endpoints",
        label: "HTTP endpoints",
        hint: "the surface this service exposes",
        run: () => void runOverlay("endpoints", "Endpoints"),
      },
      {
        id: "entrypoints",
        label: "Entry points",
        hint: "where execution starts",
        run: () => void runOverlay("entrypoints", "Entry points"),
      },
      {
        id: "impact",
        label: "Analyze impact of selection",
        hint: "the blast radius, as a wave",
        needsSelection: true,
        run: () => graphId && void showRipple(graphId),
      },
      {
        id: "explain",
        label: "Explain selection",
        hint: "why the project needs it",
        needsSelection: true,
        run: () => toggleDetail(),
      },
      {
        id: "l1",
        label: "L1 · Architecture",
        hint: "the big picture",
        run: () => void setZoom(1),
      },
      { id: "l2", label: "L2 · Modules", hint: "files and components", run: () => void setZoom(2) },
      { id: "l3", label: "L3 · Symbols", hint: "functions and classes", run: () => void setZoom(3) },
    ];
  }, [runOverlay, setZoom, showRipple, toggleDetail, selectedId, spec]);

  const visible = useMemo(
    () =>
      commands.filter((command) => {
        if (command.needsSelection && !selectedId) return false;
        if (!query.trim()) return true;
        return command.label.toLowerCase().includes(query.trim().toLowerCase());
      }),
    [commands, query, selectedId],
  );

  // ⌘K anywhere; Escape closes. Bound at the window so the palette needs no
  // trigger button taking up permanent space.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette(!useGraphStore.getState().paletteOpen);
      } else if (event.key === "Escape" && useGraphStore.getState().paletteOpen) {
        event.preventDefault();
        setPalette(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPalette]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setCursor(0);
      inputRef.current?.focus();
    }
  }, [open]);

  if (phase !== "exploring" && phase !== "revealing") return null;
  if (!open) return null;

  const rows = [
    ...visible.map((command) => ({ kind: "command" as const, command })),
    ...matches.map((match) => ({ kind: "match" as const, match })),
  ];

  const activate = (index: number) => {
    const row = rows[index];
    if (!row) return;
    if (row.kind === "command") row.command.run();
    else {
      // A search result moves the graph. There is no results page — and
      // `goTo` changes depth when the level on screen cannot draw the hit.
      void goTo(row.match);
    }
  };

  return (
    <div className="palette-scrim" onClick={() => setPalette(false)}>
      <div className="palette" onClick={(event) => event.stopPropagation()}>
        <input
          ref={inputRef}
          className="palette-input"
          placeholder="Search files and symbols, or run a command…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setCursor(0);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setCursor((current) => Math.min(current + 1, rows.length - 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setCursor((current) => Math.max(current - 1, 0));
            } else if (event.key === "Enter") {
              event.preventDefault();
              activate(cursor);
            }
          }}
        />

        <ul className="palette-list">
          {rows.length === 0 && <li className="palette-empty">Nothing matches.</li>}
          {rows.map((row, index) => (
            <li key={row.kind === "command" ? row.command.id : row.match.id}>
              <button
                className={index === cursor ? "palette-row active" : "palette-row"}
                onMouseEnter={() => setCursor(index)}
                onClick={() => activate(index)}
              >
                {row.kind === "command" ? (
                  <>
                    <span className="palette-label">{row.command.label}</span>
                    <span className="palette-hint">{row.command.hint}</span>
                  </>
                ) : (
                  <>
                    <span className="palette-label">{row.match.name}</span>
                    <span className="palette-hint">{row.match.path}</span>
                  </>
                )}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
