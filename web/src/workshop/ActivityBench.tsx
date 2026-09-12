import { useLayoutEffect, useRef } from "react";
import { hhmm } from "@/lib/format";
import { STREAM_INFO, STREAMS, type StreamName, type StreamRef } from "@/lib/streams";
import { EventRow } from "./EventRow";
import { StreamChips } from "./StreamChips";
import type { Activity } from "./useActivity";

export interface ActivityBenchProps {
  activity: Activity;
  onWhy: (ref: StreamRef) => void;
}

function staleText(liveAt: number | null): string {
  return liveAt === null
    ? "Feed has not been live yet. Nothing below is live."
    : `Feed stopped at ${hhmm(liveAt)}. Nothing below is live.`;
}

// Three things an empty list can mean, and only the first two used to be said.
//
// `loaded` can be false with nothing in flight — tapping Pause before the first
// head read lands retires that read — so an unloaded list says "nothing loaded
// yet" rather than showing progress it may never make.
//
// And `loaded` goes true when the head read *settles*, however it settled:
// `useActivity` sets it even when all eight streams rejected. So a soloed
// stream is only told it has nothing written when that stream's own page came
// back (`streamLoaded`). Saying "nothing has been written" about a stream this
// bench never read would be a claim about the server made from no evidence,
// which is exactly what spec §5.2 forbids.
function emptyNote({ solo, loaded, streamLoaded }: Pick<Activity, "solo" | "loaded" | "streamLoaded">): string {
  if (solo) {
    const { mono } = STREAM_INFO[solo];
    if (!loaded) return `${mono} · nothing loaded yet`;
    return streamLoaded[solo]
      ? `${mono} · 0 entries · nothing has been written`
      : `${mono} · could not be read`;
  }
  return loaded ? `${STREAMS.length} streams · 0 entries` : `${STREAMS.length} streams · nothing loaded yet`;
}

/**
 * The two streams a row can be asked `why?` from. Both have a cause the server
 * itself holds, which is the bar the plan's decision 4 set: a reflex
 * observation carries the originating event's whole dump, so `trace.ts` joins
 * it by `event_id`, and a reply names the tools it ran in `actions_taken`,
 * which `trace.ts` joins to an action's `tool_name`. A reply is also spec
 * §5.1's own wording for Causality — "correlate one conversation turn with the
 * system activity it caused" — and this bench is where that turn is reachable;
 * the Room's chat bubbles are a handoff visual and stay as they are
 * (`docs/backlog/low/pwa-phase2-followups.md` §11).
 *
 * Everything else is left out for decision 4's reason: notifications and
 * triggers have no server-held cause to join on, and a pill that always
 * produced a dashed-only column would be noise.
 */
const WHY_STREAMS: readonly StreamName[] = ["user_responses", "reflex_observations"];

/**
 * The Activity bench (handoff, Workshop → Activity; spec §8 phase 2). A
 * merged column of the eight streams, newest first; the older button above
 * it names the cursor it will read before; the footer solos, pauses and
 * clears. The banner is the one place the bench speaks about itself: while
 * the socket is down every row is last-known, and it says so (spec §5.2).
 *
 * Rendered into a flex column (Task 7's `Layer`): the banner, the error line
 * and the footer take their own height and the list takes what is left between
 * them. The list needs no `min-h-0` to do that — `overflow-y-auto` already
 * zeroes its automatic minimum size, which is the thing that would otherwise
 * push the footer off the bottom.
 */
