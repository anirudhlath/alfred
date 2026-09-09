import { hhmm } from "./format";

export interface HeadlineInput {
  online: boolean;
  reconnecting: boolean;
  /** Every catalogued stream is empty — nothing has ever happened in this house. */
  firstRun: boolean;
  dnd: { active: boolean; until?: string | null };
  holding: boolean;
  /** A turn is in flight: sent, no reply yet. */
  busy: boolean;
  /** Local hour, 0–23. Passed in so the function stays pure and testable. */
  hour: number;
}

/** The one table every greeting reads from — the Room's and the first run's alike. */
export function partOfDay(hour: number): "morning" | "afternoon" | "evening" {
  if (hour >= 5 && hour <= 11) return "morning";
  if (hour >= 12 && hour <= 17) return "afternoon";
  return "evening";
}

/** Exported because the empty Room's first-day row must greet the same way. */
export function greetingFor(hour: number): string {
  return `Good ${partOfDay(hour)}, sir.`;
}

/**
 * What Alfred says about himself, in one line.
 *
 * Order matters and is a product decision: connection state outranks everything,
 * because a greeting over a dead socket is a lie; the microphone outranks the
 * conversation, because it is the thing the user is doing right now.
 */
export function pickHeadline(input: HeadlineInput): string {
  if (input.reconnecting) return "Reconnecting…";
  if (!input.online) return "Unreachable.";
  if (input.holding) return "Go on, sir.";
  if (input.busy) return "One moment, sir.";

  if (input.dnd.active) {
    const until = input.dnd.until;
    const stamp = until ? hhmm(until) : "--:--";
    // `hhmm` answers `--:--` for anything it cannot parse. "Quiet until --:--."
    // would be worse than admitting there is no end.
    return stamp === "--:--" ? "Quiet until further notice." : `Quiet until ${stamp}.`;
  }

  if (input.firstRun) return greetingFor(input.hour);
  return "Listening, sir.";
}
