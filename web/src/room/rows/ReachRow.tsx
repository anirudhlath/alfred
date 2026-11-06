import { hhmm } from "@/lib/format";

export interface ReachRowProps {
  /** The first waiting Door's time, which places the row and stamps its meta (the spec's D10). */
  at: string;
  onShowMe: () => void;
  onNotNow: () => void;
}

/**
 * Alfred saying what the tab can't do, beside a Door that shows it: his voice, as
 * `AlfredRow` draws it, with the handoff's two buttons under it. Both at 44 px.
 * `Show me how` takes the fill of a gate's primary, and `Not now` is outlined.
 */
export function ReachRow({ at, onShowMe, onNotNow }: ReachRowProps) {
  return (
    <div className="flex max-w-[330px] flex-col gap-3">
      <div className="t-alfred" style={{ color: "var(--fg)" }}>
        <span className="sr-only">Alfred: </span>
        That door is waiting on you below. Had this tab been closed, I would have had no way to tell
        you. Add me to the home screen and I can knock when something cannot wait.
      </div>
      <div className="t-meta">notifications · unavailable in Safari · {hhmm(at)}</div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onShowMe}
          className="h-11 rounded-[22px] border-0 px-[18px] text-[14px] font-medium"
          style={{ background: "var(--ink)", color: "var(--paper)" }}
        >
          Show me how
        </button>
        <button
          type="button"
          onClick={onNotNow}
          className="h-11 rounded-[22px] bg-transparent px-3.5 text-[14px] font-medium"
          style={{ border: "1px solid var(--line)", color: "var(--fg)" }}
        >
          Not now
        </button>
      </div>
    </div>
  );
}
