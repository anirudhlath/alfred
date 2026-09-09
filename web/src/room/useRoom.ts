import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { playWavBase64 } from "@/lib/audio";
import { hhmm } from "@/lib/format";
import { withDividers, type TimelineItem } from "@/lib/history";
import { onVisible } from "@/lib/lifecycle";
import type { ChatServerMessage } from "@/lib/types";
import { useConnection } from "@/shell/ConnectionProvider";

/** Messages typed while the house was unreachable, kept across a cold launch. */
export const UNSENT_KEY = "alfred.unsent";
/** The server's own `publish_and_wait` timeout. Past this, nothing is still coming. */
export const NO_REPLY_MS = 60_000;

const THINKING_ID = "thinking";
const TRANSCRIBING_ID = "transcribing";

// A monotonic counter rather than a random id: stable React keys, and a
// deterministic order in tests. It only has to be unique within one page load.
let seq = 0;
function uid(prefix: string): string {
  seq += 1;
  return `${prefix}:${seq}`;
}

type YouItem = Extract<TimelineItem, { kind: "you" }>;

function isUnsent(item: TimelineItem): item is YouItem {
  return item.kind === "you" && item.state === "unsent";
}

// Every field, not just the kind: a row without its timestamp would put a
// `NaN undefined` day divider at the top of the thread, and one without its
// state would never be retried and never be cleared.
function isPersistedUnsent(item: unknown): item is YouItem {
  if (!item || typeof item !== "object") return false;
  const row = item as Partial<YouItem>;
  return (
    row.kind === "you" &&
    typeof row.id === "string" &&
    typeof row.at === "string" &&
    typeof row.text === "string" &&
    row.state === "unsent"
  );
}

/**
 * What a row would have been read back as — its kind and text, and for an act
 * row its hue too, because a reflex and a notification are both acts and must
 * not answer for each other. Null for a row the house never writes to its
 * streams: an unsent message, a client-made error row, and the transients.
 */
function readBackKey(item: TimelineItem): string | null {
  switch (item.kind) {
    case "you":
      return item.state === "sent" ? `you:${item.text}` : null;
    case "alfred":
      return item.error ? null : `alfred:${item.text}`;
    case "act":
      return `act:${item.hue}:${item.text}`;
    default:
      return null;
  }
}

/**
 * Pair the live rows the house has since read back to us with their copies:
 * live id → history id.
 *
 * The history is re-read whenever the app returns to the foreground, and the
 * server writes every turn to the streams it is read from, so a re-read
 * carries the rows that arrived live since the last one — without this, each
 * trip to the background doubled the recent thread. `unread` is only what the
 * history has gained since the Room opened and has not answered for a live
 * row already, because a live row can only be a copy of a turn from this
 * session; an older "yes" must not swallow a new one. Within that, the
 * read-back key decides, one history row answering for one live row in
 * order, so "yes" twice stays twice. The clocks are never compared: the live
 * row carries the phone's stamp and the history row the server's.
 */
function readBackPairs(live: TimelineItem[], unread: TimelineItem[]): Map<string, string> {
  const pairs = new Map<string, string>();
  const taken = new Set<string>();
  for (const item of live) {
    const key = readBackKey(item);
    if (key === null) continue;
    const copy = unread.find((row) => readBackKey(row) === key && !taken.has(row.id));
    if (!copy) continue;
    taken.add(copy.id);
    pairs.set(item.id, copy.id);
  }
  return pairs;
}

/** What the read-back has settled so far. */
interface ReadBack {
  /** The history it was last settled against, by identity. */
  history: TimelineItem[] | undefined;
  /** The ids of the turns that predate this session; null until the first history. */
  baseline: ReadonlySet<string> | null;
  /** The history rows that have already answered for a live row. */
  answered: ReadonlySet<string>;
}

function readUnsent(): TimelineItem[] {
  try {
    const raw = localStorage.getItem(UNSENT_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isPersistedUnsent) : [];
  } catch {
    // Corrupt or unavailable storage loses the queue, never the app.
    return [];
  }
}

function writeUnsent(items: TimelineItem[]): void {
  try {
    localStorage.setItem(UNSENT_KEY, JSON.stringify(items.filter(isUnsent)));
  } catch {
    // Private mode. The queue survives in memory for this session only.
  }
}

