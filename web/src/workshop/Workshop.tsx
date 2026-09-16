import { memo, useEffect, useId, useRef, useState } from "react";
import { hhmm } from "@/lib/format";
import type { StreamRef } from "@/lib/streams";
import { rateText } from "@/lib/format";
import { useOverview } from "@/room/useOverview";
import { Layer } from "@/shell/Layer";
import { ActivityBench } from "./ActivityBench";
import { BenchSwitcher, benchTabId, type Bench } from "./BenchSwitcher";
import { MemoryBench } from "./MemoryBench";
import { SystemBench } from "./SystemBench";
import { TriggersBench } from "./TriggersBench";
import { useActivity } from "./useActivity";
import { useMemory } from "./useMemory";
import { useSystem } from "./useSystem";
import { useTriggers } from "./useTriggers";

export interface WorkshopProps {
  open: boolean;
  onClose: () => void;
  /** A row asked `Why · causal thread`. The Room opens the sheet. */
  onWhy: (ref: StreamRef) => void;
  /**
   * System › Quiet asked for the Held-back queue. The sheet is the Room's —
   * it is already up there for `DndRow` — so this is a second opener rather
   * than a second sheet.
   */
  onHeld: () => void;
}

/**
 * The Workshop (spec §4.10, §8 phase 2): the second of the two surfaces, under
 * the Room's handle. A `Layer` at the workshop level, so the Why sheet and the
 * Door both paint over it. The panel inside carries every hook, so the feed
 * is subscribed and read only while the layer is mounted — including the
 * 400 ms it takes to leave.
 *
 * `memo`, because it is mounted in the Room and the Room re-renders for things
 * the Workshop cannot see: a chat frame, the 30 s overview poll, the Door's
 * once-a-second `now` while a fuse counts. Without it each of those walks
 * `WorkshopPanel` -> `ActivityBench` -> every mounted `EventRow`, all of them
 * unmemoised, every one re-running `summarise` — 400 of them per stream with
 * nothing solo'd, since `MAX_PER_STREAM` is *per stream* and `mergeRows`
 * merges eight, and no ceiling at all once `↑ older` has been pressed, which
 * is exempt from the cap (`feed.ts`) — and re-attaches the panel's Escape listener. All three props the Room passes are stable, or this would never hit:
 * `setWhy`, and a `useCallback`'d `onClose` and `onHeld`.
 */
export const Workshop = memo(function Workshop({ open, onClose, onWhy, onHeld }: WorkshopProps) {
  return (
    <Layer open={open} label="Workshop" durationMs={400} level="workshop">
      <WorkshopPanel onClose={onClose} onWhy={onWhy} onHeld={onHeld} />
    </Layer>
  );
});

interface WorkshopPanelProps {
  onClose: () => void;
  onWhy: (ref: StreamRef) => void;
  onHeld: () => void;
}

/**
 * A fragment, not a wrapper: `Layer`'s panel is already a `flex flex-col`
 * column, and this gives it two children — the header, at its own height, and
 * the `tabpanel`, taking the rest. The bench inside that panel is a column of
 * its own, which is what `ActivityBench` documents it needs: banner and footer
 * at their heights, its `flex-1` list between them.
 */
