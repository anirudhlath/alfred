import { useCallback, useState } from "react";
import type { StreamRef } from "@/lib/streams";

export interface Detail {
  why: StreamRef | null;
  heldOpen: boolean;
  openWhy: (ref: StreamRef) => void;
  openHeld: () => void;
  closeWhy: () => void;
  closeHeld: () => void;
}

/**
 * Which detail is up: one at a time. Nothing is modal at width, so a `why?` in the
 * Room can be pressed with Held back open, and both would land in the one column.
 * On the phone the sheet makes everything behind it inert, so this never comes up.
 *
 * Every callback is stable, or the memoised Workshop re-renders for every chat
 * frame, overview poll and Door tick the Room re-renders on (`Workshop.tsx`).
 */
export function useDetail(): Detail {
  /**
   * The row a `why?` was asked on — the Room's or the Workshop's. Deliberately
   * not cleared when the Workshop closes: the sheet renders over the Room as
   * happily as over the Workshop, and while it is up the Workshop under it is
   * inert, so a close arriving from there is unreachable anyway. The sheet
   * outlives the surface that opened it.
   */
  const [why, setWhy] = useState<StreamRef | null>(null);
  /**
   * The Held-back sheet: its one opener is shared by the DND row and System ›
   * Quiet inside the Workshop. One sheet and one piece of state: the Workshop
   * does not render a second copy, it asks for this one.
   */
  const [heldOpen, setHeldOpen] = useState(false);

  const openWhy = useCallback((ref: StreamRef) => {
    setHeldOpen(false);
    setWhy(ref);
  }, []);
  const openHeld = useCallback(() => {
    setWhy(null);
    setHeldOpen(true);
  }, []);
  const closeWhy = useCallback(() => setWhy(null), []);
  const closeHeld = useCallback(() => setHeldOpen(false), []);

  return { why, heldOpen, openWhy, openHeld, closeWhy, closeHeld };
}
