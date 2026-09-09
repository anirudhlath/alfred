import { api } from "./api";
import { dayLabel, hhmm, humaniseTool, notificationText, shortId } from "./format";
import type { Mood, StreamEntry, StreamPage } from "./types";

/** One row of the Room. Every kind carries an ISO `at`; the list is sorted by it. */
export type TimelineItem =
  | { kind: "divider"; id: string; at: string; label: string }
  | { kind: "you"; id: string; at: string; text: string; state: "sent" | "unsent" }
  | {
      kind: "alfred";
      id: string;
      at: string;
      text: string;
      mood?: Mood;
      actions: string[];
      error?: boolean;
    }
  /** hue 120 trigger (EV) · 210 reflex (RX) · 255 notification (NT) — the handoff's stream hues. */
  | { kind: "act"; id: string; at: string; hue: 120 | 210 | 255; text: string; meta: string }
  | { kind: "tombstone"; id: string; at: string; title: string; meta: string }
  | { kind: "transcribing"; id: string; at: string; seconds: number }
  | { kind: "thinking"; id: string; at: string; detail: string };

/** The four streams the Room reads. Anything else belongs to the Workshop. */
export const ROOM_STREAMS = [
  "user_requests",
  "user_responses",
  "reflex_observations",
  "notifications",
] as const;

export type RoomStream = (typeof ROOM_STREAMS)[number];

export type RoomHistory = Record<RoomStream, StreamEntry[]>;

const HISTORY_COUNT = 50;
/**
 * The server's chat session idles out after `SESSION_TIMEOUT_MINUTES`
 * (`shared/config.py`, default 30). This is the fallback until the overview has
 * reported the real value (`Overview.session.idle_minutes`); the gap divider, the
 * Room's window and the session-id rotation all use the same number.
 */
export const SESSION_IDLE_MS = 30 * 60 * 1000;

function emptyHistory(): RoomHistory {
  return {
    user_requests: [],
    user_responses: [],
    reflex_observations: [],
    notifications: [],
  };
}

/**
 * Read the Room's history: four pages of fifty, in parallel.
 *
 * `allSettled`, not `all`: one stream that 503s because Redis is briefly gone
 * should cost the user that stream, not the whole conversation. A missing stream
 * is an empty list, which renders as a thread with a hole in it rather than a
 * blank screen with an error.
 *
 * That holds when all four are down too: the result is four empty lists, never a
 * rejection, so `useRoomHistory().isError` is never true and an empty thread is
 * indistinguishable here from a dark house. Deliberate — the Room does not
 * announce outages; the status line and the offline note do, from the overview
 * read (which does reject) and the socket.
 */
