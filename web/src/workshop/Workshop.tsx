import { useEffect, useId, useRef, useState } from "react";
import { evs, hhmm } from "@/lib/format";
import type { StreamRef } from "@/lib/streams";
import { useOverview } from "@/room/useOverview";
import { Layer } from "@/shell/Layer";
import { ActivityBench } from "./ActivityBench";
import { BenchSwitcher, benchTabId, type Bench } from "./BenchSwitcher";
import { useActivity } from "./useActivity";

/** What the three phase-3 benches say for now. Phase 3 deletes it, and the branch that reads it. */
const UNBUILT = "not built yet · phase 3";

export interface WorkshopProps {
  open: boolean;
  onClose: () => void;
  /** A row asked `Why · causal thread`. The Room opens the sheet. */
  onWhy: (ref: StreamRef) => void;
}

/**
 * The Workshop (spec §4.10, §8 phase 2): the second of the two surfaces, under
 * the Room's handle. A `Layer` at the workshop level, so the Why sheet and the
 * Door both paint over it. The panel inside carries every hook, so the feed
 * is subscribed and read only while the layer is mounted — including the
 * 400 ms it takes to leave.
 */
export function Workshop({ open, onClose, onWhy }: WorkshopProps) {
  return (
    <Layer open={open} label="Workshop" durationMs={400} level="workshop">
      <WorkshopPanel onClose={onClose} onWhy={onWhy} />
    </Layer>
  );
}

interface WorkshopPanelProps {
  onClose: () => void;
  onWhy: (ref: StreamRef) => void;
}

/**
 * A fragment, not a wrapper: `Layer`'s panel is already a `flex flex-col`
 * column, and this gives it two children — the header, at its own height, and
 * the `tabpanel`, taking the rest. The bench inside that panel is a column of
 * its own, which is what `ActivityBench` documents it needs: banner and footer
 * at their heights, its `flex-1` list between them.
 */
function WorkshopPanel({ onClose, onWhy }: WorkshopPanelProps) {
  const [bench, setBench] = useState<Bench>("activity");
  const activity = useActivity();
  const overview = useOverview();
  const tabsBase = useId();
  const panelId = useId();

  // Escape closes it — constraint §4.12, a standalone app has no browser chrome
  // to escape with, which is why `Sheet.tsx` carries the same handler. Scoped
  // to this panel rather than to the document, so a surface *over* the Workshop
  // keeps its own Escape: a sheet or a gate portals to `document.body`, a
  // sibling of this dialog, so its keys never bubble through here. `Layer` owns
  // the panel node and does not hand it out, so this finds it from the inside —
  // the dialog is the root of everything the layer renders.
  const header = useRef<HTMLElement>(null);
  useEffect(() => {
    const panel = header.current?.closest<HTMLElement>('[role="dialog"]');
    if (!panel) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    panel.addEventListener("keydown", onKeyDown);
    return () => panel.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // An overview never read is not a silent house: `evs({})` is a bare `0`
  // (format.ts), which would claim a quiet feed on every cold open and on every
  // failed poll. `StatusLine` says `—` for its own unknowns; so does this.
  const rate = overview.data ? `${evs(overview.data.streams)} ev/s` : "— ev/s";

  // §5.2: live is not last-known. Not-live outranks paused; paused outranks the
  // rate. The stamp is the feed's own, `activity.liveAt` — not the connection's
  // `lastTrueAt`, which a chat frame and the 30 s overview poll both set. Those
  // say the *house* is reachable; this sentence is about the telemetry pump.
  // With REST healthy and the pump dead the two disagree, and the header would
  // contradict the bench's own banner sixty pixels below it.
  const status = !activity.live
    ? `last true ${activity.liveAt ? hhmm(activity.liveAt) : "--:--"} · not live`
    : activity.paused
      ? `paused · ${activity.heldCount} new`
      : `live · ${rate}`;

  return (
    <>
      {/* 62 px of top padding is the handoff's status-bar clearance (README §4,
          "Header padding 62 16 0"). A literal rather than
          `env(safe-area-inset-top)`: index.html asks for `viewport-fit=cover`,
          but neither this header nor the Room's reads the inset, and one
          surface guessing differently from the other would step the two apart.
          Phase 3 moves both or neither. */}
      <header ref={header} className="flex flex-col gap-2.5 px-4 pt-[62px]">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={onClose}
            className="-ml-2 flex h-11 items-center gap-1.5 px-2 text-[15px] font-medium"
            // `--accent-text`, not `--accent`: the raw accent is 2.34:1 on
            // paper's `--bg` at 15 px (index.css, --accent-text).
            style={{ color: "var(--accent-text)" }}
          >
            <span
              aria-hidden="true"
              className="h-[9px] w-[9px] rotate-45"
              style={{ borderLeft: "1.5px solid currentColor", borderBottom: "1.5px solid currentColor" }}
            />
            Room
          </button>
          {/* Deliberately not a live region. Everything it says is announced
              somewhere better already: the bench's "Feed status" banner speaks
              the not-live transition, the Pause/Resume button's own name
              carries the held count, and a rate that moves every 30 s in a
              polite region is noise rather than news.
              Phase 3: on Memory, Triggers and System that banner is unmounted
              with the bench, so nothing announces a socket drop. The live
              region should move up here when those three land. */}
          <span
            className="t-meta"
            data-testid="workshop-status"
            // `t-meta` is `--muted`, 3.46:1 on `--bg` in light — the trap
            // BenchSwitcher's own comment names. These are the header's only
            // words besides the back button.
            style={{ color: "var(--fg2)" }}
          >
            {status}
          </span>
        </div>
        <BenchSwitcher bench={bench} onChange={setBench} idBase={tabsBase} panelId={panelId} />
      </header>
      <div
        role="tabpanel"
        id={panelId}
        aria-labelledby={benchTabId(tabsBase, bench)}
        // The panel is the scroll container's parent, not itself focusable
        // content, but the pattern asks for a tab stop so a keyboard reaches
        // the bench without walking every row of it.
        tabIndex={0}
        className="flex flex-1 flex-col overflow-hidden"
      >
        {bench === "activity" ? (
          <ActivityBench activity={activity} onWhy={onWhy} />
        ) : (
          <p
            className="t-meta flex flex-1 items-center justify-center"
            // `t-meta` again: see the status line above.
            style={{ color: "var(--fg2)" }}
          >
            {UNBUILT}
          </p>
        )}
      </div>
    </>
  );
}