export function ActivityBench({ activity, onWhy }: ActivityBenchProps) {
  const pauseLabel = activity.paused
    ? activity.heldCount > 0
      ? `Resume · ${activity.heldCount} new`
      : "Resume"
    : "Pause feed";

  const listRef = useRef<HTMLUListElement>(null);
  const prevHeight = useRef(0);
  const prevTopKey = useRef<string | null>(null);
  const topKey = activity.rows[0]?.key ?? null;

  // Live rows land on *top* of this list, so every one of them slides whatever
  // the reader is looking at down the screen. A browser with `overflow-anchor`
  // would hold the place for us; WebKit has never shipped it, and this is a
  // phone client, so measure the growth and give it back by hand. Keyed on
  // which row is newest rather than on the height, because opening a row grows
  // the list too and that growth is the reader's own doing. A layout effect,
  // like `Timeline.tsx:70-82` at the other end of the app, so the correction
  // lands before paint instead of as a visible jump.
  //
  // `rows.length` and `expanded` are in the deps for the measurement, not for
  // the correction: an `↑ older` page appends below and an opening row unfolds
  // its JSON panel, and neither moves anything — but the taller list each
  // leaves behind is what the *next* prepend must be measured against. Miss
  // one and the following live row hands back its own height plus that one's,
  // which throws the reader past the panel they were reading. Those three are
  // the whole set: nothing else changes this list's height.
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const grew = el.scrollHeight - prevHeight.current;
    const prepended = prevTopKey.current !== null && topKey !== prevTopKey.current;
    // `scrollTop > 0` leaves the reader who is already at the newest row where
    // they are: at the top, watching rows arrive, which is the other thing
    // this list is for.
    if (prepended && grew > 0 && el.scrollTop > 0) el.scrollTop += grew;
    prevHeight.current = el.scrollHeight;
    prevTopKey.current = topKey;
  }, [topKey, activity.rows.length, activity.expanded]);

  // A different stream is a different list, and halfway down one is nowhere in
  // the other. Declared after the anchor so it has the last word on the render
  // where the solo and the rows change together.
  useLayoutEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = 0;
  }, [activity.solo]);

  return (
    <>
      {/* Both live regions stay mounted and only change their contents:
          VoiceOver can miss a region inserted with its text already in it
          (`room/OfflineNote.tsx` documents the same pattern for the same
          socket). Empty, each is `sr-only` — out of flow, so no box opens
          around nothing. The labels keep two polite regions an inch apart
          tellable from one another. `status` and not `alert` for the error:
          a read that failed in the background is news, not an interrupt. */}
      <div
        role="status"
        aria-label="Feed status"
        className={
          activity.live
            ? "sr-only"
            : "mx-4 mt-2.5 flex items-center gap-2 rounded-[10px] px-3 py-2 text-[13px] leading-[1.4]"
        }
        style={activity.live ? undefined : { background: "var(--surface)" }}
      >
        {activity.live ? null : (
          <>
            <span
              aria-hidden="true"
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ background: "var(--muted)" }}
            />
            <span>{staleText(activity.liveAt)}</span>
          </>
        )}
      </div>
      <p
        role="status"
        aria-label="Read errors"
        // `t-meta-strong`, not `t-meta`: this is the line that says part of the
        // list is missing, and --muted's 3.46:1 is not a thing to read it at
        // (index.css, .t-meta-strong).
        className={activity.error ? "t-meta-strong mx-4 mt-2.5" : "sr-only"}
      >
        {activity.error}
      </p>

      <ul ref={listRef} role="list" className="m-0 flex flex-1 list-none flex-col overflow-y-auto px-4 pt-2 pb-3">
        {activity.cursor && (
          <li>
            {/* `aria-disabled`, not `disabled`: a button that disables itself
                under the finger that just pressed it throws focus to `<body>`
                mid-action. `loadOlder` already refuses to re-enter while a page
                is in flight (`useActivity.ts`), same as `SlideToConfirm`. The
                id stays on screen and out of the name — a screen reader would
                otherwise spell fifteen digits on every focus — while "older"
                stays in the name for voice control. */}
            <button
              type="button"
              aria-disabled={activity.fetchingOlder}
              aria-label={activity.fetchingOlder ? "Fetching older entries" : "Load older entries"}
              onClick={activity.loadOlder}
              className="h-11 w-full font-mono text-[11px]"
              style={{ color: "var(--fg2)" }}
            >
              <span aria-hidden="true">
                {activity.fetchingOlder
                  ? `↑ older · fetching before cursor ${activity.cursor}`
                  : `↑ older · before cursor ${activity.cursor}`}
              </span>
            </button>
          </li>
        )}
        {/* Fresh closures per row on every render, by design: `EventRow` is
            deliberately unmemoised, so a shallow compare would never hit. */}
        {activity.rows.map((row) => (
          <EventRow
            key={row.key}
            row={row}
            expanded={activity.expanded === row.key}
            onToggle={() => activity.toggle(row.key)}
            onSolo={() => activity.setSolo(row.stream)}
            onWhy={
              WHY_STREAMS.includes(row.stream)
                ? () => onWhy({ stream: row.stream, entry: row.entry })
                : undefined
            }
          />
        ))}
        {activity.rows.length === 0 && (
          <li className="flex flex-col items-center gap-1.5 py-10 text-center">
            <span className="text-[15px]">
              {activity.solo ? "Nothing on this stream yet." : "Nothing on any stream yet."}
            </span>
            {/* `t-meta-strong` like the error line: this is the only thing
                on screen when the list is empty. */}
            <span className="t-meta-strong">{emptyNote(activity)}</span>
          </li>
        )}
      </ul>

      <div className="flex flex-col gap-2.5 px-4 pt-2.5" style={{ borderTop: "1px solid var(--line)" }}>
        <StreamChips counts={activity.counts} solo={activity.solo} onSolo={activity.setSolo} />
        <div
          className="flex items-center gap-2.5"
          style={{ paddingBottom: "calc(12px + env(safe-area-inset-bottom, 0px))" }}
        >
          <button
            type="button"
            onClick={activity.paused ? activity.resume : activity.pause}
            className="h-[50px] flex-1 rounded-[25px] border-0 text-[15px] font-medium whitespace-nowrap"
            style={{
              background: activity.paused ? "var(--accent)" : "var(--ink)",
              // `--on-accent`, not `--ink`: ink is near-white in the dark theme,
              // which is the first paint, and near-white on the accent is
              // 1.77:1 (index.css, --on-accent).
              color: activity.paused ? "var(--on-accent)" : "var(--paper)",
            }}
          >
            {pauseLabel}
          </button>
          <button
            type="button"
            disabled={activity.solo === null}
            onClick={() => activity.setSolo(null)}
            className="h-[50px] rounded-[25px] border border-line bg-transparent px-[18px] text-[14px] font-medium"
            style={{ color: "var(--fg)", opacity: activity.solo === null ? 0.4 : 1 }}
          >
            All streams
          </button>
        </div>
      </div>
    </>
  );
}
