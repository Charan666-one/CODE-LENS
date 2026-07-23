"use client";

import { useState } from "react";
import { useGraphStore } from "@/lib/store";

/** The landing screen is the trailer (EXPERIENCE.md): black, one line,
 *  one input. No feature grid, no paragraphs. */
export default function HeroInput() {
  const phase = useGraphStore((s) => s.phase);
  const error = useGraphStore((s) => s.error);
  const analyze = useGraphStore((s) => s.analyze);
  const [value, setValue] = useState("");

  if (phase !== "idle") return null;

  const submit = (source: string) => {
    if (source.trim()) void analyze(source.trim());
  };

  return (
    <div className="overlay">
      <div className="hero">
        <h1>Understand software, not files.</h1>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            submit(value);
          }}
        >
          <input
            autoFocus
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="https://github.com/owner/repo"
            spellCheck={false}
          />
          <button type="submit">Understand</button>
        </form>
        <div className="try-row">
          <span>try</span>
          {["psf/requests", "pallets/flask", "pallets/click"].map((repo) => (
            <button key={repo} onClick={() => submit(`https://github.com/${repo}`)}>
              {repo}
            </button>
          ))}
        </div>
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  );
}
