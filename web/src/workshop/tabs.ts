import type { KeyboardEvent } from "react";

/**
 * The two things every tab set in the Workshop shares: how a tab's id is spelled,
 * and what the keyboard does to it.
 *
 * There are two of these sets — the bench switcher and the Memory bench's
 * sub-tabs — and they look nothing alike: one is a `--field` segment on a
 * `--surface` track, the other a 44 px pill filled `--ink` on `--paper`
 * (handoff §4 and §6). What they must not differ in is behaviour, which is
 * why the behaviour lives here and only the paint lives in the components.
 */

/**
 * The id of one tab in a set. The `tabpanel` names the tab that labels it and
 * the tab names the panel it controls, so the two ends have to agree on a
 * string: the component owns the base and both sides derive from it.
 */
export function tabId(base: string, id: string): string {
  return `${base}-${id}`;
}

/** Which tab a key moves to in a set of `count`, or null when the key is not ours. */
function target(key: string, index: number, count: number): number | null {
  // Wrapping, so the set is a ring; Home and End are the APG's own additions
  // for a tab list long enough to be worth jumping.
  if (key === "ArrowRight") return (index + 1) % count;
  if (key === "ArrowLeft") return (index - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

/**
 * A tab set's keyboard: automatic activation, which is what a segmented control
 * does and what the APG allows when every panel is cheap to render — the key
 * moves the selection along with the focus.
 *
 * Handed the whole `ids` list rather than an index so the caller cannot get the
 * order wrong in one place and right in the other. Every tab in the set is
 * mounted, so the one to focus exists now, before React has moved the roving
 * `tabIndex` onto it: focusing a `tabIndex={-1}` element from script is legal,
 * and the render that follows fixes the order.
 */
export function tabKeyDown<T extends string>(
  event: KeyboardEvent<HTMLElement>,
  ids: readonly T[],
  current: T,
  onChange: (id: T) => void,
): void {
  const next = target(event.key, ids.indexOf(current), ids.length);
  if (next === null) return;
  // Otherwise the arrow also scrolls whatever is under the control.
  event.preventDefault();
  onChange(ids[next]);
  event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
}