export async function fetchRoomHistory(): Promise<RoomHistory> {
  const settled = await Promise.allSettled(
    ROOM_STREAMS.map((name) =>
      api<StreamPage>(`/api/admin/streams/${name}?count=${HISTORY_COUNT}`),
    ),
  );

  const history = emptyHistory();
  settled.forEach((result, index) => {
    if (result.status === "fulfilled") history[ROOM_STREAMS[index]] = result.value.entries ?? [];
  });
  return history;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function youItem(entry: StreamEntry): TimelineItem | null {
  const at = str(entry.event.timestamp);
  const text = str(entry.event.content);
  if (!at || !text) return null;
  return { kind: "you", id: `you:${entry.id}`, at, text, state: "sent" };
}

function alfredItem(entry: StreamEntry): TimelineItem | null {
  const at = str(entry.event.timestamp);
  const text = str(entry.event.text);
  if (!at || !text) return null;
  const actions = Array.isArray(entry.event.actions_taken)
    ? entry.event.actions_taken.filter((a): a is string => typeof a === "string")
    : [];
  const mood = str(entry.event.mood);
  return {
    kind: "alfred",
    id: `alfred:${entry.id}`,
    at,
    text,
    mood: (mood ?? undefined) as Mood | undefined,
    actions,
  };
}

function reflexItem(entry: StreamEntry): TimelineItem | null {
  const at = str(entry.event.timestamp);
  const action = record(entry.event.action);
  // Passive observation: seen, considered, nothing done. Spec §10 — the Room
  // renders only observations that carry an action; the rest are the Workshop's.
  if (!at || !action) return null;
  const tool = str(action.tool_name) ?? "action";
  const result = record(entry.event.result);
  const failed = str(result?.status) === "error";
  return {
    kind: "act",
    id: `rx:${entry.id}`,
    at,
    hue: 210,
    text: str(entry.event.decision_context) ?? tool,
    meta: `${hhmm(at)} · reflex · ${tool}${failed ? " · failed" : ""}`,
  };
}

function notificationItem(entry: StreamEntry): TimelineItem | null {
  const at = str(entry.event.timestamp);
  const text = notificationText(entry.event.body, entry.event.title);
  if (!at || !text) return null;
  // A confirmation request is the Door's, and rendering it here as well would
  // show the same decision twice, one of them without a fuse.
  if (str(record(entry.event.metadata)?.pending_action_id)) return null;
  const source = str(entry.event.source) ?? "house";
  const urgency = str(entry.event.urgency) ?? "informational";
  return {
    kind: "act",
    id: `nt:${entry.id}`,
    at,
    hue: 255,
    text,
    meta: `${hhmm(at)} · ${source} · ${urgency}`,
  };
}

/** Four stream pages → one chronological thread. Unreadable entries are skipped. */
export function toTimelineItems(history: RoomHistory): TimelineItem[] {
  const items: TimelineItem[] = [];
  const add = (item: TimelineItem | null) => {
    if (item) items.push(item);
  };

  for (const entry of history.user_requests) add(youItem(entry));
  for (const entry of history.user_responses) add(alfredItem(entry));
  for (const entry of history.reflex_observations) add(reflexItem(entry));
  for (const entry of history.notifications) add(notificationItem(entry));

  return items.sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/**
 * The current session: the turns since the last silence of `idleMs` or more,
 * walked back from the newest turn — which is what Alfred still has in context.
 * A newest turn that is itself `idleMs` old means no session, and no turns.
 *
 * The house's own rows (notifications, reflex acts) are not conversation; they
 * stay for the day, or from the session's start if that came earlier, so a
 * session that crossed midnight keeps what happened around it. Everything else
 * (tombstones, live rows, the unsent queue) is the caller's, never windowed here.
 *
 * `now` is only the reference for "idle" and "today": a turn stamped after it
 * (the server's clock, a phone a few seconds behind) is current.
 */
export function sessionWindow(
  items: TimelineItem[],
  now: Date,
  idleMs: number = SESSION_IDLE_MS,
): TimelineItem[] {
  let start: number | null = null;
  let next = now.getTime();
  for (const item of items.slice().reverse()) {
    if (item.kind !== "you" && item.kind !== "alfred") continue;
    const at = Date.parse(item.at);
    if (next - at >= idleMs) break;
    start = at;
    next = at;
  }

  const today = startOfDay(now);
  const houseSince = start === null ? today : Math.min(today, start);
  return items.filter((item) => {
    const at = Date.parse(item.at);
    if (item.kind === "you" || item.kind === "alfred") return start !== null && at >= start;
    return at >= houseSince;
  });
}

/**
 * Insert the two kinds of divider the handoff draws: one per day, and one after
 * a half-hour silence between conversational turns.
 *
 * Only `you` and `alfred` rows open a conversation. An autonomous act at 03:00
 * is Alfred talking to the house, not to you, and must not split the thread.
 */
export function withDividers(items: TimelineItem[], now: Date): TimelineItem[] {
  const out: TimelineItem[] = [];
  let lastDay: number | null = null;
  let lastTurnAt: number | null = null;

  for (const item of items) {
    const at = new Date(item.at);
    const day = startOfDay(at);

    if (day !== lastDay) {
      out.push({
        kind: "divider",
        id: `divider:day:${day}`,
        at: item.at,
        label: dayLabel(at, now),
      });
      lastDay = day;
      lastTurnAt = null;
    }

    if (item.kind === "you" || item.kind === "alfred") {
      const stamp = at.getTime();
      if (lastTurnAt !== null && stamp - lastTurnAt >= SESSION_IDLE_MS) {
        out.push({
          kind: "divider",
          id: `divider:gap:${item.id}`,
          at: item.at,
          label: `new conversation · ${hhmm(at)}`,
        });
      }
      lastTurnAt = stamp;
    }

    out.push(item);
  }

  return out;
}

/**
 * `pending_action_id` → a human title, harvested from the notifications page.
 *
 * A notification tap that lands on an action the house has already forgotten has
 * only the id in the URL. This is the one place the client can learn what that
 * id was *about*, so the tombstone can say "Lock unlock" instead of "Action a91f".
 */
export function pendingActionTitles(history: RoomHistory | undefined): Record<string, string> {
  const titles: Record<string, string> = {};
  for (const entry of history?.notifications ?? []) {
    const metadata = record(entry.event.metadata);
    const id = str(metadata?.pending_action_id);
    if (!id) continue;
    const tool = str(metadata?.tool_name);
    titles[id] = tool ? humaniseTool(tool) : (str(entry.event.title) ?? `Action ${shortId(id)}`);
  }
  return titles;
}
