import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { STREAMS } from "@/lib/streams";
import type { StreamEntry, StreamPage, TelemetryMessage } from "@/lib/types";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { useActivity } from "./useActivity";

const { telemetries } = vi.hoisted(() => ({ telemetries: [] as unknown[] }));

vi.mock("@/lib/chat-socket", () => {
  class ChatSocket {
    onstatus: (status: string) => void = () => {};
    connect() { this.onstatus("online"); }
    close = vi.fn();
    sendText = vi.fn(() => true);
    sendAudio = vi.fn(() => true);
    listen() { return () => {}; }
  }
  return { ChatSocket };
});

vi.mock("@/lib/telemetry-socket", () => {
  class TelemetrySocket {
    onstatus: (status: string) => void = () => {};
    listeners = new Set<(msg: TelemetryMessage) => void>();
    close = vi.fn();
    subscribe = vi.fn();
    unsubscribe = vi.fn();
    constructor() { telemetries.push(this); }
    connect() { this.onstatus("online"); }
    listen(fn: (msg: TelemetryMessage) => void): () => void {
      this.listeners.add(fn);
      return () => void this.listeners.delete(fn);
    }
    deliver(msg: TelemetryMessage): void {
      for (const fn of [...this.listeners]) fn(msg);
    }
  }
  return { TelemetrySocket };
});

interface FakeTelemetry {
  subscribe: ReturnType<typeof vi.fn>;
  unsubscribe: ReturnType<typeof vi.fn>;
  deliver: (msg: TelemetryMessage) => void;
}

/**
 * The socket ConnectionProvider uses. It is a module-level singleton built when
 * that module is first imported — i.e. once for the whole file, before any
 * `beforeEach` — so this list is filled exactly once and must never be cleared.
 *
 * The consequence for assertions: the `vi.fn()`s on it are **cumulative across
 * every test in the file**, never reset between mounts. Assert on deltas
 * (`mock.calls.length` before and after) and on `toHaveBeenLastCalledWith` —
 * never a bare `toHaveBeenCalledWith`, which an earlier test has already
 * satisfied and which therefore proves nothing about this one.
 */
const telemetry = (): FakeTelemetry => telemetries.at(-1) as FakeTelemetry;

const BASE = 1788815640000;
const entry = (offsetMs: number): StreamEntry => ({ id: `${BASE + offsetMs}-0`, event: { n: offsetMs } });
const empty: StreamPage = { entries: [], next_before: null };

/** URL → page (or status) for every stream read. Unknown URLs get an empty page. */
let routes: Record<string, StreamPage | number> = {};
const calls: string[] = [];
/** Every request that has produced its answer — what `calls` becomes once released. */
const settled: string[] = [];

interface Gate {
  promise: Promise<void>;
  release: () => void;
}

function defer(): Gate {
  let release = (): void => {};
  const promise = new Promise<void>((resolve) => {
    release = () => resolve();
  });
  return { promise, release };
}

/**
 * While set, every request that starts is held until the test releases it. The
 * route is looked up *after* the wait, so a test can change the answer under a
 * request already in flight — which is how a stale read is staged.
 */
let gate: Gate | null = null;

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      const held = gate;
      if (held) await held.promise;
      const route = routes[url] ?? empty;
      settled.push(url);
      if (typeof route === "number") {
        return new Response(JSON.stringify({ detail: "redis gone" }), { status: route });
      }
      return new Response(JSON.stringify(route), { status: 200 });
    }),
  );
}

/** Drain what is pending: the microtask queue, and the macrotask turn behind it. */
async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function Probe() {
  const a = useActivity();
  return (
    <div>
      <ul aria-label="rows">
        {a.rows.map((row) => (
          <li key={row.key}>
            <button type="button" onClick={() => a.toggle(row.key)}>{row.key}</button>
          </li>
        ))}
      </ul>
      <output data-testid="state">
        {JSON.stringify({
          loaded: a.loaded,
          live: a.live,
          paused: a.paused,
          liveAt: a.liveAt,
          heldCount: a.heldCount,
          solo: a.solo,
          expanded: a.expanded,
          cursor: a.cursor,
          fetchingOlder: a.fetchingOlder,
          error: a.error,
          counts: a.counts,
          streamLoaded: a.streamLoaded,
        })}
      </output>
      <button type="button" onClick={a.pause}>pause</button>
      <button type="button" onClick={a.resume}>resume</button>
      <button type="button" onClick={a.loadOlder}>older</button>
      <button type="button" onClick={() => a.setSolo("home_state")}>solo home_state</button>
      <button type="button" onClick={() => a.setSolo(null)}>all</button>
    </div>
  );
}

