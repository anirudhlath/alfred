import {
  fuseLength,
  fusePercent,
  fuseRemaining,
  type ActionPhase,
  type TrackedAction,
} from "@/lib/actions";
import { hhmm, humaniseTool, mmss, rawCall, shortId } from "@/lib/format";
import { DANGER_SECONDS, FuseRing } from "@/door/FuseRing";
import { SlideToConfirm } from "@/door/SlideToConfirm";
import { Layer } from "@/shell/Layer";
import { useLatched } from "@/shell/presence";

/**
 * "The five minutes ran out" reads better than "The 5 minutes ran out", and the
 * fuse is a server setting that may not be 300 s. Spelled out to ten, numeric above.
 */
const MINUTE_WORDS: Record<number, string> = {
  1: "one",
  2: "two",
  3: "three",
  4: "four",
  5: "five",
  6: "six",
  7: "seven",
  8: "eight",
  9: "nine",
  10: "ten",
};

/** "five minutes", "one minute" — or just "time" for a fuse under half a minute. */
function fuseWords(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return "time";
  if (minutes === 1) return "one minute";
  return `${MINUTE_WORDS[minutes] ?? String(minutes)} minutes`;
}

// No `pending` entry: that phase draws the slider, not a pill, and the type
// keeps the lookup inside the branch that knows it.
const PILL: Record<Exclude<ActionPhase, "pending">, { word: string; dot: string }> = {
  queued: { word: "Confirmed · queued", dot: "var(--accent)" },
  applied: { word: "Applied", dot: "var(--green)" },
  expired: { word: "Expired", dot: "var(--paper-muted)" },
  answered: { word: "Answered", dot: "var(--paper-muted)" },
};

function subLabel(tracked: TrackedAction): string {
  if (tracked.phase === "pending") return "until it lapses";
  if (tracked.phase === "expired") return "lapsed";
  if (tracked.phase === "answered") return "answered elsewhere";
  return tracked.confirmedAt ? `confirmed ${hhmm(tracked.confirmedAt)}` : "confirmed";
}

function footLine(tracked: TrackedAction, online: boolean): string {
  const { action, phase } = tracked;
  switch (phase) {
    case "pending":
      return online
        ? "Approval only; the lock itself reports back separately. Releasing before the end snaps back."
        : "Cannot confirm while offline; the fuse is still running on the server.";
    case "queued":
      return `Sent to Home Assistant. Waiting for it to report (request ${shortId(action.request_id)}).`;
    case "applied": {
      // The result event is the only road to `applied` and it sets both; if
      // either were ever missing, say less rather than make a status up.
      const what = tracked.result ? `reported ${tracked.result.status}` : "reported back";
      const when = tracked.appliedAt ? ` at ${hhmm(tracked.appliedAt)}` : "";
      return `${action.tool_name} ${what}${when}.`;
    }
    case "expired":
      return `The ${fuseWords(fuseLength(action))} ran out at ${hhmm(
        action.expires_at,
      )}. Nothing was done. Ask again to get a fresh one.`;
    default:
      return "Already answered elsewhere; the house no longer holds this request. Nothing further was sent.";
  }
}

export interface DoorLayerProps {
  tracked: TrackedAction | null;
  open: boolean;
  online: boolean;
  /** Epoch ms from `useDoor().now`. */
  now: number;
  onClose: () => void;
  onConfirm: (id: string) => void;
}

export function DoorLayer({ tracked, open, online, now, onClose, onConfirm }: DoorLayerProps) {
  // Hold the last action through the 420 ms leave animation: `close()` clears
  // the current one immediately, and an empty ink panel sliding away is worse
  // than the one you just dismissed sliding away.
  const item = useLatched(tracked);
  if (!item) return null;

  const { action, phase } = item;
  const remaining = fuseRemaining(action, now);
  const percent = fusePercent(action, now);
  const reason =
    action.reason ?? `Alfred wants to run '${action.tool_name}' on ${action.target_service}.`;

  return (
    <Layer open={open} label="Critical approval" durationMs={420}>
      <div
        className="flex flex-1 flex-col overflow-hidden"
        style={{ background: "var(--ink)", color: "var(--paper)" }}
      >
        <div className="flex items-center justify-between px-6 pt-16">
          <button
            type="button"
            onClick={onClose}
            className="-ml-1 flex h-11 items-center gap-1.5 border-0 bg-transparent px-1 text-[15px] font-normal"
            style={{ color: "var(--paper-muted)" }}
          >
            <span
              aria-hidden="true"
              className="block h-2.5 w-2.5 border-b-[1.5px] border-l-[1.5px] border-current"
              style={{ transform: "rotate(45deg)" }}
            />
            Leave it
          </button>
          <div className="font-mono text-[11px]" style={{ color: "var(--paper-muted)" }}>
            CRITICAL · {shortId(action.request_id)}
          </div>
        </div>

        <div className="flex flex-1 flex-col items-center justify-center gap-7 px-6 text-center">
          <FuseRing percent={percent} size={168} danger={remaining <= DANGER_SECONDS}>
            <div className="flex flex-col items-center gap-0.5">
              <div className="t-fuse">{mmss(remaining)}</div>
              <div className="font-mono text-[11px]" style={{ color: "var(--paper-muted)" }}>
                {subLabel(item)}
              </div>
            </div>
          </FuseRing>

          <div className="flex flex-col items-center gap-2.5">
            <h2 className="t-gate">{humaniseTool(action.tool_name)}</h2>
            <p
              className="max-w-[300px] text-[15px] leading-[1.45]"
              style={{ color: "var(--paper-muted)" }}
            >
              {reason}
            </p>
            <div
              className="rounded-lg border px-3 py-2 font-mono text-[11.5px] leading-[1.5] break-all"
              style={{ borderColor: "var(--ring)", color: "var(--paper-muted)" }}
            >
              {rawCall(action.tool_name, action.parameters)}
            </div>
          </div>
        </div>

        <div
          className="flex flex-col gap-3 px-5"
          // The screen's own edge: the slider must clear the home indicator (§4).
          style={{ paddingBottom: "calc(40px + env(safe-area-inset-bottom, 0px))" }}
        >
          {phase === "pending" ? (
            <SlideToConfirm
              hint="Slide to confirm"
              disabled={!online}
              onConfirm={() => onConfirm(action.request_id)}
            />
          ) : (
            <div
              className="flex h-16 items-center justify-center gap-2.5 rounded-[32px] border-[1.5px] text-[16px] font-medium"
              style={{ borderColor: "var(--ring)" }}
            >
              <span
                aria-hidden="true"
                className="h-2 w-2 rounded-full"
                style={{ background: PILL[phase].dot }}
              />
              {PILL[phase].word}
            </div>
          )}

          <div
            className="text-center font-mono text-[11px] leading-[1.5]"
            style={{ color: "var(--paper-muted)" }}
          >
            {footLine(item, online)}
          </div>

          {phase === "pending" ? null : (
            <button
              type="button"
              onClick={onClose}
              className="h-[50px] rounded-[25px] border-0 bg-transparent text-[15px] font-medium"
              style={{ color: "var(--paper)" }}
            >
              Back to the room
            </button>
          )}
        </div>
      </div>
    </Layer>
  );
}
