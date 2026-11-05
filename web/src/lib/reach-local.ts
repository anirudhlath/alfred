import { hhmm } from "./format";
import type { TimelineItem } from "./history";

/**
 * What this browser alone knows about reaching this device, never sent to the
 * house: the iOS dialog's answers, the Doors `Not now` has answered, and whether
 * the gate has had a button tapped. Corrupt or unavailable storage loses them and
 * never the app, so nothing here throws into a tap.
 */

/** The Act rows' cache entry: `usePush` writes it, `useReachActRows` reads it. */
export const REACH_ACTS_KEY = ["reach", "acts"] as const;

const ACTS_STORE = "alfred.reach.acts";
const ANSWERED_DOORS_STORE = "alfred.reach.answeredDoors";
const GATE_ANSWERED_STORE = "alfred.reach.gateAnswered";
const GATE_ANSWERED = "1";

/** How the iOS dialog was answered: allowed and registered, or declined. */
export type ReachOutcome = "on" | "off";

export interface ReachAct {
  at: string;
  outcome: ReachOutcome;
}

// Every field, as with the unsent queue (`useRoom.ts`): `reachActRow` has copy for
// no other outcome, and no time to print from an unreadable stamp.
function isReachAct(entry: unknown): entry is ReachAct {
  if (!entry || typeof entry !== "object") return false;
  const act = entry as Partial<ReachAct>;
  return (
    (act.outcome === "on" || act.outcome === "off") &&
    typeof act.at === "string" &&
    !Number.isNaN(Date.parse(act.at))
  );
}

const isString = (entry: unknown): entry is string => typeof entry === "string";

function readList<T>(store: string, keep: (entry: unknown) => entry is T): T[] {
  try {
    const raw = localStorage.getItem(store);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(keep) : [];
  } catch {
    // Corrupt or unavailable storage loses the list, never the app.
    return [];
  }
}

function write(store: string, value: string): void {
  try {
    localStorage.setItem(store, value);
  } catch {
    // Private mode. What was tapped holds for this run only, in the caller's hands.
  }
}

export function readReachActs(): ReachAct[] {
  return readList(ACTS_STORE, isReachAct);
}

/** The list including this answer, even if the write failed: this run still shows it. */
export function addReachAct(outcome: ReachOutcome, at: string): ReachAct[] {
  const acts = [...readReachActs(), { at, outcome }];
  write(ACTS_STORE, JSON.stringify(acts));
  return acts;
}

/** An answer as the Room shows it. Hue 255 is the notification stream's. */
export function reachActRow({ at, outcome }: ReachAct): TimelineItem {
  const row = { kind: "act", id: `reach:${outcome}:${at}`, at, hue: 255 } as const;
  return outcome === "on"
    ? {
        ...row,
        text: "Notifications on for this iPhone",
        meta: `${hhmm(at)} · notifications · iOS allowed · important and urgent only`,
      }
    : {
        ...row,
        text: "Notifications stay off on this iPhone",
        meta: `${hhmm(at)} · iOS declined · Settings › Alfred › Notifications to change · not asked again`,
      };
}

export function readAnsweredDoors(): string[] {
  return readList(ANSWERED_DOORS_STORE, isString);
}

export function writeAnsweredDoors(ids: string[]): void {
  write(ANSWERED_DOORS_STORE, JSON.stringify(ids));
}

export function gateAnswered(): boolean {
  try {
    return localStorage.getItem(GATE_ANSWERED_STORE) === GATE_ANSWERED;
  } catch {
    return false;
  }
}

export function markGateAnswered(): void {
  write(GATE_ANSWERED_STORE, GATE_ANSWERED);
}
