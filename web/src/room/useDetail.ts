import { useCallback, useState } from "react";
import type { StreamRef } from "@/lib/streams";

export interface Detail {
  why: StreamRef | null;
  heldOpen: boolean;
  /**
   * How many presses have opened a detail, the one already open included: what sends
   * a detail beside the app back to its top (`Sheet`'s `press`). Not the ref's
   * identity, because the Room's Act row hands over the same `item.why` every time.
   */
  press: number;
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
   * happily as over the Workshop, and a close arriving from there is unreachable
   * anyway. On the phone the Workshop under the sheet is inert while it is up, and
   * from 760 px the Workshop never closes. The sheet outlives the surface that
   * opened it.
   */
  const [why, setWhy] = useState<StreamRef | null>(null);
  /**
   * The Held-back sheet: its one opener is shared by the DND row and System ›
   * Quiet inside the Workshop. One sheet and one piece of state: the Workshop
   * does not render a second copy, it asks for this one.
   */
  const [heldOpen, setHeldOpen] = useState(false);
  const [press, setPress] = useState(0);

  const openWhy = useCallback((ref: StreamRef) => {
    setHeldOpen(false);
    setWhy(ref);
    setPress((count) => count + 1);
  }, []);
  const openHeld = useCallback(() => {
    setWhy(null);
    setHeldOpen(true);
    setPress((count) => count + 1);
  }, []);
  // A close is not a press: a sheet's leave is still on screen, and should not jump.
  const closeWhy = useCallback(() => setWhy(null), []);
  const closeHeld = useCallback(() => setHeldOpen(false), []);

  return { why, heldOpen, press, openWhy, openHeld, closeWhy, closeHeld };
}