export interface UseRoomOptions {
  /**
   * `toTimelineItems(useRoomHistory().data)` — the thread as it stood on open,
   * and undefined until the first read has answered. The distinction matters:
   * that first answer is the line between the turns that predate this session
   * and the ones the house reads back to us (see `readBackPairs`).
   */
  history?: TimelineItem[];
  /** Expired and already-answered approvals, from `tombstoneItems` (Task 25). */
  tombstones?: TimelineItem[];
}

export interface RoomValue {
  items: TimelineItem[];
  /** A turn is in flight: something was sent and nothing has come back. */
  thinking: boolean;
  sendText: (text: string) => void;
  sendAudio: (dataUrl: string, seconds: number) => void;
}

export function useRoom({ history, tombstones }: UseRoomOptions): RoomValue {
  const { chat, online, subscribeOnline } = useConnection();
  const [live, setLive] = useState<TimelineItem[]>(readUnsent);

  // The read-back is settled the moment a new history arrives — during render
  // rather than in an effect, so the frame that shows the history never shows
  // a turn twice — and settled once per history, not on every render: a live
  // row goes the moment its copy arrives, and the copy is remembered so that
  // it cannot answer for a later turn once the window has rolled past it
  // (the history is only ever the last fifty rows of each stream). The first
  // history to arrive is the thread as it stood before this session, kept as
  // ids: stream ids are the server's and survive every re-read.
  const [readBack, setReadBack] = useState<ReadBack>({
    history: undefined,
    baseline: null,
    answered: new Set(),
  });
  if (history !== readBack.history) {
    const baseline = readBack.baseline ?? (history ? new Set(history.map((item) => item.id)) : null);
    let { answered } = readBack;
    if (history && baseline) {
      const unread = history.filter((row) => !baseline.has(row.id) && !answered.has(row.id));
      const pairs = readBackPairs(live, unread);
      if (pairs.size > 0) {
        answered = new Set([...answered, ...pairs.values()]);
        setLive((current) => current.filter((item) => !pairs.has(item.id)));
      }
    }
    setReadBack({ history, baseline, answered });
  }

  // Read once at mount and again whenever the app comes back to the foreground.
  // Day dividers are relative to it, and a PWA left open across midnight would
  // otherwise still be calling yesterday "earlier today".
  const [now, setNow] = useState(() => new Date());
  useEffect(() => onVisible(() => setNow(new Date())), []);

  useEffect(() => {
    writeUnsent(live);
  }, [live]);

  const sendText = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (!text) return;
      const at = new Date().toISOString();
      const sent = online && chat.sendText(text);
      setLive((current) => {
        // Only a send that left starts a new turn. One that did not leaves the
        // turn already in flight — and its countdown — exactly as it was.
        // Annotated: TS infers a type predicate from the filter and would
        // otherwise refuse to push a thinking row back in.
        const next: TimelineItem[] = sent
          ? current.filter((item) => item.kind !== "thinking")
          : [...current];
        next.push({ kind: "you", id: uid("you"), at, text, state: sent ? "sent" : "unsent" });
        if (sent) next.push({ kind: "thinking", id: THINKING_ID, at, detail: "working" });
        return next;
      });
    },
    [chat, online],
  );

  const sendAudio = useCallback(
    (dataUrl: string, seconds: number) => {
      // Nothing is shown unless the frame actually left: a dashed bubble waiting
      // on a server that never heard the audio would never resolve.
      if (!chat.sendAudio(dataUrl)) return;
      const at = new Date().toISOString();
      setLive((current) => [
        ...current.filter((item) => item.kind !== "transcribing"),
        { kind: "transcribing", id: TRANSCRIBING_ID, at, seconds },
      ]);
    },
    [chat],
  );

  // Retry the queue, in order, the moment the socket says it is online. Stopping
  // at the first refusal keeps the conversation in the order it was written.
  // What went out is a turn in flight like any other, so it gets the thinking
  // row and, through it, the 60 s countdown; a reconnect the server never
  // answers would otherwise show nothing at all.
  // Driven by the socket's own notification rather than the `online` flag: the
  // sends are an external side effect and the rows they settle are recorded in
  // the same callback, so the effect itself only subscribes. The queue is read
  // through a ref so that subscription is made once, not per timeline change.
  const liveRef = useRef(live);
  useEffect(() => {
    liveRef.current = live;
  }, [live]);

  useEffect(
    () =>
      subscribeOnline(() => {
        const delivered = new Set<string>();
        for (const item of liveRef.current.filter(isUnsent)) {
          if (!chat.sendText(item.text)) break;
          delivered.add(item.id);
        }
        if (delivered.size === 0) return;

        const at = new Date().toISOString();
        setLive((current) => {
          const settled: TimelineItem[] = current.map((item) =>
            item.kind === "you" && delivered.has(item.id)
              ? { kind: "you", id: item.id, at: item.at, text: item.text, state: "sent" }
              : item,
          );
          return [
            ...settled.filter((item) => item.kind !== "thinking"),
            { kind: "thinking", id: THINKING_ID, at, detail: "working" },
          ];
        });
      }),
    [subscribeOnline, chat],
  );

  useEffect(() => {
    return chat.listen((msg: ChatServerMessage) => {
      const at = new Date();
      const iso = at.toISOString();

      if (msg.type === "response") {
        setLive((current) => [
          // Both transients go: a failed transcription answers with a `response`
          // and never a `transcription`, so this is the only thing that clears it.
          ...current.filter((item) => item.kind !== "thinking" && item.kind !== "transcribing"),
          {
            kind: "alfred",
            id: uid("alfred"),
            at: iso,
            text: msg.text,
            mood: msg.mood,
            actions: msg.actions_taken ?? [],
          },
        ]);
        if (msg.audio) playWavBase64(msg.audio);
        return;
      }

      if (msg.type === "error") {
        setLive((current) => [
          ...current.filter((item) => item.kind !== "thinking" && item.kind !== "transcribing"),
          { kind: "alfred", id: uid("alfred"), at: iso, text: msg.text, actions: [], error: true },
        ]);
        return;
      }

      if (msg.type === "transcription") {
        setLive((current) => [
          ...current.filter((item) => item.kind !== "transcribing"),
          { kind: "you", id: uid("you"), at: iso, text: msg.text, state: "sent" },
          { kind: "thinking", id: THINKING_ID, at: iso, detail: "working" },
        ]);
        return;
      }

      if (msg.type === "notification") {
        // A confirmation request has a fuse and a Door; it is not a thread row.
        if (typeof msg.metadata?.pending_action_id === "string") return;
        setLive((current) => [
          ...current,
          {
            kind: "act",
            id: uid("nt"),
            at: iso,
            hue: 255,
            text: msg.title,
            // `live`, not a source: the /ws notification frame carries none
            // (core/notifications/adapters/websocket.py). When the history is
            // next re-read, its copy of this notification takes this row's place
            // (`readBackPairs`) and shows the real one.
            meta: `${hhmm(at)} · live · ${msg.urgency}`,
          },
        ]);
        if (msg.audio && msg.urgency === "urgent") playWavBase64(msg.audio);
      }
    });
  }, [chat]);

  // Keyed on the thinking row's own timestamp, so an unrelated notification
  // arriving at 59 s does not quietly restart the countdown.
  const thinkingAt = live.find((item) => item.kind === "thinking")?.at ?? null;

  useEffect(() => {
    if (!thinkingAt) return;
    const timer = setTimeout(() => {
      setLive((current) => [
        ...current.filter((item) => item.kind !== "thinking"),
        {
          kind: "alfred",
          id: uid("alfred"),
          at: new Date().toISOString(),
          text: "No reply in 60 s.",
          actions: [],
          error: true,
        },
      ]);
    }, NO_REPLY_MS);
    return () => clearTimeout(timer);
  }, [thinkingAt]);

  const items = useMemo(() => {
    const merged = [...(history ?? []), ...(tombstones ?? []), ...live].sort(
      (a, b) => Date.parse(a.at) - Date.parse(b.at),
    );
    return withDividers(merged, now);
  }, [history, tombstones, live, now]);

  return { items, thinking: thinkingAt !== null, sendText, sendAudio };
}
