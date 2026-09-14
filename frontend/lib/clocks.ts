"use client";

import { useEffect } from "react";
import { useGraphStore } from "./store";

/** The two clocks that drive the choreography, in one place.
 *
 *  The reveal advances one assembly step at a time and the ripple one
 *  distance ring at a time. Both are the *pace* of a story the store is
 *  telling, not a property of any canvas, so they belong to whichever
 *  renderer happens to be mounted rather than to a particular one — and
 *  keeping one copy means the flat and deep views cannot end up telling it at
 *  different speeds.
 */
export function useChoreographyClocks(): void {
  const phase = useGraphStore((s) => s.phase);
  const rippleFor = useGraphStore((s) => s.rippleFor);
  const advanceReveal = useGraphStore((s) => s.advanceReveal);
  const advanceRipple = useGraphStore((s) => s.advanceRipple);

  // The wave expands one distance ring at a time.
  useEffect(() => {
    if (!rippleFor) return;
    const timer = window.setInterval(advanceRipple, 320);
    return () => window.clearInterval(timer);
  }, [rippleFor, advanceRipple]);

  useEffect(() => {
    if (phase !== "revealing") return;
    const timer = window.setInterval(advanceReveal, 90);
    return () => window.clearInterval(timer);
  }, [phase, advanceReveal]);
}
