import type { Overview, StreamSummary } from "./types";

/** `21:02:11` — the Workshop's row stamp, to the second, on the device's own clock. */
export function hhmmss(value: string | number | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}

/**
 * `21:14` — the device's own clock, which is the only one the user reads. The
 * same string as `hhmmss` without its seconds, so the Room's stamp and the
 * Workshop's can never disagree about the hour; `--:--:--` truncates to `--:--`.
 */
export function hhmm(value: string | number | Date): string {
  return hhmmss(value).slice(0, 5);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * `12 Aug`. Written out rather than `toLocaleDateString("en-GB")`, whose short
 * September became "Sept" in CLDR 42 — the design says `4 Sep`, on every ICU.
 */
export function dayMonth(date: Date): string {
  return `${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

/** `4:12` — a fuse, counted down. Never negative: a lapsed fuse reads `0:00`. */
export function mmss(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/**
 * An ISO stamp that parses, as epoch ms; null for anything that does not.
 *
 * The one copy of a guard `triggers.ts`, `memory.ts` and `system.ts` each grew
 * their own: a stamp is absent (`""`, which the sessions route sends for a field
 * the hash never carried), unparseable, or not a string at all, and all three
 * mean "no time" rather than `Invalid Date`. Handing one to `pastLabel` is not
 * caught downstream — `hhmm` guards NaN, but `dayMonth` prints `NaN undefined`.
 *
 * Non-positive is null for the same reason it is here at all: nothing this app
 * reads was written in 1969, and an epoch-0 stamp is a field never set.
 *
 * There is deliberately no separate guard for the blank string: `Date.parse("")`
 * is `NaN`, so the check below already answers it, and a second one in front
 * would be a branch no input can reach — untestable by construction and read by
 * the next person as though it were load-bearing. The `trim` stays because it
 * feeds the parse: a stamp arriving with a space around it is still a stamp.
 */
export function isoMs(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const parsed = Date.parse(value.trim());
  return Number.isNaN(parsed) || parsed <= 0 ? null : parsed;
}

/**
 * A number a line may print: finite, and nothing else. `null` and `undefined`
 * are absent fields, and `NaN`/`Infinity` are arithmetic that got away — none of
 * the three belongs in a sentence.
 */
export function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** `1.42`. The currency symbol belongs to the sentence around it, not here. */
export function usd(value: number): string {
  return value.toFixed(2);
}

/** `2.1` — the summed five-minute event rate. A silent house reads a bare `0`. */
export function evs(streams: Record<string, StreamSummary>): string {
  const total = Object.values(streams).reduce((sum, stream) => sum + (stream.rate_5m ?? 0), 0);
  return total === 0 ? "0" : total.toFixed(1);
}

/**
 * `2.1 ev/s`, or `— ev/s` when the map is absent or empty. An empty `streams` is
 * Redis down (see `isFirstRun`), and `evs({})` is a bare `0` — the string
 * reserved for a house that really is silent. A first run keeps its keys, each
 * at length 0, and still reads `0 ev/s`.
 *
 * Shared by the Room's status line, the Workshop's header and the System
 * bench's health grid, so the three cannot drift into telling different stories
 * about the same map. Here rather than beside `useOverview` because the health
 * grid derives in `lib/`, which cannot import upward from `room/`.
 */
export function rateText(overview: Overview | undefined): string {
  const streams = overview?.streams;
  return streams && Object.keys(streams).length > 0 ? `${evs(streams)} ev/s` : "— ev/s";
}

/** `a91f` — enough of a request id to match one line against another. */
export function shortId(id: string): string {
  return id.slice(0, 4);
}

/** `home.lock_unlock` → `Lock unlock`. The Door's title. */
export function humaniseTool(toolName: string): string {
  const last = toolName.split(".").pop() ?? "";
  const words = last.replace(/_/g, " ").trim();
  if (!words) return "Action";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** `home.lock_unlock { entity_id: "lock.front_door", action: "unlock" }` — one mono line. */
export function rawCall(toolName: string, params: Record<string, unknown>): string {
  const entries = Object.entries(params).map(([key, value]) => `${key}: ${JSON.stringify(value)}`);
  return entries.length === 0 ? `${toolName} {}` : `${toolName} { ${entries.join(", ")} }`;
}

const DAY_MS = 86_400_000;

const startOfDay = (value: Date): number =>
  new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();

/**
 * Whole days from `now`'s midnight to `date`'s: 0 today, positive ahead, negative
 * behind. One signed count for all three labels below, which otherwise counted in
 * opposite directions a call apart.
 */
const daysAhead = (date: Date, now: Date): number =>
  Math.round((startOfDay(date) - startOfDay(now)) / DAY_MS);

/** Timeline day divider: `earlier today` · `yesterday` · `4 Sep`. */
export function dayLabel(date: Date, now: Date): string {
  const days = daysAhead(date, now);
  if (days >= 0) return "earlier today";
  if (days === -1) return "yesterday";
  return dayMonth(date);
}

/**
 * `08:40 tomorrow` · `23:15` · `08:00 earlier today` · `20:52 yesterday` · `12 Sep`
 * — a stamp that may be ahead of `now`. `dayLabel` alone cannot say so: written
 * for a feed, where everything is behind, it answers "earlier today" for every
 * date in the future.
 *
 * A bare clock means *still ahead, today*, and nothing else. A moment already
 * behind says which day it was, because `runs 08:00` on a one-shot that was due
 * this morning is otherwise indistinguishable from one due tonight.
 */
export function whenLabel(date: Date, now: Date): string {
  const days = daysAhead(date, now);
  if (days === 1) return `${hhmm(date)} tomorrow`;
  if (days > 1) return `${hhmm(date)} ${dayMonth(date)}`;
  if (days === 0 && date.getTime() > now.getTime()) return hhmm(date);
  return `${hhmm(date)} ${dayLabel(date, now)}`;
}

/**
 * `20:52` · `20:52 yesterday` · `20:52 9 Sep` — a stamp the reader already knows
 * is behind them: when a trigger was created, when a session signed in. The day
 * is named only when it is not today, where `whenLabel` would add an "earlier
 * today" that a line already spoken in the past tense does not need.
 *
 * A stamp that is somehow *ahead* — a clock disagreement between the house and
 * the phone — is dated rather than hidden, but never with `whenLabel`'s
 * forward-looking words: `signed in 08:40 tomorrow` is a sentence about
 * something that has not happened, and `tomorrow` belongs to the surfaces that
 * mean it.
 */
export function pastLabel(date: Date, now: Date): string {
  const days = daysAhead(date, now);
  if (days === 0) return hhmm(date);
  if (days > 0) return `${hhmm(date)} ${dayMonth(date)}`;
  return whenLabel(date, now);
}

/**
 * A notification's one line of text: the body, else the title. The body is the
 * message; the title is often only a label ("Routine Suggestion"), and a value
 * of nothing but spaces is no value. `undefined` when it says neither, which
 * the history guard drops the row on.
 *
 * One function, not one rule written down twice, because the Room's live rows
 * and its history rows must land on the same string: `readBackKey` pairs them
 * on it, and derivations that drifted apart would print the notification twice.
 */
export function notificationText(body: unknown, title: unknown): string | undefined {
  const text = (value: unknown) => (typeof value === "string" ? value.trim() : "");
  return text(body) || text(title) || undefined;
}