function WorkshopPanel({ onClose, onWhy, onHeld }: WorkshopPanelProps) {
  const [bench, setBench] = useState<Bench>("activity");
  const activity = useActivity();
  // Every bench's hook is called here, above the bench that is swapped out, and
  // gated on whether its own bench is the one showing: the reads stop when the
  // reader leaves, and the state does not. That is what lets a sub-tab, a kind
  // filter, a queued drain and a credential save note all survive a trip to
  // another bench — and it is why the benches below are plain views.
  const memory = useMemory(bench === "memory");
  const triggers = useTriggers(bench === "triggers");
  const system = useSystem(bench === "system", onHeld);
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

  // `— ev/s` for an overview unread *or* answering with an empty map, which is
  // Redis down rather than a quiet house. The Room's status line asks the same
  // function, so the two headers cannot disagree about the same reading.
  const rate = rateText(overview.data);

  // §5.2: live is not last-known. Not-live outranks paused; paused outranks the
  // rate. The stamp is the feed's own, `activity.liveAt` — not the connection's
  // `lastTrueAt`, which a chat frame and the 30 s overview poll both set. Those
  // say the *house* is reachable; this sentence is about the telemetry pump.
  // With REST healthy and the pump dead the two disagree, and the header would
  // contradict the bench's own banner sixty pixels below it.
  const state = !activity.live
    ? `last true ${activity.liveAt ? hhmm(activity.liveAt) : "--:--"} · not live`
    : activity.paused
      ? `paused · ${activity.heldCount} new`
      : "live";

  /**
   * One bench, and only one: a `switch` rather than four mounted panels with
   * three of them hidden. The bench nobody is looking at is unmounted, which is
   * what stops `SystemBench`'s two timers — the 1 Hz health stamp and the
   * minute tick its session rows are dated against — from running for the life
   * of the Workshop behind a panel nobody can see. Nothing is lost by it: every
   * piece of state a reader would notice lives in the hooks above, not in the
   * views.
   *
   * A function rather than a lookup object built here, for `memo`'s sake one
   * level up: an object literal in the render body is a fresh object every
   * render, which is exactly the kind of prop the memoisation exists to avoid.
   */
  function currentBench() {
    switch (bench) {
      case "activity":
        return <ActivityBench activity={activity} onWhy={onWhy} />;
      case "memory":
        return <MemoryBench memory={memory} />;
      case "triggers":
        return <TriggersBench triggers={triggers} />;
      case "system":
        return <SystemBench system={system} />;
    }
  }

  return (
    <>
      {/* The handoff's "Header padding 62 16 0" (README §4) is measured from the
          top of a 393x852 iPhone *mock*, and the mock has the status bar drawn
          into it. Reading it as padding inside the app added the status bar a
          second time, which is the gap the phone reported twice — at 62 and
          again at 48.

          `env(safe-area-inset-top)` rather than any literal, because the two
          things the literal would have to guess are exactly what the env knows:
          whether `viewport-fit=cover` put this surface under the status bar
          (inset 59, total 67) or below it (inset 0, total 8). Both land the ink
          21 px under the clock — the `‹ Room` button is a 44 px touch target
          around 18 px of text, so 13 px of its own box sits above the first
          pixel anyone sees. The Room's header keeps its literal on purpose: it
          is a headline in an open field, not a nav bar, and the phone called
          that one right. */}
      <header
        ref={header}
        className="flex flex-col gap-2.5 px-4"
        style={{ paddingTop: "calc(env(safe-area-inset-top, 0px) + 8px)" }}
      >
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
          {/* The one live region that survives a change of bench, which is why
              it is this one. The Activity bench's banner used to speak the
              not-live transition and no longer does: it is unmounted with its
              bench on the other three, so a socket dropping while the reader is
              in Memory would be announced by nothing at all. Its text stays,
              without the region — one sentence, said once.

              The rate is the thing that must not be announced: it moves every
              30 s, and a polite region that re-reads `2.1 ev/s` at every poll
              is noise wearing the voice of news. `aria-hidden` keeps it on
              screen and out of the announcement, so only the *state* — live,
              paused with its count, or not live with its stamp — is what
              changes the region's contents. Each bench keeps its own `Read
              errors` region underneath: that is a different fact, about a read
              that came back refused rather than about the connection. */}
          <span className="t-meta-strong" role="status" aria-live="polite" data-testid="workshop-status">
            {state}
            {activity.live && !activity.paused ? <span aria-hidden="true">{` · ${rate}`}</span> : null}
          </span>
        </div>
        <BenchSwitcher bench={bench} onChange={setBench} idBase={tabsBase} panelId={panelId} />
      </header>
      <div
        role="tabpanel"
        id={panelId}
        aria-labelledby={benchTabId(tabsBase, bench)}
        // The APG makes the panel's tab stop optional when the panel holds
        // something focusable and required when it does not. All four benches
        // hold something — the chips, the sub-tabs, the switch — so this is the
        // optional case, kept because it is the one stop that lands *on* the
        // bench rather than inside it, and because a tab stop that came and
        // went as the reader walked the switcher would be worse than either
        // rule. It costs Activity nothing: the stop sits ahead of the content,
        // so tabbing on continues into `↑ older` and the rows as before.
        tabIndex={0}
        className="flex flex-1 flex-col overflow-hidden"
      >
        {currentBench()}
      </div>
    </>
  );
}
