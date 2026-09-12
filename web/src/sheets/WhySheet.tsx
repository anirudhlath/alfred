import { skipToken, useQuery } from "@tanstack/react-query";
import { errorText } from "@/lib/api";
import {
  ringFill,
  rowKey,
  STREAM_INFO,
  STREAMS,
  streamLabel,
  summarise,
  type StreamRef,
} from "@/lib/streams";
import { CANDIDATE_COUNT, fetchThread, JOIN_WINDOW_MS, nodeMeta, type Thread } from "@/lib/trace";
import { useLatched } from "@/shell/presence";
import { Sheet } from "@/shell/Sheet";

const INTRO =
  "Every link drawn solid is joined by an id the server holds. Dashed means adjacent in time only.";
/**
 * A thread of one means nothing the anchor's ids reached was inside the read.
 * Not the ordinary case for a passive reflex observation: every bus event
 * carries an `event_id` and `trigger_event` is the originating event's full
 * dump, so `tokens()` yields that event's id and the `home_state` row it came
 * from normally joins (`trace.ts`, `tokens`). A lone anchor is that row having
 * aged past its stream's hundred entries, or a schema carrying no id anything
 * else in the window shares. Without this line the sheet promises solid and
 * dashed links, then shows neither, and says nothing about why.
 */
const ALONE = "Nothing else in the eight streams is joined to this row.";

export interface WhySheetProps {
  /** The row asked about; `null` closes the sheet. */
  anchor: StreamRef | null;
  onClose: () => void;
}

/**
 * How much of the past this thread is drawn from: `searched 8 streams · 100
 * entries each · ±10 min`, and what the read could not reach.
 *
 * Two different shortfalls, and they must not be confused with each other.
 * `searched` is short of eight when a stream's read *failed* — nothing of it
 * is here at all. `partial` counts the streams that answered but whose page
 * stopped inside the window (`trace.ts`, `isPartial`) — busy enough that
 * causes older than the page's end are simply not here. Both are said out
 * loud, because "searched 8 streams" on its own reads as "I looked
 * everywhere", which is the exact misreading the data layer went to the
 * trouble of detecting; and `7 of 8` on its own said that one stream was
 * missing without ever saying why, while the only other "could not be read" on
 * the screen meant the other thing entirely. The word `stream` is repeated in
 * the suffix rather than left implied: a bare `1` beside `100 entries each`
 * reads as a count of entries.
 */
function footnote({ searched, partial }: Thread): string {
  const unread = STREAMS.length - searched;
  const streams =
    unread === 0
      ? `${STREAMS.length} streams`
      : `${searched} of ${STREAMS.length} streams (${unread} could not be read)`;
  const line = `searched ${streams} · ${CANDIDATE_COUNT} entries each · ±${JOIN_WINDOW_MS / 60_000} min`;
  if (partial === 0) return line;
  return `${line} · ${partial} stream${partial === 1 ? "" : "s"} could not be read back far enough`;
}

/**
 * The handoff's "Why Alfred did that": the thread `lib/trace.ts` draws
 * through the eight streams from one row (spec §7). A `Sheet`, so it sits over
 * the Workshop and the Room alike.
 */
