import { describe, expect, it } from "vitest";
import {
  feedReducer,
  initialFeed,
  MAX_PER_STREAM,
  mergeRows,
  olderTargets,
  type FeedEvent,
  type FeedState,
} from "./feed";
import { STREAMS, type StreamName } from "./streams";
import type { StreamEntry, StreamPage } from "./types";

const BASE = 1788815640000;

/** An entry `offsetMs` after BASE. `seq` is the Redis sequence half of the id. */
function entry(offsetMs: number, seq = 0): StreamEntry {
  return { id: `${BASE + offsetMs}-${seq}`, event: { n: offsetMs } };
}

function page(entries: StreamEntry[], next_before: string | null = null): StreamPage {
  return { entries, next_before };
}

function head(stream: StreamName, entries: StreamEntry[], next_before: string | null = null): FeedEvent {
  return { type: "page", stream, mode: "head", page: page(entries, next_before) };
}

function older(stream: StreamName, entries: StreamEntry[], next_before: string | null = null): FeedEvent {
  return { type: "page", stream, mode: "older", page: page(entries, next_before) };
}

function live(stream: StreamName, e: StreamEntry, at: number): FeedEvent {
  return { type: "live", stream, entry: e, at };
}

function reduce(events: FeedEvent[], from: FeedState = initialFeed()): FeedState {
  return events.reduce(feedReducer, from);
}

function ids(state: FeedState, stream: StreamName): string[] {
  return state.streams[stream].entries.map((e) => e.id);
}

describe("initialFeed", () => {
  it("starts with eight empty streams, not paused, nothing held, never live", () => {
    const state = initialFeed();
    expect(Object.keys(state.streams)).toEqual([...STREAMS]);
    for (const name of STREAMS) {
      expect(state.streams[name]).toEqual({ entries: [], nextBefore: null, loaded: false });
    }
    expect(state).toMatchObject({ paused: false, held: [], liveAt: null });
  });
});

describe("head pages", () => {
  it("fills an empty stream, newest first, and keeps the server's cursor", () => {
    const state = reduce([head("events", [entry(1000), entry(3000), entry(2000)], entry(1000).id)]);
    expect(ids(state, "events")).toEqual([entry(3000).id, entry(2000).id, entry(1000).id]);
    expect(state.streams.events).toMatchObject({ nextBefore: entry(1000).id, loaded: true });
  });

  it("merges an overlapping refresh and keeps the deeper cursor", () => {
    const state = reduce([
      head("events", [entry(3000), entry(2000), entry(1000)], entry(1000).id),
      head("events", [entry(4000), entry(3000), entry(2000)], entry(2000).id),
    ]);
    expect(ids(state, "events")).toEqual([4000, 3000, 2000, 1000].map((ms) => entry(ms).id));
    expect(state.streams.events.nextBefore).toBe(entry(1000).id);
  });

  it("starts over when a refresh leaves a gap it cannot see across", () => {
    const state = reduce([
      head("events", [entry(2000), entry(1000)], entry(1000).id),
      head("events", [entry(9000), entry(8000)], entry(8000).id),
    ]);
    expect(ids(state, "events")).toEqual([entry(9000).id, entry(8000).id]);
    expect(state.streams.events.nextBefore).toBe(entry(8000).id);
  });

  it("marks a stream complete when the page has no cursor", () => {
    const state = reduce([head("events", [entry(1000)])]);
    expect(state.streams.events).toEqual({ entries: [entry(1000)], nextBefore: null, loaded: true });
  });

  it("drops duplicate ids", () => {
    const state = reduce([
      head("events", [entry(2000), entry(1000)], entry(1000).id),
      head("events", [entry(2000), entry(1000)], entry(1000).id),
    ]);
    expect(ids(state, "events")).toEqual([entry(2000).id, entry(1000).id]);
  });

  it("an empty page leaves an empty, complete, loaded stream", () => {
    // The server sets `next_before` only on a full page, so an empty page never
    // carries a cursor and a stream is never left paging into nothing.
    const state = reduce([head("events", [])]);
    expect(state.streams.events).toEqual({ entries: [], nextBefore: null, loaded: true });
  });

  it("a page with no cursor still honours the mark, and re-arms the cursor when it trims", () => {
    const deep = Array.from({ length: MAX_PER_STREAM }, (_, i) => entry((MAX_PER_STREAM + 50 - i) * 1000));
    const below = Array.from({ length: 50 }, (_, i) => entry((50 - i) * 1000));
    const paged = reduce([
      head("events", deep, deep[deep.length - 1].id),
      older("events", below, entry(500).id),
    ]);

    // The server has since trimmed the stream: a head read now says it is
    // complete. What we already hold plus that page is one past the mark.
    const state = feedReducer(paged, head("events", [entry(500000), entry(450000)]));
    expect(state.streams.events.entries).toHaveLength(MAX_PER_STREAM + 50);
    expect(ids(state, "events")[0]).toBe(entry(500000).id);
    // "Complete" stops being true the moment the mark drops an entry, so the
    // cursor names the last one kept rather than staying null.
    expect(state.streams.events.nextBefore).toBe(entry(2000).id);
  });

  it("takes the page's cursor when a live frame arrived before the first page", () => {
    const state = reduce([
      live("events", entry(3000), 1),
      head("events", [entry(3000), entry(2000)], entry(2000).id),
    ]);
    expect(ids(state, "events")).toEqual([entry(3000).id, entry(2000).id]);
    expect(state.streams.events.nextBefore).toBe(entry(2000).id);
  });
});

