import { hhmm } from "@/lib/format";
import type { Mood } from "@/lib/types";

export interface AlfredRowProps {
  text: string;
  at: string;
  mood?: Mood;
  actions: string[];
  error?: boolean;
}

/**
 * No bubble — Alfred's voice is the page, one size above yours. Plain text with
 * `pre-line`, never markdown (decision 3), and a meta line naming the mood, the
 * tools he ran and when. The speaker is visible only to a screen reader — the
 * missing bubble says it to eyes.
 */
export function AlfredRow({ text, at, mood, actions, error }: AlfredRowProps) {
  // An error frame has no mood and ran no tools; `neutral · no tools` would be
  // a small lie about a failure.
  const meta = error
    ? `error · ${hhmm(at)}`
    : [mood ?? "neutral", actions.length > 0 ? actions.join(", ") : "no tools", hhmm(at)].join(
        " · ",
      );

  return (
    <div className="flex max-w-[330px] flex-col gap-[7px]">
      <div
        className="t-alfred whitespace-pre-line"
        style={{ color: error ? "var(--fg2)" : "var(--fg)" }}
      >
        <span className="sr-only">Alfred: </span>
        {text}
      </div>
      <div className="t-meta">{meta}</div>
    </div>
  );
}
