import type { StreamSummary } from "./types";

type Ev = Record<string, unknown>;

/** `21:14` — the device's own clock, which is the only one the user reads. */
export function hhmm(value: string | number | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--";
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
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

/** `1.42`. The currency symbol belongs to the sentence around it, not here. */
export function usd(value: number): string {
  return value.toFixed(2);
}

/** `2.1` — the summed five-minute event rate. A silent house reads a bare `0`. */
export function evs(streams: Record<string, StreamSummary>): string {
  const total = Object.values(streams).reduce((sum, stream) => sum + (stream.rate_5m ?? 0), 0);
  return total === 0 ? "0" : total.toFixed(1);
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

/** Timeline day divider: `earlier today` · `yesterday` · `4 Sep`. */
export function dayLabel(date: Date, now: Date): string {
  const startOfDay = (value: Date) =>
    new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (days <= 0) return "earlier today";
  if (days === 1) return "yesterday";
  return dayMonth(date);
}

/** One-line summary of a raw stream event, for feed rows. */
export function summarize(stream: string, event: Ev): string {
  const type = String(event.event_type ?? "");
  if (type === "state_changed") return `${event.entity_id} → ${event.new_state}`;
  if (type === "action_request" || type === "action_result")
    return String(event.tool_name ?? type);
  if (type === "trigger_fired") return `${event.trigger_name} fired`;
  if (type === "user_request") return String(event.content ?? "").slice(0, 60);
  if (type === "alfred_response") return String(event.text ?? "").slice(0, 60);
  if (type === "reflex_observation") {
    const action = event.action as Ev | null | undefined;
    if (action?.tool_name != null) return String(action.tool_name);
    // Passive observation: the Reflex Engine saw this and acted on nothing.
    // Render the transition it saw, or these all read as one identical word.
    const trigger = event.trigger_event as Ev | null | undefined;
    const entity = String(trigger?.entity_id ?? "");
    if (!entity) return "observation";
    // `||` not `??`, matching the Python summary: an empty state reads as unknown.
    return `${entity}: ${trigger?.old_state || "unknown"} → ${trigger?.new_state || "unknown"}`;
  }
  if (stream === "notifications") return String(event.title ?? "notification");
  return type || "event";
}

/** `HH:MM:SS` from a Redis stream id (`<ms>-<seq>`). */
export function timeOf(streamId: string): string {
  const ms = Number(streamId.split("-")[0]);
  return new Date(ms).toLocaleTimeString("en-GB", { hour12: false });
}
