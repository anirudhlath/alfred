import { useId } from "react";
import type { FeedRow } from "@/lib/feed";
import { hhmmss } from "@/lib/format";
import { idMs, ringFill, STREAM_INFO, streamLabel, summarise } from "@/lib/streams";

export interface EventRowProps {
  row: FeedRow;
  expanded: boolean;
  onToggle: () => void;
  /** `Only {mono}` — narrow the list to this row's stream. */
  onSolo: () => void;
  /** `Why · causal thread` — offered on reflex observations only. */
  onWhy?: () => void;
}

/**
 * 36 px of pill, 44 px of target: the `after` box adds 4 px above and below,
 * which the row's own `gap-2`/`mb-3` already holds clear of everything else.
 */
const PILL =
  "relative h-9 rounded-[18px] border border-line bg-transparent px-3.5 text-[13px] font-medium after:absolute after:inset-x-0 after:-inset-y-1 after:content-['']";

/** `idMs` is 0 for an id it cannot read, and 0 ms is a real instant — a 1970 clock. Say so instead. */
function stamp(id: string): string {
  const ms = idMs(id);
  return ms === 0 ? "--:--:--" : hhmmss(ms);
}

/**
 * One event on one stream (handoff, Activity list). The monogram tile carries
 * the stream's ring; the stamp is the Redis id's clock, to the second, because
 * the bench is for telling two events a hundred milliseconds apart in order.
 * Open, the row shows the whole event as the server holds it — no field is
 * hidden or renamed (spec §5.2).
 *
 * The tile is a picture of the stream, so it is `aria-hidden` and the name is
 * said in words beside it. Unmemoised, and the bench renders up to 3 200 of
 * these — `MAX_PER_STREAM` is 400 per stream and the merged list is eight —
 * because its handlers are per-row closures, so a shallow compare would miss on
 * every render and cost more than it saved. What keeps that affordable is that
 * the bench only renders when something it owns changes — `Workshop` is
 * `memo`ised precisely so the Room's unrelated re-renders stop above it.
 */
export function EventRow({ row, expanded, onToggle, onSolo, onWhy }: EventRowProps) {
  const { mono, hue } = STREAM_INFO[row.stream];
  const { text, meta } = summarise(row.stream, row.entry.event);
  const panelId = useId();

  return (
    <li className="shrink-0" style={{ borderTop: "1px solid var(--line)" }}>
      <button
        type="button"
        aria-expanded={expanded}
        // Only while open: `aria-controls` pointing at an absent id is invalid.
        aria-controls={expanded ? panelId : undefined}
        onClick={onToggle}
        className="grid min-h-11 w-full grid-cols-[22px_1fr] gap-2.5 py-[9px] text-left"
        style={{ color: "var(--fg)" }}
      >
        <span
          aria-hidden="true"
          data-testid="monogram"
          className="t-monogram mt-px h-[22px] w-[22px] rounded-md text-center"
          style={{ background: ringFill(hue), color: "#fff" }}
        >
          {mono}
        </span>
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="sr-only">{`${streamLabel(row.stream)}, `}</span>
          <span className="truncate text-[14px] leading-[1.35]">{text}</span>
          <span className="t-meta truncate">{`${stamp(row.entry.id)} · ${meta}`}</span>
        </span>
      </button>
      {expanded && (
        <div id={panelId} className="mb-3 ml-8 flex flex-col gap-2">
          <pre
            className="rounded-lg px-3 py-2.5 font-mono text-[11px] leading-[1.55] whitespace-pre-wrap break-words"
            style={{ background: "var(--surface)", color: "var(--fg2)" }}
          >
            {JSON.stringify(row.entry.event, null, 2)}
          </pre>
          <div className="flex gap-2">
            {onWhy && (
              // `--accent-text` rather than the raw accent, which is 2.34:1 on
              // paper (index.css, --accent-text).
              <button
                type="button"
                onClick={onWhy}
                className={PILL}
                style={{ color: "var(--accent-text)" }}
              >
                Why · causal thread
              </button>
            )}
            <button type="button" onClick={onSolo} className={PILL} style={{ color: "var(--fg)" }}>
              Only {mono}
            </button>
          </div>
        </div>
      )}
    </li>
  );
}
