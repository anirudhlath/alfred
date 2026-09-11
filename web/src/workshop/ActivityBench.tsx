import { hhmm } from "@/lib/format";
import { STREAM_INFO, STREAMS, type StreamRef } from "@/lib/streams";
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

// `loaded` can be false with nothing in flight — tapping Pause before the first
// head read lands retires that read — so an unloaded list says "nothing loaded
// yet" rather than showing progress it may never make.
function emptyNote({ solo, loaded }: Activity): string {
  if (solo) {
    const { mono } = STREAM_INFO[solo];
    return loaded ? `${mono} · 0 entries · nothing has been written` : `${mono} · nothing loaded yet`;
  }
  return loaded ? `${STREAMS.length} streams · 0 entries` : `${STREAMS.length} streams · nothing loaded yet`;
}

/**
 * The Activity bench (handoff, Workshop → Activity; spec §8 phase 2). A
 * merged column of the eight streams, newest first; the older button above
 * it names the cursor it will read before; the footer solos, pauses and
 * clears. The banner is the one place the bench speaks about itself: while
 * the socket is down every row is last-known, and it says so (spec §5.2).
 */
export function ActivityBench({ activity, onWhy }: ActivityBenchProps) {
  const a = activity;
  const pauseLabel = a.paused ? (a.heldCount > 0 ? `Resume · ${a.heldCount} new` : "Resume") : "Pause feed";

  return (
    <>
      {!a.live && (
        <div
          role="status"
          className="mx-4 mt-2.5 flex items-center gap-2 rounded-[10px] px-3 py-2 text-[13px] leading-[1.4]"
          style={{ background: "var(--surface)" }}
        >
          <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full" style={{ background: "var(--muted)" }} />
          {staleText(a.liveAt)}
        </div>
      )}
      {a.error && (
        <p role="status" className="t-meta mx-4 mt-2.5">
          {a.error}
        </p>
      )}

      <ul role="list" className="m-0 flex flex-1 list-none flex-col overflow-y-auto px-4 pt-2">
        {a.cursor && (
          <li>
            <button
              type="button"
              disabled={a.fetchingOlder}
              onClick={a.loadOlder}
              className="h-11 w-full font-mono text-[11px]"
              style={{ color: "var(--muted)" }}
            >
              {a.fetchingOlder ? `↑ older · fetching before cursor ${a.cursor}` : `↑ older · before cursor ${a.cursor}`}
            </button>
          </li>
        )}
        {/* Fresh closures per row on every render, by design: `EventRow` is
            deliberately unmemoised, so a shallow compare would never hit. */}
        {a.rows.map((row) => (
          <EventRow
            key={row.key}
            row={row}
            expanded={a.expanded === row.key}
            onToggle={() => a.toggle(row.key)}
            onSolo={() => a.setSolo(row.stream)}
            onWhy={
              row.stream === "reflex_observations"
                ? () => onWhy({ stream: row.stream, entry: row.entry })
                : undefined
            }
          />
        ))}
        {a.rows.length === 0 && (
          <li className="flex flex-col items-center gap-1.5 py-10 text-center">
            <span className="text-[15px]">{a.solo ? "Nothing on this stream yet." : "Nothing on any stream yet."}</span>
            <span className="t-meta">{emptyNote(a)}</span>
          </li>
        )}
        <li aria-hidden="true" className="h-3 shrink-0" />
      </ul>

      <div className="flex flex-col gap-2.5 px-4 pt-2.5" style={{ borderTop: "1px solid var(--line)" }}>
        <StreamChips counts={a.counts} solo={a.solo} onSolo={a.setSolo} />
        <div
          className="flex items-center gap-2.5"
          style={{ paddingBottom: "calc(12px + env(safe-area-inset-bottom, 0px))" }}
        >
          <button
            type="button"
            onClick={a.paused ? a.resume : a.pause}
            className="h-[50px] flex-1 rounded-[25px] border-0 text-[15px] font-medium"
            style={{
              background: a.paused ? "var(--accent)" : "var(--ink)",
              color: a.paused ? "var(--ink)" : "var(--paper)",
            }}
          >
            {pauseLabel}
          </button>
          <button
            type="button"
            disabled={a.solo === null}
            onClick={() => a.setSolo(null)}
            className="h-[50px] rounded-[25px] border border-line bg-transparent px-[18px] text-[14px] font-medium"
            style={{ color: "var(--fg)", opacity: a.solo === null ? 0.4 : 1 }}
          >
            All streams
          </button>
        </div>
      </div>
    </>
  );
}
