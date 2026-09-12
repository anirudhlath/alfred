import { compareIds, rowKey, STREAMS, type StreamName, type StreamRef } from "./streams";
import type { StreamEntry, StreamPage } from "./types";

/**
 * Entries kept per stream, and the floor of the high-water mark: a stream the
 * user paged deeper than this keeps that depth, so a later frame or refresh
 * never rolls back what `↑ older` fetched. Beyond the mark the oldest fall off
 * and the cursor points at the last one kept, so `↑ older` brings them straight
 * back — memory stays bounded without anything becoming unreachable.
 */
export const MAX_PER_STREAM = 400;

export interface FeedRow extends StreamRef {
  /** `${stream}:${id}` — unique across streams, stable across renders. */
  key: string;
}

export interface StreamFeed {
  /** Newest first, one entry per id. */
  entries: StreamEntry[];
  /**
   * Where `↑ older` reads from next; null once the stream is known in full. A
   * non-null cursor implies the page it came from was non-empty: the server
   * sets `next_before` only on a full page (`core/channels/admin_api.py`), so
   * an empty page carrying a cursor is not a case this reducer handles.
   */
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

/**
 * The most entries an update may keep: the cap, or the depth already fetched,
 * whichever is deeper. Older mode is exempt — it is the thing that raises the
 * mark, and the rest of the reducer then honours it.
 */
function highWater(feed: StreamFeed): number {
  return Math.max(MAX_PER_STREAM, feed.entries.length);
}

function trim(
  entries: StreamEntry[],
  nextBefore: string | null,
  limit: number,
): Pick<StreamFeed, "entries" | "nextBefore"> {
  if (entries.length <= limit) return { entries, nextBefore };
  const kept = entries.slice(0, limit);
  return { entries: kept, nextBefore: kept[limit - 1].id };
}

function withPage(feed: StreamFeed, page: StreamPage, mode: "head" | "older"): StreamFeed {
  const incoming = [...page.entries].sort((x, y) => compareIds(y.id, x.id));
  if (mode === "older") {
    // Growing downward is what the user asked for: never trim it away.
    return { entries: union(feed.entries, incoming), nextBefore: page.next_before, loaded: true };
  }
  const limit = highWater(feed);
  if (page.next_before === null) {
    // The whole stream fits in one page: there is nothing older to page to.
    return { ...trim(union(feed.entries, incoming), null, limit), loaded: true };
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
  const merged = trim(union(feed.entries, incoming), feed.loaded ? feed.nextBefore : page.next_before, limit);
  return { ...merged, loaded: true };
}

function withLive(feed: StreamFeed, entry: StreamEntry): StreamFeed {
  // Older than the cursor: `↑ older` will fetch it in its place. Taking it now
  // would push the horizon back across a stretch the stream has not read. The
  // reducer checks this before holding a frame; this covers the resume replay.
  if (feed.nextBefore !== null && compareIds(entry.id, feed.nextBefore) < 0) return feed;
  return { ...feed, ...trim(union(feed.entries, [entry]), feed.nextBefore, highWater(feed)) };
}

export function feedReducer(state: FeedState, event: FeedEvent): FeedState {
  switch (event.type) {
    case "page":
      return {
        ...state,
        streams: { ...state.streams, [event.stream]: withPage(state.streams[event.stream], event.page, event.mode) },
      };
    case "live": {
      // Two frames the stream has no use for: an id it already shows, and one
      // older than its cursor (see `withLive`). Both are answered before the
      // paused branch, so `Resume · N new` never counts a row that resume would
      // turn around and drop — the frame is still proof the feed is live.
      const feed = state.streams[event.stream];
      if (
        feed.entries.some((e) => e.id === event.entry.id) ||
        (feed.nextBefore !== null && compareIds(event.entry.id, feed.nextBefore) < 0)
      ) {
        return { ...state, liveAt: event.at };
      }
      const ref: StreamRef = { stream: event.stream, entry: event.entry };
      const row: FeedRow = { ...ref, key: rowKey(ref) };
      if (state.paused) {
        const held = state.held.some((h) => h.key === row.key) ? state.held : [...state.held, row];
        return { ...state, held, liveAt: event.at };
      }
      return {
        ...state,
        liveAt: event.at,
        streams: { ...state.streams, [event.stream]: withLive(feed, event.entry) },
      };
    }
    case "seen":
      return { ...state, liveAt: event.at };
    case "pause":
      return state.paused ? state : { ...state, paused: true };
    case "resume": {
      if (!state.paused && state.held.length === 0) return state;
      const streams = { ...state.streams };
      for (const row of state.held) streams[row.stream] = withLive(streams[row.stream], row.entry);
      return { ...state, streams, held: [], paused: false };
    }
  }
}

/** How far back a stream that can still page is known: its oldest entry, else null. */
function depthOf(feed: StreamFeed): string | null {
  if (feed.nextBefore === null) return null;
  const oldest = feed.entries[feed.entries.length - 1];
  return oldest === undefined ? null : oldest.id;
}

/**
 * How far back every target is known. A stream with a cursor is only known
 * back to its oldest loaded entry; a stream without one is known in full and
 * never limits the view. A stream whose head read failed also keeps
 * `nextBefore: null` and so does not constrain the view — the bench's "N of 8
 * streams could not be read" banner is what covers that. Null when nothing
 * limits it.
 */
function horizonOf(state: FeedState, streams: readonly StreamName[]): string | null {
  let horizon: string | null = null;
  for (const name of streams) {
    const depth = depthOf(state.streams[name]);
    if (depth === null) continue;
    if (horizon === null || compareIds(depth, horizon) > 0) horizon = depth;
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
        rows.push({ stream, entry, key: rowKey({ stream, entry }) });
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
    const depth = depthOf(state.streams[name]);
    return depth !== null && compareIds(depth, horizon) >= 0;
  });
}
