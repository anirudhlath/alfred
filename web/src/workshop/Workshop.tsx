import { memo, useEffect, useId, useRef, useState } from "react";
import { hhmm, rateText } from "@/lib/format";
import type { StreamRef } from "@/lib/streams";
import { useOverview } from "@/room/useOverview";
import { ErrorBoundary } from "@/shell/ErrorBoundary";
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
 * is exempt from the cap (`feed.ts`). All three props the Room passes are
 * stable, or this would never hit: `setWhy`, and a `useCallback`'d `onClose`
 * and `onHeld`.
 *
 * It stops the Room's re-renders and only those. The panel re-renders on its
 * own at telemetry rate whatever this does, because `useActivity` lives inside
 * it and every frame changes the feed — which is why the expensive half of
 * that hook is gated on the bench rather than left to `memo` to catch
 * (`useActivity.ts`).
 */
export const Workshop = memo(function Workshop({ open, onClose, onWhy, onHeld }: WorkshopProps) {
  return (
    <Layer open={open} label="Workshop" durationMs={400} level="workshop">
      {/* The boundary is here rather than around the app, so a bench that
          throws costs the reader this layer and not the Room underneath it —
          and it is inside `Layer`, so closing the Workshop unmounts it and the
          next open starts clean. One bad body from one of System's five reads
          used to take Activity, Memory and Triggers down with it, because all
          four hooks live in the one panel below (`lib/system.ts`,
          `fetchIntegrations`). */}
      <ErrorBoundary fallback={<BenchFailed onClose={onClose} />}>
        <WorkshopPanel onClose={onClose} onWhy={onWhy} onHeld={onHeld} />
      </ErrorBoundary>
    </Layer>
  );
});

/**
 * What the Workshop shows when a bench throws. Deliberately short on diagnosis:
 * nothing here knows which bench failed or why, and a sentence that guessed
 * would be the §5.2 failure in a new place.
 *
 * `alert` and not `status`, against this app's habit, for the one reason that
 * distinguishes them: everything else announced in polite is news arriving
 * behind the reader, and this happened under their hands — the screen they were
 * reading has just gone. It is also the only thing left on the layer, so there
 * is nothing for it to interrupt.
 */
function BenchFailed({ onClose }: { onClose: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-1 flex-col items-center justify-center gap-2.5 px-8 text-center"
    >
      <p className="t-body m-0">This bench stopped working.</p>
      <p className="t-meta-strong m-0">
        Nothing on the Room is affected. Opening the Workshop again starts it over.
      </p>
      {/* `--accent-text` on the layer's `--bg`, the header's Room button's own
          pair (index.css, --accent-text). Same name as that button, because it
          goes to the same place. */}
      <button type="button" onClick={onClose} className="t-row min-h-11" style={{ color: "var(--accent-text)" }}>
        Room
      </button>
    </div>
  );
}

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
  const activity = useActivity(bench === "activity");
  // All four hooks are called here, above the bench that is swapped out, each
  // gated on whether its own bench is the one showing: the reads stop when the
  // reader leaves, and the state does not. That is what lets a sub-tab, a kind
  // filter, a queued drain and a credential save note all survive a trip to
  // another bench — and it is why the benches below are plain views.
  //
  // Activity's gate is the one that is not all-or-nothing: its socket stays
  // subscribed and its frames keep landing whatever bench is up, because the
  // header above speaks for the feed on all four. `useActivity.ts` says which
  // half stops.
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
      ? "paused"
      : "live";

  // What follows the state word on screen, and is never announced: a rate that
  // moves every 30 s and a held count that moves with every frame — at the
  // fixture's 2.1 ev/s, twice a second. Both are numbers a reader watches; the
  // state is the only part of this line that is news. Not live has neither: a
  // rate beside a dead pump would be the §5.2 contradiction the ladder above
  // exists to prevent, and a hold that is not holding has nothing to count.
  const detail = !activity.live ? null : activity.paused ? `${activity.heldCount} new` : rate;

  /**
   * One bench, and only one: a `switch` rather than four mounted panels with
   * three of them hidden. The bench nobody is looking at is unmounted, which is
   * what stops `SystemBench`'s two timers — the 1 Hz health stamp and the
   * minute tick its session rows are dated against — from running for the life
   * of the Workshop behind a panel nobody can see.
   *
   * What survives that unmount is everything in the hooks above: the sub-tab,
   * the kind filter, the open row, a queued drain, a credential save note.
   * What does not, deliberately, is state the view owns because it should not
   * outlive the surface it was typed into — a half-typed credential
   * (`IntegrationRow.tsx`), a confirmation prompt waiting for a second tap
   * (`SystemBench.tsx`), an unfolded semantic card (`MemoryBench.tsx`). Task 9
   * chose that for the credential form and this keeps it: a secret that
   * outlives its form is the thing that decision exists to prevent. The
   * scroll position goes with them, which is a cost rather than a choice —
   * measured and backlogged as item 15 of the phase 3 plan, which task 11
   * files as `docs/backlog/low/pwa-phase3-followups.md`.
   *
   * A `switch` rather than a lookup object because it is the shape TypeScript
   * checks: `Bench` is a closed union of four, and a fifth member would fail to
   * compile in the `never` arm. That arm is what makes it true — without it the
   * function would simply widen to `Element | undefined`, which is a valid
   * `ReactNode`, and the new bench would render as a blank panel with nothing
   * to say so.
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
      default: {
        // A fifth bench fails here at compile time instead of rendering a blank
        // panel — `room/Timeline.tsx` guards its item kinds the same way.
        const exhaustive: never = bench;
        return exhaustive;
      }
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

              Only the state word is inside it. The numbers are a *sibling*
              rather than an `aria-hidden` child: `role="status"` implies
              `aria-atomic="true"`, so any mutation inside the region
              re-presents the whole of it, and a reader who heard `live` every
              time the rate ticked would have traded one kind of noise for
              another. Outside the region there is nothing to diff. Each bench
              keeps its own `Read errors` region underneath: that is a
              different fact, about a read that came back refused rather than
              about the connection. */}
          <span className="t-meta-strong" data-testid="workshop-status">
            <span role="status" aria-live="polite" data-testid="workshop-state">
              {state}
            </span>
            {detail !== null && <span data-testid="workshop-detail">{` · ${detail}`}</span>}
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
        // optional case, kept because a tab stop that came and went as the
        // reader walked the switcher would be worse than either rule. It costs
        // Activity nothing: the stop sits ahead of the content, so tabbing on
        // continues into `↑ older` and the rows as before. On Memory it is the
        // first of two, the bench's own sub-tab panel keeping a stop of its own
        // (`MemoryBench.tsx`) — two stops before the content, which the device
        // checklist is the place to judge.
        tabIndex={0}
        className="flex flex-1 flex-col overflow-hidden"
      >
        {currentBench()}
      </div>
    </>
  );
}