function state(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId("state").textContent ?? "{}") as Record<string, unknown>;
}

function rowKeys(): string[] {
  return Array.from(screen.getByRole("list", { name: "rows" }).querySelectorAll("li")).map(
    (li) => li.textContent ?? "",
  );
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <Probe />
      </ConnectionProvider>
    </QueryClientProvider>,
  );
}

const headUrl = (name: string) => `/api/admin/streams/${name}?count=50`;
const olderUrl = (name: string, before: string) => `/api/admin/streams/${name}?count=50&before=${before}`;

beforeEach(() => {
  routes = {};
  calls.length = 0;
  settled.length = 0;
  gate = null;
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useActivity", () => {
  it("reads the head of all eight streams and merges them newest first", async () => {
    routes[headUrl("events")] = { entries: [entry(3000), entry(1000)], next_before: null };
    routes[headUrl("home_state")] = { entries: [entry(2000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(STREAMS.map(headUrl).every((url) => calls.includes(url))).toBe(true);
    expect(rowKeys()).toEqual([
      `events:${entry(3000).id}`,
      `home_state:${entry(2000).id}`,
      `events:${entry(1000).id}`,
    ]);
    expect(state().counts).toMatchObject({ events: 2, home_state: 1, actions: 0 });
    expect(state().cursor).toBeNull();
    expect(state().error).toBeNull();
  });

  it("reports the streams it could not read, and still shows the rest", async () => {
    routes[headUrl("events")] = { entries: [entry(1000)], next_before: null };
    routes[headUrl("actions")] = 500;
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(state().error).toBe("1 of 8 streams could not be read · redis gone");
    expect(rowKeys()).toEqual([`events:${entry(1000).id}`]);
    // Per stream, not just in the banner: the bench solos one stream at a
    // time, and the global `loaded` cannot tell an empty stream from one whose
    // read 500'd.
    expect(state().streamLoaded).toMatchObject({ events: true, actions: false, home_state: true });
  });

  it("re-reads the heads when the app comes back to the foreground", async () => {
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    const before = calls.length;
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(calls.length).toBe(before + STREAMS.length));
  });

  it("subscribes to all eight streams while mounted and lets go on unmount", async () => {
    const releases = telemetry().unsubscribe.mock.calls.length;
    const view = mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(telemetry().subscribe).toHaveBeenLastCalledWith([...STREAMS]);
    expect(telemetry().unsubscribe.mock.calls.length).toBe(releases);
    view.unmount();
    expect(telemetry().unsubscribe.mock.calls.length).toBe(releases + 1);
    expect(telemetry().unsubscribe).toHaveBeenLastCalledWith([...STREAMS]);
  });

  it("puts a live entry at the top, marks the feed live, and ignores streams it does not know", async () => {
    routes[headUrl("events")] = { entries: [entry(1000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(state().live).toBe(true);
    act(() => {
      telemetry().deliver({ type: "entry", stream: "events", id: entry(2000).id, event: { n: 2000 } });
      telemetry().deliver({ type: "entry", stream: "mystery", id: entry(3000).id, event: {} });
    });
    expect(rowKeys()).toEqual([`events:${entry(2000).id}`, `events:${entry(1000).id}`]);
    expect(typeof state().liveAt).toBe("number");
  });

  it("holds live entries while paused and releases them on resume", async () => {
    routes[headUrl("events")] = { entries: [entry(1000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "pause" }));
    act(() => {
      telemetry().deliver({ type: "entry", stream: "events", id: entry(2000).id, event: { n: 2000 } });
    });
    expect(state()).toMatchObject({ paused: true, heldCount: 1 });
    expect(rowKeys()).toEqual([`events:${entry(1000).id}`]);
    const before = calls.length;
    fireEvent.click(screen.getByRole("button", { name: "resume" }));
    expect(state()).toMatchObject({ paused: false, heldCount: 0 });
    expect(rowKeys()).toEqual([`events:${entry(2000).id}`, `events:${entry(1000).id}`]);
    // Resume's own head read, awaited so it lands inside the test.
    await waitFor(() => expect(calls.length).toBe(before + STREAMS.length));
  });

  it("does not re-read the heads while paused, and reads them once on resume", async () => {
    routes[headUrl("events")] = { entries: [entry(1000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "pause" }));
    const before = calls.length;
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    // Nothing new was fetched: a head page would bypass the hold.
    expect(calls.length).toBe(before);
    fireEvent.click(screen.getByRole("button", { name: "resume" }));
    await waitFor(() => expect(calls.length).toBe(before + STREAMS.length));
  });

  it("drops a head read that lands after Pause", async () => {
    routes[headUrl("events")] = { entries: [entry(1000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));

    // A foreground read goes out, and Pause is tapped before it can land.
    const held = defer();
    gate = held;
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    gate = null;
    routes[headUrl("events")] = { entries: [entry(3000), entry(1000)], next_before: null };
    fireEvent.click(screen.getByRole("button", { name: "pause" }));

    held.release();
    await waitFor(() => expect(settled).toHaveLength(2 * STREAMS.length));
    await flush();
    // The list the hold froze is the list still on screen: a page is not a
    // live frame, so nothing would have counted it on the Resume button.
    expect(rowKeys()).toEqual([`events:${entry(1000).id}`]);
    expect(state()).toMatchObject({ paused: true, heldCount: 0 });
  });

  it("drops a head read that lands after a newer one", async () => {
    const held = defer();
    gate = held;
    mount();
    // The mount read is still out; nothing it has to say has landed.
    expect(state().loaded).toBe(false);

    // A read from the foreground return goes out unheld and finishes first.
    gate = null;
    routes[headUrl("events")] = { entries: [entry(1000)], next_before: null };
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(rowKeys()).toEqual([`events:${entry(1000).id}`]);
    expect(state().error).toBeNull();

    // Only now does the stalled read come back — and every stream 500s for it.
    for (const name of STREAMS) routes[headUrl(name)] = 500;
    held.release();
    await waitFor(() => expect(settled).toHaveLength(2 * STREAMS.length));
    await flush();
    // Its answer is about a list two reads old; it says nothing.
    expect(state().error).toBeNull();
    expect(rowKeys()).toEqual([`events:${entry(1000).id}`]);
  });

  it("solo narrows the rows and closes whatever was expanded", async () => {
    routes[headUrl("events")] = { entries: [entry(3000)], next_before: null };
    routes[headUrl("home_state")] = { entries: [entry(2000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: `events:${entry(3000).id}` }));
    expect(state().expanded).toBe(`events:${entry(3000).id}`);
    fireEvent.click(screen.getByRole("button", { name: `events:${entry(3000).id}` }));
    expect(state().expanded).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: `events:${entry(3000).id}` }));
    fireEvent.click(screen.getByRole("button", { name: "solo home_state" }));
    expect(state()).toMatchObject({ solo: "home_state", expanded: null });
    expect(rowKeys()).toEqual([`home_state:${entry(2000).id}`]);
    fireEvent.click(screen.getByRole("button", { name: "all" }));
    expect(rowKeys()).toHaveLength(2);
  });

  it("loads older pages for the streams at the horizon", async () => {
    routes[headUrl("events")] = { entries: [entry(5000), entry(4000)], next_before: entry(4000).id };
    routes[headUrl("home_state")] = { entries: [entry(6000), entry(1000)], next_before: entry(1000).id };
    routes[olderUrl("events", entry(4000).id)] = { entries: [entry(2000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(state().cursor).toBe(entry(4000).id);
    expect(rowKeys()).toHaveLength(3);

    fireEvent.click(screen.getByRole("button", { name: "older" }));
    expect(state().fetchingOlder).toBe(true);
    await waitFor(() => expect(state().fetchingOlder).toBe(false));
    // Only events sat at the horizon; home_state already reached further back.
    expect(calls.filter((url) => url.includes("before="))).toEqual([olderUrl("events", entry(4000).id)]);
    expect(rowKeys()).toEqual([
      `home_state:${entry(6000).id}`,
      `events:${entry(5000).id}`,
      `events:${entry(4000).id}`,
      `events:${entry(2000).id}`,
      `home_state:${entry(1000).id}`,
    ]);
    expect(state().cursor).toBe(entry(1000).id);
  });

  it("reads only the solo stream when ↑ older is tapped soloed", async () => {
    routes[headUrl("events")] = { entries: [entry(5000), entry(4000)], next_before: entry(4000).id };
    routes[headUrl("home_state")] = { entries: [entry(6000), entry(3000)], next_before: entry(3000).id };
    routes[olderUrl("home_state", entry(3000).id)] = { entries: [entry(1000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "solo home_state" }));
    expect(state().cursor).toBe(entry(3000).id);

    fireEvent.click(screen.getByRole("button", { name: "older" }));
    expect(state().fetchingOlder).toBe(true);
    await waitFor(() => expect(state().fetchingOlder).toBe(false));
    // events sits at a horizon of its own, and is not what the user asked for.
    expect(calls.filter((url) => url.includes("before="))).toEqual([olderUrl("home_state", entry(3000).id)]);
    expect(rowKeys()).toEqual([
      `home_state:${entry(6000).id}`,
      `home_state:${entry(3000).id}`,
      `home_state:${entry(1000).id}`,
    ]);
  });

  it("does nothing when ↑ older is tapped with nothing older to read", async () => {
    routes[headUrl("events")] = { entries: [entry(1000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(state().cursor).toBeNull();
    const before = calls.length;
    fireEvent.click(screen.getByRole("button", { name: "older" }));
    expect(calls.length).toBe(before);
    expect(state().fetchingOlder).toBe(false);
  });

  it("leaves a head-read failure standing when ↑ older succeeds", async () => {
    routes[headUrl("events")] = { entries: [entry(5000), entry(4000)], next_before: entry(4000).id };
    routes[headUrl("actions")] = 500;
    routes[olderUrl("events", entry(4000).id)] = { entries: [entry(2000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(state().error).toBe("1 of 8 streams could not be read · redis gone");

    fireEvent.click(screen.getByRole("button", { name: "older" }));
    expect(state().fetchingOlder).toBe(true);
    await waitFor(() => expect(state().fetchingOlder).toBe(false));
    expect(rowKeys()).toHaveLength(3);
    // Reading further back down one stream answers nothing about the chip that
    // could not be read at all.
    expect(state().error).toBe("1 of 8 streams could not be read · redis gone");
  });

  it("names an ↑ older failure without claiming the chips could not be read", async () => {
    routes[headUrl("events")] = { entries: [entry(5000), entry(4000)], next_before: entry(4000).id };
    routes[olderUrl("events", entry(4000).id)] = 500;
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(state().error).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "older" }));
    expect(state().fetchingOlder).toBe(true);
    await waitFor(() => expect(state().fetchingOlder).toBe(false));
    expect(state().error).toBe("Could not read further back · redis gone");
    expect(rowKeys()).toHaveLength(2);
  });

  it("retires an ↑ older failure once the heads read clean again", async () => {
    routes[headUrl("events")] = { entries: [entry(5000), entry(4000)], next_before: entry(4000).id };
    routes[olderUrl("events", entry(4000).id)] = 500;
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "older" }));
    expect(state().fetchingOlder).toBe(true);
    await waitFor(() => expect(state().fetchingOlder).toBe(false));
    expect(state().error).toBe("Could not read further back · redis gone");

    // A foreground return reads all eight cleanly. The endpoint is answering,
    // so "could not read further back" is last week's news, not the banner.
    const before = calls.length;
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(calls.length).toBe(before + STREAMS.length));
    await waitFor(() => expect(state().error).toBeNull());
    expect(rowKeys()).toHaveLength(2);
  });

  it("clears the older banner the moment a retry goes out", async () => {
    routes[headUrl("events")] = { entries: [entry(5000), entry(4000)], next_before: entry(4000).id };
    routes[olderUrl("events", entry(4000).id)] = 500;
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "older" }));
    await waitFor(() => expect(state().fetchingOlder).toBe(false));
    expect(state().error).toBe("Could not read further back · redis gone");

    // Tap it again and hold the request there: while the retry is in flight the
    // banner from the attempt before it is already gone.
    const held = defer();
    gate = held;
    fireEvent.click(screen.getByRole("button", { name: "older" }));
    expect(state()).toMatchObject({ fetchingOlder: true, error: null });

    gate = null;
    routes[olderUrl("events", entry(4000).id)] = { entries: [entry(2000)], next_before: null };
    held.release();
    await waitFor(() => expect(state().fetchingOlder).toBe(false));
    expect(state().error).toBeNull();
    expect(rowKeys()).toHaveLength(3);
  });
});
