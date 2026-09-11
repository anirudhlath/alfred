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