describe("older pages", () => {
  it("appends below and moves the cursor to the server's", () => {
    const state = reduce([
      head("events", [entry(3000), entry(2000)], entry(2000).id),
      older("events", [entry(1000), entry(500)], entry(500).id),
    ]);
    expect(ids(state, "events")).toEqual([3000, 2000, 1000, 500].map((ms) => entry(ms).id));
    expect(state.streams.events.nextBefore).toBe(entry(500).id);
  });

  it("an empty page with no cursor ends paging", () => {
    const state = reduce([
      head("events", [entry(3000), entry(2000)], entry(2000).id),
      older("events", []),
    ]);
    expect(ids(state, "events")).toEqual([entry(3000).id, entry(2000).id]);
    expect(state.streams.events).toMatchObject({ nextBefore: null, loaded: true });
  });
});

describe("live entries", () => {
  it("goes to the top and stamps when the feed was last live", () => {
    const state = reduce([
      head("events", [entry(2000), entry(1000)], entry(1000).id),
      live("events", entry(3000), 42),
    ]);
    expect(ids(state, "events")).toEqual([entry(3000).id, entry(2000).id, entry(1000).id]);
    expect(state.liveAt).toBe(42);
    expect(feedReducer(state, { type: "seen", at: 43 }).liveAt).toBe(43);
  });

  it("ignores an id already held", () => {
    const state = reduce([
      head("events", [entry(2000), entry(1000)], entry(1000).id),
      live("events", entry(2000), 42),
    ]);
    expect(ids(state, "events")).toEqual([entry(2000).id, entry(1000).id]);
  });

  it("keeps the newest MAX_PER_STREAM and points the cursor at the last one kept", () => {
    const full = Array.from({ length: MAX_PER_STREAM }, (_, i) => entry((MAX_PER_STREAM - i) * 1000));
    const state = reduce([
      head("events", full),
      live("events", entry((MAX_PER_STREAM + 1) * 1000), 1),
    ]);
    const kept = ids(state, "events");
    expect(kept).toHaveLength(MAX_PER_STREAM);
    expect(kept[0]).toBe(entry((MAX_PER_STREAM + 1) * 1000).id);
    expect(kept[MAX_PER_STREAM - 1]).toBe(entry(2000).id);
    expect(state.streams.events.nextBefore).toBe(entry(2000).id);
  });

  it("is held, not shown, while paused — and still stamps liveAt", () => {
    const state = reduce([head("events", [entry(1000)]), { type: "pause" }, live("events", entry(2000), 42)]);
    expect(ids(state, "events")).toEqual([entry(1000).id]);
    expect(state.held.map((row) => row.key)).toEqual([`events:${entry(2000).id}`]);
    expect(state).toMatchObject({ paused: true, liveAt: 42 });
  });

  it("holds each key once", () => {
    const state = reduce([{ type: "pause" }, live("events", entry(2000), 1), live("events", entry(2000), 2)]);
    expect(state.held).toHaveLength(1);
  });

  it("keeps the depth ↑ older fetched instead of trimming back to the cap", () => {
    const deep = Array.from({ length: MAX_PER_STREAM }, (_, i) => entry((MAX_PER_STREAM + 50 - i) * 1000));
    const below = Array.from({ length: 50 }, (_, i) => entry((50 - i) * 1000));
    const paged = reduce([
      head("events", deep, deep[deep.length - 1].id),
      older("events", below, entry(500).id),
    ]);
    expect(paged.streams.events.entries).toHaveLength(MAX_PER_STREAM + 50);

    const state = feedReducer(paged, live("events", entry(500000), 1));
    // The mark is the paged depth, not the cap: the fetched page stays put.
    expect(state.streams.events.entries).toHaveLength(MAX_PER_STREAM + 50);
    expect(ids(state, "events")[0]).toBe(entry(500000).id);
    // What the mark does evict, the cursor names, so it reads straight back.
    expect(state.streams.events.nextBefore).toBe(entry(2000).id);

    // A refresh honours the same mark rather than rolling back to the cap.
    const refreshed = feedReducer(state, head("events", [entry(500000), entry(450000)], entry(450000).id));
    expect(refreshed.streams.events.entries).toHaveLength(MAX_PER_STREAM + 50);
    expect(refreshed.streams.events.nextBefore).toBe(entry(2000).id);
  });

  it("ignores an entry older than the cursor, which paging will bring back in order", () => {
    const paged = reduce([head("events", [entry(5000), entry(4000)], entry(4000).id)]);
    const state = feedReducer(paged, live("events", entry(2000), 42));
    expect(ids(state, "events")).toEqual([entry(5000).id, entry(4000).id]);
    expect(state.streams.events.nextBefore).toBe(entry(4000).id);
    expect(mergeRows(state, STREAMS).cursor).toBe(entry(4000).id);
  });

  it("never holds an entry older than the cursor", () => {
    const state = reduce([
      head("events", [entry(5000), entry(4000)], entry(4000).id),
      { type: "pause" },
      live("events", entry(2000), 42),
    ]);
    // Resume would only drop it again, so it must never reach `held` — where
    // it would have inflated `Resume · N new`.
    expect(state.held).toEqual([]);
    expect(state.liveAt).toBe(42);
    expect(ids(state, "events")).toEqual([entry(5000).id, entry(4000).id]);
  });

  it("never holds an id the stream already shows", () => {
    const state = reduce([head("events", [entry(2000)]), { type: "pause" }, live("events", entry(2000), 42)]);
    expect(state.held).toEqual([]);
    expect(state.liveAt).toBe(42);
  });
});