export function WhySheet({ anchor, onClose }: WhySheetProps) {
  // The sheet is still on screen for its leave after `anchor` goes null; keep
  // drawing the last thread rather than emptying the column mid-animation.
  const shown = useLatched(anchor);

  const thread = useQuery<Thread>({
    queryKey: ["trace", shown?.stream, shown?.entry.id],
    // Keyed on `shown` and reading `shown`: the key and the row actually
    // fetched cannot disagree, not even on the render pass that re-points
    // `shown` and is then thrown away. `anchor` only gates it — the read
    // happens while open, and the cached thread survives the leave.
    queryFn: anchor !== null && shown !== null ? () => fetchThread(shown) : skipToken,
    // The app's default is 10 s with a refetch on focus (`QueryProvider.tsx`),
    // tuned for the Room's live vitals. A thread is not live: it is a picture
    // of one fixed ±10 min window, and by the time that much has passed the
    // window has closed behind it and nothing can join it any more. Without
    // this, backgrounding the phone re-reads eight streams for the same
    // unchanged answer. A constant, not a deadline off `Date.now()`, which
    // would make the render impure.
    staleTime: JOIN_WINDOW_MS,
  });

  const nodes = thread.data?.nodes ?? [];
  // One region for the sheet's life, contents changing: VoiceOver can miss a
  // region inserted with its text already in it, and a thread that would not
  // read must not fail silently (`room/OfflineNote.tsx` documents the same
  // pattern for the same reason). Being one region, it also needs no name to
  // be told from a second. `status`, not `alert`: news, not an interrupt.
  const note = thread.isLoading
    ? `reading ${STREAMS.length} streams…`
    : thread.isError
      ? errorText(thread.error)
      : "";

  return (
    <Sheet open={anchor !== null} title="Why Alfred did that" onClose={onClose}>
      <p className="text-[13.5px] leading-[1.5]" style={{ color: "var(--fg2)" }}>
        {INTRO}
      </p>

      {/* Empty, it is `sr-only` — out of flow, so the sheet's `gap-3` does not
          open around a box with nothing in it. */}
      <p role="status" className={note ? "t-meta-strong" : "sr-only"}>
        {note}
      </p>

      {thread.data ? (
        <>
          {/* An explicit `role="list"`: preflight and `list-none` strip
              list-style, and WebKit strips the list semantics with it, so
              VoiceOver would read the chain as loose text with no item
              positions (`gates/StepList.tsx`). */}
          <ol role="list" className="m-0 list-none p-0 pt-1.5">
            {nodes.map((node, index) => {
              const next = nodes[index + 1];
              const { mono, hue } = STREAM_INFO[node.stream];
              // A segment touching an adjacent node is dashed; every other is a join.
              const dashed = node.link === "adjacent" || next?.link === "adjacent";
              return (
                <li key={rowKey(node)} className="grid grid-cols-[22px_1fr] gap-3">
                  <div className="flex flex-col items-center">
                    {/* `ringFill`, not `ring`: white on L 0.62 is 3.45:1
                        (streams.ts, ringFill). The tile is a picture of the
                        stream, so the name is said in words below. */}
                    <span
                      aria-hidden="true"
                      className="t-monogram h-[22px] w-[22px] shrink-0 rounded-md text-center"
                      style={{ background: ringFill(hue), color: "#fff" }}
                    >
                      {mono}
                    </span>
                    {next ? (
                      // Solid or dashed is a picture of the link too; the meta
                      // line below says which in words ("joined by …" against
                      // "adjacent in time only").
                      <span
                        aria-hidden="true"
                        data-testid="connector"
                        data-dashed={dashed}
                        className="my-1 min-h-[22px] w-0 flex-1"
                        style={{ borderLeft: `1.5px ${dashed ? "dashed" : "solid"} var(--line)` }}
                      />
                    ) : null}
                  </div>
                  <div className="flex min-w-0 flex-col gap-0.5 pb-3.5">
                    <span className="sr-only">{`${streamLabel(node.stream)}, `}</span>
                    <div className="t-row">{summarise(node.stream, node.entry.event).text}</div>
                    {/* `t-meta-strong`, not `t-meta`: this line is the whole
                        evidence for the link drawn beside it, and --muted's
                        3.46:1 is not a thing to read it at (index.css). */}
                    <div className="t-meta-strong break-words">{nodeMeta(node)}</div>
                  </div>
                </li>
              );
            })}
          </ol>
          {/* `--fg2`, not the `--muted` its sibling line in `HeldBackSheet`
              uses: 3.46:1 is under AA at this size too. */}
          {nodes.length === 1 ? (
            <div className="t-row" style={{ color: "var(--fg2)" }}>
              {ALONE}
            </div>
          ) : null}
          <p className="t-meta-strong">{footnote(thread.data)}</p>
        </>
      ) : null}
    </Sheet>
  );
}
