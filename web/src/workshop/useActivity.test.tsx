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
 */
const telemetry = (): FakeTelemetry => telemetries.at(-1) as FakeTelemetry;

const BASE = 1788815640000;
const entry = (offsetMs: number): StreamEntry => ({ id: `${BASE + offsetMs}-0`, event: { n: offsetMs } });
const empty: StreamPage = { entries: [], next_before: null };

/** URL → page (or status) for every stream read. Unknown URLs get an empty page. */
let routes: Record<string, StreamPage | number> = {};
const calls: string[] = [];

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      const route = routes[url] ?? empty;
      if (typeof route === "number") {
        return new Response(JSON.stringify({ detail: "redis gone" }), { status: route });
      }
      return new Response(JSON.stringify(route), { status: 200 });
    }),
  );
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

beforeEach(() => {
  routes = {};
  calls.length = 0;
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
    const view = mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(telemetry().subscribe).toHaveBeenCalledWith([...STREAMS]);
    view.unmount();
    expect(telemetry().unsubscribe).toHaveBeenCalledWith([...STREAMS]);
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
    fireEvent.click(screen.getByRole("button", { name: "resume" }));
    expect(state()).toMatchObject({ paused: false, heldCount: 0 });
    expect(rowKeys()).toEqual([`events:${entry(2000).id}`, `events:${entry(1000).id}`]);
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
    routes[`/api/admin/streams/events?count=50&before=${entry(4000).id}`] = {
      entries: [entry(2000)],
      next_before: null,
    };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(state().cursor).toBe(entry(4000).id);
    expect(rowKeys()).toHaveLength(3);

    fireEvent.click(screen.getByRole("button", { name: "older" }));
    expect(state().fetchingOlder).toBe(true);
    await waitFor(() => expect(state().fetchingOlder).toBe(false));
    // Only events sat at the horizon; home_state already reached further back.
    expect(calls.filter((url) => url.includes("before="))).toEqual([
      `/api/admin/streams/events?count=50&before=${entry(4000).id}`,
    ]);
    expect(rowKeys()).toEqual([
      `home_state:${entry(6000).id}`,
      `events:${entry(5000).id}`,
      `events:${entry(4000).id}`,
      `events:${entry(2000).id}`,
      `home_state:${entry(1000).id}`,
    ]);
    expect(state().cursor).toBe(entry(1000).id);
  });
});