describe("pause and resume", () => {
  it("resume releases held entries into their streams and clears them", () => {
    const state = reduce([
      head("events", [entry(1000)]),
      { type: "pause" },
      live("events", entry(3000), 1),
      live("home_state", entry(2000), 2),
      { type: "resume" },
    ]);
    expect(state).toMatchObject({ paused: false, held: [] });
    expect(ids(state, "events")).toEqual([entry(3000).id, entry(1000).id]);
    expect(ids(state, "home_state")).toEqual([entry(2000).id]);
  });

  it("pausing twice is the same state", () => {
    const paused = reduce([{ type: "pause" }]);
    expect(feedReducer(paused, { type: "pause" })).toBe(paused);
  });

  it("resume with nothing paused and nothing held is the same state", () => {
    const state = reduce([head("events", [entry(1000)])]);
    expect(feedReducer(state, { type: "resume" })).toBe(state);
  });

  it("trims on release when the held rows push a stream past its limit", () => {
    const full = Array.from({ length: MAX_PER_STREAM }, (_, i) => entry((MAX_PER_STREAM - i) * 1000));
    const state = reduce([
      head("events", full),
      { type: "pause" },
      live("events", entry((MAX_PER_STREAM + 1) * 1000), 1),
      live("events", entry((MAX_PER_STREAM + 2) * 1000), 2),
      { type: "resume" },
    ]);
    const kept = ids(state, "events");
    expect(kept).toHaveLength(MAX_PER_STREAM);
    expect(kept[0]).toBe(entry((MAX_PER_STREAM + 2) * 1000).id);
    expect(state.streams.events.nextBefore).toBe(entry(3000).id);
  });
});

