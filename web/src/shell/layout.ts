import { createContext, useContext, useSyncExternalStore } from "react";

export type Layout = "phone" | "sheet-over-bench" | "columns";

// Both ask for height too: a phone on its side is 852 px wide and under 450 px
// tall, and it stays the phone (R4).
const SHEET_OVER_BENCH_QUERY = "(min-width: 760px) and (min-height: 500px)";
const COLUMNS_QUERY = "(min-width: 1100px) and (min-height: 500px)";

export function readLayout(): Layout {
  if (typeof window.matchMedia !== "function") return "phone";
  if (window.matchMedia(COLUMNS_QUERY).matches) return "columns";
  if (window.matchMedia(SHEET_OVER_BENCH_QUERY).matches) return "sheet-over-bench";
  return "phone";
}

function subscribeLayout(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") return () => {};
  const queries = [SHEET_OVER_BENCH_QUERY, COLUMNS_QUERY].map((query) => window.matchMedia(query));
  for (const query of queries) query.addEventListener("change", onChange);
  return () => {
    for (const query of queries) query.removeEventListener("change", onChange);
  };
}

/**
 * `readLayout()`, kept current. Both queries are watched because a browser fires
 * `change` only on the one whose answer flipped: crossing 1100 px says nothing on
 * the 760 px query, and the reverse.
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
