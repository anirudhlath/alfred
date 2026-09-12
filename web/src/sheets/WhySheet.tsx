import { useState } from "react";
import { skipToken, useQuery } from "@tanstack/react-query";
import { errorText } from "@/lib/api";
import { ringFill, STREAM_INFO, STREAMS, streamLabel, summarise, type StreamRef } from "@/lib/streams";
import { CANDIDATE_COUNT, fetchThread, JOIN_WINDOW_MS, nodeMeta, type Thread } from "@/lib/trace";
import { Sheet } from "@/shell/Sheet";

const INTRO =
  "Every link drawn solid is joined by an id the server holds. Dashed means adjacent in time only.";

export interface WhySheetProps {
  /** The row asked about; `null` closes the sheet. */
  anchor: StreamRef | null;
  onClose: () => void;
}

/**
 * How much of the past this thread is drawn from: `searched 8 streams · 100
 * entries each · ±10 min`, and what the read could not reach.
 *
 * `partial` counts the streams that answered but whose page stopped inside the
 * window (`trace.ts`, `isPartial`) — busy enough that causes older than the
 * page's end are simply not here. Said out loud, because "searched 8 streams"
 * on its own reads as "I looked everywhere", which is the exact misreading the
 * data layer went to the trouble of detecting.
 */
function footnote({ searched, partial }: Thread): string {
  const streams =
    searched === STREAMS.length ? `${STREAMS.length} streams` : `${searched} of ${STREAMS.length} streams`;
  const line = `searched ${streams} · ${CANDIDATE_COUNT} entries each · ±${JOIN_WINDOW_MS / 60_000} min`;
  return partial > 0 ? `${line} · ${partial} could not be read back far enough` : line;
}

/**
 * The handoff's "Why Alfred did that": the thread `lib/trace.ts` draws
 * through the eight streams from one row (spec §7). A `Sheet`, so it sits over
 * the Workshop and the Room alike.
 */
export function WhySheet({ anchor, onClose }: WhySheetProps) {
  // The sheet is still on screen for its leave after `anchor` goes null; keep
  // drawing the last thread rather than emptying the column mid-animation.
  // Adjusted during render, as `usePresence` does.
  const [shown, setShown] = useState(anchor);
  if (anchor !== null && anchor !== shown) setShown(anchor);

  const thread = useQuery<Thread>({
    queryKey: ["trace", shown?.stream, shown?.entry.id],
    // Keyed on `shown` and reading `shown`: the key and the row actually
    // fetched cannot disagree, not even on the render pass that re-points
    // `shown` and is then thrown away. `anchor` only gates it — the read
    // happens while open, and the cached thread survives the leave.
    queryFn: anchor !== null && shown !== null ? () => fetchThread(shown) : skipToken,
  });

  const nodes = thread.data?.nodes ?? [];

  return (
    <Sheet open={anchor !== null} title="Why Alfred did that" onClose={onClose}>
      <p className="text-[13.5px] leading-[1.5]" style={{ color: "var(--fg2)" }}>
        {INTRO}
      </p>

      {/* At most one of these is ever mounted — a query is pending or errored,
          never both — so neither needs a name to be told from the other, the
          way `ActivityBench`'s two permanent regions do. `status` and not
          `alert`: a thread that would not read is news, not an interrupt. */}
      {thread.isLoading ? (
        <p role="status" className="t-meta-strong">
          reading {STREAMS.length} streams…
        </p>
      ) : null}

      {thread.isError ? (
        <p role="status" className="t-meta-strong">
          {errorText(thread.error)}
        </p>
      ) : null}

      {thread.data ? (
        <>
          <ol className="m-0 list-none p-0 pt-1.5">
            {nodes.map((node, index) => {
              const next = nodes[index + 1];
              const { mono, hue } = STREAM_INFO[node.stream];
              // A segment touching an adjacent node is dashed; every other is a join.
              const dashed = node.link === "adjacent" || next?.link === "adjacent";
              return (
                <li key={`${node.stream}:${node.entry.id}`} className="grid grid-cols-[22px_1fr] gap-3">
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
          <p className="t-meta-strong">{footnote(thread.data)}</p>
        </>
      ) : null}
    </Sheet>
  );
}
