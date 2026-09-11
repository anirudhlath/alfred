import type { FeedRow } from "@/lib/feed";
import { hhmmss } from "@/lib/format";
import { idMs, ring, STREAM_INFO, summarise } from "@/lib/streams";

export interface EventRowProps {
  row: FeedRow;
  expanded: boolean;
  onToggle: () => void;
  /** `Only {mono}` — narrow the list to this row's stream. */
  onSolo: () => void;
  /** `Why · causal thread` — offered on reflex observations only. */
  onWhy?: () => void;
}

const PILL =
  "h-9 rounded-[18px] border border-line bg-transparent px-3.5 text-[13px] font-medium";

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
 */
export function EventRow({ row, expanded, onToggle, onSolo, onWhy }: EventRowProps) {
  const { mono, hue } = STREAM_INFO[row.stream];
  const { text, meta } = summarise(row.stream, row.entry.event);

  return (
    <li style={{ borderTop: "1px solid var(--line)" }}>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="grid min-h-11 w-full grid-cols-[22px_1fr] gap-2.5 py-[9px] text-left"
        style={{ color: "var(--fg)" }}
      >
        <span
          data-testid="monogram"
          className="t-monogram mt-px h-[22px] w-[22px] rounded-md text-center"
          style={{ background: ring(hue), color: "#fff" }}
        >
          {mono}
        </span>
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate text-[14px] leading-[1.35]">{text}</span>
          <span className="t-meta truncate">{`${stamp(row.entry.id)} · ${meta}`}</span>
        </span>
      </button>
      {expanded && (
        <div className="mb-3 ml-8 flex flex-col gap-2">
          <pre
            className="m-0 rounded-lg px-3 py-2.5 font-mono text-[11px] leading-[1.55] whitespace-pre-wrap break-all"
            style={{ background: "var(--surface)", color: "var(--fg2)" }}
          >
            {JSON.stringify(row.entry.event, null, 2)}
          </pre>
          <div className="flex gap-2">
            {onWhy && (
              <button type="button" onClick={onWhy} className={PILL} style={{ color: "var(--accent)" }}>
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
