import type { KeyboardEvent } from "react";

export type Bench = "activity" | "memory" | "triggers" | "system";

const BENCHES: { id: Bench; label: string }[] = [
  { id: "activity", label: "Activity" },
  { id: "memory", label: "Memory" },
  { id: "triggers", label: "Triggers" },
  { id: "system", label: "System" },
];

/**
 * The id of one bench's tab. The `tabpanel` names the tab that labels it and
 * the tab names the panel it controls, so the two ends have to agree on a
 * string: `Workshop` owns the base and both sides derive from it.
 */
// One helper exported beside the component, as `ConnectionProvider.tsx`,
// `DoorProvider.tsx` and `ThemeProvider.tsx` already do: a file of its own for
// one template string would cost more than the fast-refresh boundary it buys.
// eslint-disable-next-line react-refresh/only-export-components
export function benchTabId(base: string, bench: Bench): string {
  return `${base}-${bench}`;
}

export interface BenchSwitcherProps {
  bench: Bench;
  onChange: (bench: Bench) => void;
  /** Prefix for the tabs' own ids — see `benchTabId`. */
  idBase: string;
  /** The `tabpanel` these tabs control. Required: a tablist without one is half a pattern. */
  panelId: string;
}

/**
 * The handoff's four-up segmented control (README §4, Workshop header): a
 * `surface` track, `field` under the chosen bench. The track is 44 px and each
 * segment is 38 × ~85 inside its 3 px padding — under Apple's 44, over WCAG
 * 2.5.8's 24 px minimum, and the handoff's own measurement.
 *
 * A real tab set, not four buttons wearing `role="tab"`: each tab names the
 * panel it controls, only the chosen one is in the tab order, and the arrows
 * walk the four. The panel itself is the Workshop's, since the bench content
 * is what the Workshop renders.
 *
 * The handoff asks for `muted` on the inactive three; this uses `--fg2`
 * instead. `--muted` on `--surface` is 4.53:1 in the dark theme but **3.20:1 in
 * light** — under AA for 13 px text, the same trap Tasks 5 and 6 found on
 * `--bg` (3.46:1). `--fg2` is 9.36:1 dark / 7.66:1 light and still reads a
 * clear step below the chosen bench's `--fg`.
 */
export function BenchSwitcher({ bench, onChange, idBase, panelId }: BenchSwitcherProps) {
  const index = BENCHES.findIndex((entry) => entry.id === bench);

  // Automatic activation — the arrow moves the selection along with the focus,
  // which is what a segmented control does and what the APG allows for a tab
  // set whose panels are all cheap to render. Wraps, so the four are a ring.
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (step === 0) return;
    // Otherwise the arrow also scrolls the bench underneath.
    event.preventDefault();
    const next = (index + step + BENCHES.length) % BENCHES.length;
    onChange(BENCHES[next].id);
    // All four tabs are mounted, so the one to focus exists now — before React
    // has moved the roving `tabIndex` onto it. Focusing a `tabIndex={-1}`
    // element from script is legal; the render that follows fixes the order.
    event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label="Bench"
      onKeyDown={onKeyDown}
      className="grid h-11 grid-cols-4 gap-[3px] rounded-xl p-[3px]"
      style={{ background: "var(--surface)" }}
    >
      {BENCHES.map(({ id, label }) => {
        const active = id === bench;
        return (
          <button
            key={id}
            id={benchTabId(idBase, id)}
            type="button"
            role="tab"
            aria-selected={active}
            aria-controls={panelId}
            // Roving tabindex: one stop for the whole control, then the arrows.
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(id)}
            className="rounded-[9px] border-0 text-[13px] font-medium"
            style={{
              background: active ? "var(--field)" : "transparent",
              color: active ? "var(--fg)" : "var(--fg2)",
            }}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
