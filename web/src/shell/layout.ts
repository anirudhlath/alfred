import { createContext, useContext, useSyncExternalStore } from "react";

export type Layout = "phone" | "sheet-over-bench" | "columns";

// The breakpoints are the design spec's phase 6 row and handoff §11
// (docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md,
// docs/design/2026-09-04-pwa-client-handoff/README.md).
const SHEET_OVER_BENCH_QUERY = "(min-width: 760px)";
const COLUMNS_QUERY = "(min-width: 1100px)";

// Both breakpoints also ask for this much screen, because a phone on its side is
// 852 px wide and under 450 px tall, and it stays the phone (R4). The screen and
// not the viewport: `interactive-widget=resizes-content` (index.html) shrinks the
// viewport for the keyboard, so a viewport height would flip a landscape tablet to
// the phone, which unmounts the focused field, which closes the keyboard, which
// flips it back.
const MIN_SCREEN_SHORT_SIDE_PX = 500;

export function readLayout(): Layout {
  if (typeof window.matchMedia !== "function") return "phone";
  if (Math.min(window.screen.width, window.screen.height) < MIN_SCREEN_SHORT_SIDE_PX) {
    return "phone";
  }
  if (window.matchMedia(COLUMNS_QUERY).matches) return "columns";
  if (window.matchMedia(SHEET_OVER_BENCH_QUERY).matches) return "sheet-over-bench";
  return "phone";
}

function subscribeLayout(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") return () => {};
  const queries = [SHEET_OVER_BENCH_QUERY, COLUMNS_QUERY].map((query) => window.matchMedia(query));
  for (const query of queries) query.addEventListener("change", onChange);
  window.addEventListener("resize", onChange);
  return () => {
    for (const query of queries) query.removeEventListener("change", onChange);
    window.removeEventListener("resize", onChange);
  };
}

/**
 * `readLayout()`, kept current. Both queries are watched because a browser fires
 * `change` only on the one whose answer flipped: crossing 1100 px says nothing on
 * the 760 px query, and the reverse. `resize` is watched because the screen has no
 * change event, and a foldable changes its short side with no query flipping: an
 * 882×344 cover screen unfolding to 841×673 stays inside the 760 to 1100 px band.
 * A resize that leaves the layout alone renders nothing, since the snapshot is the
 * same string.
 */
export function useViewportLayout(): Layout {
  return useSyncExternalStore(subscribeLayout, readLayout);
}

/**
 * The layout the Room is in. Only `Room` provides it, so the default of `phone`
 * carries R5: a story in a wide frame and every test that renders no Room stay
 * phone whatever the viewport says. The Door, sheets and benches are all inside
 * the Room, so they read the same answer it does.
 */
export const LayoutContext = createContext<Layout>("phone");

export function useLayout(): Layout {
  return useContext(LayoutContext);
}
