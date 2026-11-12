import { useEffect, useRef, type RefObject } from "react";
import { STREAMS } from "@/lib/streams";
import { isEditing } from "@/shell/keys";
import type { Activity } from "./useActivity";

/**
 * The controls inside the bench that keep their own Space. A row would be one, but Enter
 * already presses it, so its Space is the feed's (R12). Outside the bench every Space is
 * the page's, control or not (`useBenchKeys`).
 */
const SPACE_IS_ITS_OWN = 'button, a, [role="tab"], [role="switch"]';

/**
 * J/K's stops: real focus on the row's own button, so Enter needs no code, past dimmed
 * rows (R11). By document position, because focus inside the list need not be on one of
 * those stops: a pill in an open row, or a row a solo has just dimmed. A reader back from
 * a thread keeps their place. From outside the list, the first.
 */
function moveFocus(list: HTMLElement, by: 1 | -1): void {
  const rows = [...list.querySelectorAll<HTMLElement>("[data-event-row]:not([data-dimmed])")];
  const from = document.activeElement;
  if (!from || !list.contains(from)) {
    rows[0]?.focus();
    return;
  }
  const side = by === 1 ? Node.DOCUMENT_POSITION_FOLLOWING : Node.DOCUMENT_POSITION_PRECEDING;
  const onThatSide = (row: HTMLElement) => (from.compareDocumentPosition(row) & side) !== 0;
  (by === 1 ? rows.find(onThatSide) : rows.findLast(onThatSide))?.focus();
}

/**
 * The keys `ActivityBench`'s footnote promises (handoff README §5): J/K move, Enter
 * opens payload, Space pauses, 1–8 solo a stream.
 *
 * `enabled` is the width. Being unmounted on the other three benches is what scopes
 * these to Activity. A Door or a gate makes `#root` inert around the list, and a sheet
 * over the bench makes the bench's body inert, and then none of these are the bench's.
 *
 * Space is the feed's only from inside the Workshop's tabpanel, the narrowest element
 * holding the list, the chips and the bench's own blank space (a press there focuses
 * it). From `<body>`, the Room, the thread column or a sheet it is left to scroll: a
 * reader whose focus is on `<body>` is scrolling what they last clicked, which need not
 * be the feed. That narrows R12's "from nothing". J/K and 1–8 reach the list from
 * anywhere.
 */
export function useBenchKeys(activity: Activity, list: RefObject<HTMLElement | null>, enabled: boolean): void {
  // Through a ref so the listeners stay put: `activity` is a new object on every
  // render, and the Space that pauses is itself what makes one, before its keyup.
  const latest = useRef(activity);
  useEffect(() => {
    latest.current = activity;
  });

  useEffect(() => {
    if (!enabled) return;
    // Whether the Space now down was taken here. Every press sets it afresh, so a keyup
    // that went missing with the window's focus cannot cost the next button its own.
    let spaceTaken = false;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === " ") spaceTaken = false;
      const root = list.current;
      if (!root || root.closest("[inert]")) return;
      if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
      if (isEditing(event.target)) return;

      const key = event.key.toLowerCase();
      if (key === "j" || key === "k") {
        event.preventDefault();
        moveFocus(root, key === "j" ? 1 : -1);
      } else if (key === " ") {
        const bench = root.closest('[role="tabpanel"]') ?? root;
        if (!(event.target instanceof Node) || !bench.contains(event.target)) return;
        const control = event.target instanceof Element ? event.target.closest(SPACE_IS_ITS_OWN) : null;
        if (control && !control.matches("[data-event-row]")) return;
        event.preventDefault();
        spaceTaken = true;
        if (event.repeat) return;
        const { paused, pause, resume } = latest.current;
        if (paused) resume();
        else pause();
      } else if (/^[1-8]$/.test(key)) {
        event.preventDefault();
        if (event.repeat) return;
        const stream = STREAMS[Number(key) - 1];
        const { solo, setSolo } = latest.current;
        setSolo(solo === stream ? null : stream);
      }
    };

    // Firefox clicks a focused button on keyup whatever became of its keydown.
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.key !== " " || !spaceTaken) return;
      spaceTaken = false;
      event.preventDefault();
    };

    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keyup", onKeyUp);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("keyup", onKeyUp);
    };
  }, [enabled, list]);
}
