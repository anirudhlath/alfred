import { compareIds, STREAMS, type StreamName, type StreamRef } from "./streams";
import type { StreamEntry, StreamPage } from "./types";

/**
 * Entries kept per stream. Beyond this the oldest fall off and the cursor
 * points at the last one kept, so `↑ older` brings them straight back —
 * memory stays bounded without anything becoming unreachable.
 */
export const MAX_PER_STREAM = 400;

export interface FeedRow extends StreamRef {
  /** `${stream}:${id}` — unique across streams, stable across renders. */
  key: string;
}

export interface StreamFeed {
  /** Newest first, one entry per id. */
  entries: StreamEntry[];
  /** Where `↑ older` reads from next; null once the stream is known in full. */
  nextBefore: string | null;
  /** A head page has been read, so an empty list means the stream is empty. */
  loaded: boolean;
}

export interface FeedState {
  streams: Record<StreamName, StreamFeed>;
  paused: boolean;
  /** Live rows that arrived while paused, in arrival order, unique by key. */
  held: FeedRow[];
  /** When the feed was last known live: socket up, or a frame arrived. */
  liveAt: number | null;
}

export type FeedEvent =
  | { type: "page"; stream: StreamName; page: StreamPage; mode: "head" | "older" }
  | { type: "live"; stream: StreamName; entry: StreamEntry; at: number }
  | { type: "seen"; at: number }
  | { type: "pause" }
  | { type: "resume" };

export function rowKey(stream: StreamName, id: string): string {
  return `${stream}:${id}`;
}

export function initialFeed(): FeedState {
  const streams = {} as Record<StreamName, StreamFeed>;
  for (const name of STREAMS) streams[name] = { entries: [], nextBefore: null, loaded: false };
  return { streams, paused: false, held: [], liveAt: null };
}

/** Newest first, one entry per id; a later copy of an id wins. */
function union(a: StreamEntry[], b: StreamEntry[]): StreamEntry[] {
  const byId = new Map<string, StreamEntry>();
  for (const e of a) byId.set(e.id, e);
  for (const e of b) byId.set(e.id, e);
  return [...byId.values()].sort((x, y) => compareIds(y.id, x.id));
}

function trim(entries: StreamEntry[], nextBefore: string | null): Pick<StreamFeed, "entries" | "nextBefore"> {
  if (entries.length <= MAX_PER_STREAM) return { entries, nextBefore };
  const kept = entries.slice(0, MAX_PER_STREAM);
  return { entries: kept, nextBefore: kept[MAX_PER_STREAM - 1].id };
}

function withPage(feed: StreamFeed, page: StreamPage, mode: "head" | "older"): StreamFeed {
  const incoming = [...page.entries].sort((x, y) => compareIds(y.id, x.id));
  if (mode === "older") {
    // Growing downward is what the user asked for: never trim it away.
    return { entries: union(feed.entries, incoming), nextBefore: page.next_before, loaded: true };
  }
  if (page.next_before === null) {
    // The whole stream fits in one page: there is nothing older to page to.
    return { entries: union(feed.entries, incoming), nextBefore: null, loaded: true };
  }
  const newest = feed.entries[0];
  const oldestIncoming = incoming[incoming.length - 1];
  const gap =
    newest === undefined ||
    (oldestIncoming !== undefined && compareIds(oldestIncoming.id, newest.id) > 0);
  if (gap) {
    // Everything held is older than this whole page, with an unknown stretch
    // between: keep the page and let its cursor lead back to the rest.
    return { entries: incoming, nextBefore: page.next_before, loaded: true };
  }
  const merged = trim(union(feed.entries, incoming), feed.loaded ? feed.nextBefore : page.next_before);
  return { ...merged, loaded: true };
}

function withLive(feed: StreamFeed, entry: StreamEntry): StreamFeed {
  if (feed.entries.some((e) => e.id === entry.id)) return feed;
  return { ...feed, ...trim(union(feed.entries, [entry]), feed.nextBefore) };
}

export function feedReducer(state: FeedState, event: FeedEvent): FeedState {
  switch (event.type) {
    case "page":
      return {
        ...state,
        streams: { ...state.streams, [event.stream]: withPage(state.streams[event.stream], event.page, event.mode) },
      };
    case "live": {
      const row: FeedRow = { stream: event.stream, entry: event.entry, key: rowKey(event.stream, event.entry.id) };
      if (state.paused) {
        const held = state.held.some((h) => h.key === row.key) ? state.held : [...state.held, row];
        return { ...state, held, liveAt: event.at };
      }
      return {
        ...state,
        liveAt: event.at,
        streams: { ...state.streams, [event.stream]: withLive(state.streams[event.stream], event.entry) },
      };
    }
    case "seen":
      return { ...state, liveAt: event.at };
    case "pause":
      return state.paused ? state : { ...state, paused: true };
    case "resume": {
      const streams = { ...state.streams };
      for (const row of state.held) streams[row.stream] = withLive(streams[row.stream], row.entry);
      return { ...state, streams, held: [], paused: false };
    }
  }
}

function oldestId(feed: StreamFeed): string | null {
  const last = feed.entries[feed.entries.length - 1];
  return last === undefined ? null : last.id;
}

/**
 * How far back every target is known. A stream with a cursor is only known
 * back to its oldest loaded entry; a stream without one is known in full and
 * never limits the view. Null when nothing limits it.
 */
function horizonOf(state: FeedState, streams: readonly StreamName[]): string | null {
  let horizon: string | null = null;
  for (const name of streams) {
    const feed = state.streams[name];
    const oldest = oldestId(feed);
    if (feed.nextBefore === null || oldest === null) continue;
    if (horizon === null || compareIds(oldest, horizon) > 0) horizon = oldest;
  }
  return horizon;
}

/**
 * The merged list: every target's rows from the horizon forward, newest
 * first, same-id ties in catalogue order. `cursor` is the horizon — what the
 * `↑ older` button names, null when there is nothing older to fetch.
 */
export function mergeRows(
  state: FeedState,
  streams: readonly StreamName[],
): { rows: FeedRow[]; cursor: string | null } {
  const horizon = horizonOf(state, streams);
  const rows: FeedRow[] = [];
  for (const stream of streams) {
    for (const entry of state.streams[stream].entries) {
      if (horizon === null || compareIds(entry.id, horizon) >= 0) {
        rows.push({ stream, entry, key: rowKey(stream, entry.id) });
      }
    }
  }
  rows.sort(
    (a, b) => compareIds(b.entry.id, a.entry.id) || STREAMS.indexOf(a.stream) - STREAMS.indexOf(b.stream),
  );
  return { rows, cursor: horizon };
}

/** The targets `↑ older` must read to move the horizon: those sitting on it. */
export function olderTargets(state: FeedState, streams: readonly StreamName[]): StreamName[] {
  const horizon = horizonOf(state, streams);
  if (horizon === null) return [];
  return streams.filter((name) => {
    const feed = state.streams[name];
    const oldest = oldestId(feed);
    return feed.nextBefore !== null && oldest !== null && compareIds(oldest, horizon) >= 0;
  });
}