describe("mergeRows", () => {
  it("merges the targets newest first, ties in catalogue order", () => {
    const state = reduce([
      head("events", [entry(2000), entry(1000)]),
      head("home_state", [entry(2000, 1), entry(1000)]),
    ]);
    const { rows, cursor } = mergeRows(state, STREAMS);
    expect(rows.map((row) => row.key)).toEqual([
      `home_state:${entry(2000, 1).id}`,
      `events:${entry(2000).id}`,
      `events:${entry(1000).id}`,
      `home_state:${entry(1000).id}`,
    ]);
    expect(cursor).toBeNull();
  });

  it("hides rows older than the shallowest stream that can still page, and offers that depth as the cursor", () => {
    const state = reduce([
      head("events", [entry(5000), entry(4000)], entry(4000).id),
      head("home_state", [entry(6000), entry(3000), entry(1000)], entry(1000).id),
    ]);
    const all = mergeRows(state, STREAMS);
    expect(all.rows.map((row) => row.entry.id)).toEqual([6000, 5000, 4000].map((ms) => entry(ms).id));
    expect(all.cursor).toBe(entry(4000).id);

    // Solo on the deeper stream: its own depth is the only horizon.
    const solo = mergeRows(state, ["home_state"]);
    expect(solo.rows.map((row) => row.entry.id)).toEqual([6000, 3000, 1000].map((ms) => entry(ms).id));
    expect(solo.cursor).toBe(entry(1000).id);
  });

  it("a stream with nothing older never sets the horizon", () => {
    const state = reduce([
      head("events", [entry(5000), entry(4000)]),
      head("home_state", [entry(6000), entry(3000), entry(1000)], entry(1000).id),
    ]);
    const { rows, cursor } = mergeRows(state, STREAMS);
    expect(rows.map((row) => row.entry.id)).toEqual([6000, 5000, 4000, 3000, 1000].map((ms) => entry(ms).id));
    expect(cursor).toBe(entry(1000).id);
  });

  it("no targets is an empty view with nothing older to fetch", () => {
    const state = reduce([head("events", [entry(2000)], entry(2000).id)]);
    expect(mergeRows(state, [])).toEqual({ rows: [], cursor: null });
  });
});

describe("olderTargets", () => {
  it("names the streams sitting at the horizon", () => {
    const state = reduce([
      head("events", [entry(5000), entry(4000)], entry(4000).id),
      head("home_state", [entry(6000), entry(3000), entry(1000)], entry(1000).id),
      head("actions", [entry(4000)], entry(4000).id),
    ]);
    expect(olderTargets(state, STREAMS)).toEqual(["events", "actions"]);
    expect(olderTargets(state, ["home_state"])).toEqual(["home_state"]);
    expect(olderTargets(initialFeed(), STREAMS)).toEqual([]);
  });
});
