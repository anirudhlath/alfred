/**
 * A bench's edges at width (handoff §11): 24 px in from the column, and at most the
 * switcher's 520 px of content, so every bench starts and ends under it. The mock
 * draws them only on Activity. On the phone each bench keeps its own 16 px gutter.
 */

/** A block that does not scroll: the gutter as its margin, and the cap on the block. */
export const DESK_EDGE = "mx-6 max-w-[520px]";

/**
 * A scroller, which stays the column's full width so its bar is at the column's edge
 * and the wheel turns it from anywhere in the column. The cap is its padding instead:
 * 24 px on the left, and on the right whatever is past 520 px of content, 24 px at the
 * least. A padding's percentage is of the column, not of the scroller, so the content
 * is where a `DESK_EDGE` block's is at every width. A scrollbar that takes room takes
 * it from the content, as it did when the cap was on the scroller.
 *
 * Padding rather than a wrapper inside: Activity's and Triggers' scrollers are lists,
 * which hold only their rows, and an expanded Activity row reaches 12 px past the
 * content on either side (`EventRow`), which a cap on each row would take back.
 */
export const DESK_SCROLL_EDGE = "pl-6 pr-[max(24px,100%_-_544px)]";
