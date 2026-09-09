import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authEvents } from "@/lib/auth-events";
import type { SocketStatus } from "@/lib/ws";
import { ConnectionProvider, useConnection } from "./ConnectionProvider";

interface FakeSocket {
  onstatus: (status: SocketStatus) => void;
  connect: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  deliver: (msg: unknown) => void;
}

const { chats, telemetries } = vi.hoisted(() => ({
  chats: [] as unknown[],
  telemetries: [] as unknown[],
}));

vi.mock("@/lib/chat-socket", () => {
  class ChatSocket {
    onstatus: (status: string) => void = () => {};
    listeners = new Set<(msg: unknown) => void>();
    connect = vi.fn();
    close = vi.fn();
    constructor() {
      chats.push(this);
    }
    listen(fn: (msg: unknown) => void): () => void {
      this.listeners.add(fn);
      return () => void this.listeners.delete(fn);
    }
    deliver(msg: unknown): void {
      for (const fn of this.listeners) fn(msg);
    }
  }
  return { ChatSocket };
});

vi.mock("@/lib/telemetry-socket", () => {
  class TelemetrySocket {
    onstatus: (status: string) => void = () => {};
    listeners = new Set<(msg: unknown) => void>();
    connect = vi.fn();
    close = vi.fn();
    subscribe = vi.fn();
    constructor() {
      telemetries.push(this);
    }
    listen(fn: (msg: unknown) => void): () => void {
      this.listeners.add(fn);
      return () => void this.listeners.delete(fn);
    }
    deliver(msg: unknown): void {
      for (const fn of this.listeners) fn(msg);
    }
  }
  return { TelemetrySocket };
});

function Probe() {
  const { online, lastTrueAt, chatStatus, reconnect } = useConnection();
  return (
    <div>
      <span data-testid="online">{String(online)}</span>
      <span data-testid="status">{chatStatus}</span>
      <span data-testid="last-true">{lastTrueAt ? lastTrueAt.toISOString() : "none"}</span>
      <button onClick={reconnect}>reconnect</button>
    </div>
  );
}

function Subscriber({ onOnline }: { onOnline: () => void }) {
  const { subscribeOnline } = useConnection();
  useEffect(() => subscribeOnline(onOnline), [subscribeOnline, onOnline]);
  return null;
}

function renderProvider() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const utils = render(
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <Probe />
      </ConnectionProvider>
    </QueryClientProvider>,
  );
  return {
    ...utils,
    invalidate,
    chat: chats[0] as FakeSocket,
    telemetry: telemetries[0] as FakeSocket,
  };
}

/** The app comes back to the foreground. */
function comeBack(): void {
  act(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

beforeEach(() => {
  // One module load means one pair of singletons for the whole file — which is the
  // behaviour under test, so reset their spies rather than expecting new instances.
  for (const socket of [chats[0], telemetries[0]] as (FakeSocket | undefined)[]) {
    socket?.connect.mockClear();
    socket?.close.mockClear();
  }
});

afterEach(() => {
  vi.useRealTimers();
  // A test that fails before its own `mockRestore()` must not hand its console
  // spy, calls and all, to the next one.
  vi.restoreAllMocks();
});

describe("ConnectionProvider", () => {
  it("connects both sockets on mount", () => {
    const { chat, telemetry } = renderProvider();
    expect(chat.connect).toHaveBeenCalled();
    expect(telemetry.connect).toHaveBeenCalled();
  });

  it("is online only while the chat socket is", () => {
    const { chat } = renderProvider();
    expect(screen.getByTestId("online")).toHaveTextContent("false");

    // `lastTrue` is module state and an earlier test may have stamped it, so
    // prove the stamp moved rather than that it exists.
    const at = new Date("2031-05-04T09:41:00Z");
    vi.setSystemTime(at);
    act(() => chat.onstatus("online"));

    expect(screen.getByTestId("online")).toHaveTextContent("true");
    expect(screen.getByTestId("status")).toHaveTextContent("online");
    expect(screen.getByTestId("last-true")).toHaveTextContent(at.toISOString());

    act(() => chat.onstatus("reconnecting"));
    expect(screen.getByTestId("online")).toHaveTextContent("false");
  });

  it("stamps last-true on every frame from the house", () => {
    const { chat } = renderProvider();
    const at = new Date("2031-05-04T09:42:00Z");
    vi.setSystemTime(at);

    act(() => chat.deliver({ type: "response", text: "Quite so, sir.", session_id: "s_1" }));

    expect(screen.getByTestId("last-true")).toHaveTextContent(at.toISOString());
  });

  it("puts the telemetry pump's trouble on the console, and nothing else", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { telemetry } = renderProvider();

    act(() => telemetry.deliver({ type: "entry", stream: "events", id: "1-0", event: {} }));
    expect(warn).not.toHaveBeenCalled();

    act(() => telemetry.deliver({ type: "status", detail: "redis_error" }));
    act(() => telemetry.deliver({ type: "error", message: "invalid JSON" }));
    expect(warn.mock.calls).toEqual([["telemetry: redis_error"], ["telemetry: invalid JSON"]]);
    warn.mockRestore();
  });

  it("says the same trouble once a minute, not once a second", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2031-05-04T09:43:00Z"));
    const { telemetry } = renderProvider();

    // The pump's whole outage, as it arrives: a frame a second.
    for (let second = 0; second < 90; second += 1) {
      act(() => telemetry.deliver({ type: "status", detail: "redis_error" }));
      vi.advanceTimersByTime(1000);
    }
    expect(warn.mock.calls).toEqual([["telemetry: redis_error"], ["telemetry: redis_error"]]);

    // A different complaint is not held back by the first.
    act(() => telemetry.deliver({ type: "error", message: "invalid JSON" }));
    expect(warn).toHaveBeenCalledTimes(3);
    warn.mockRestore();
  });

  it("turns a 4001 close on either socket into the expired gate", () => {
    const expired = vi.fn();
    const off = authEvents.on("expired", expired);
    const { chat, telemetry } = renderProvider();

    act(() => chat.onstatus("unauthorized"));
    expect(expired).toHaveBeenCalledTimes(1);

    // The chat socket has since been reopened; the telemetry socket says so on its own.
    act(() => chat.onstatus("connecting"));
    act(() => telemetry.onstatus("unauthorized"));
    expect(expired).toHaveBeenCalledTimes(2);
    off();
  });

  it("reconnects both sockets and re-reads everything on the way back from the background", () => {
    const { invalidate, chat, telemetry } = renderProvider();
    chat.connect.mockClear();
    telemetry.connect.mockClear();
    invalidate.mockClear();

    comeBack();

    expect(chat.connect).toHaveBeenCalledTimes(1);
    expect(telemetry.connect).toHaveBeenCalledTimes(1);
    const keys = invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toEqual([
      '["overview"]',
      '["room-history"]',
      '["pending-actions"]',
      '["deferred"]',
    ]);
  });

  it("reopens both sockets on reconnect()", () => {
    const { chat, telemetry } = renderProvider();
    chat.connect.mockClear();
    telemetry.connect.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "reconnect" }));

    expect(chat.connect).toHaveBeenCalledTimes(1);
    expect(telemetry.connect).toHaveBeenCalledTimes(1);
  });

  it("tells a subscriber about every open, including one before it subscribed", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const early = vi.fn();
    const late = vi.fn();
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <ConnectionProvider>
          <Subscriber onOnline={early} />
        </ConnectionProvider>
      </QueryClientProvider>,
    );
    const chat = chats[0] as FakeSocket;
    expect(early).not.toHaveBeenCalled();

    act(() => chat.onstatus("online"));
    expect(early).toHaveBeenCalledTimes(1);

    // The Room mounts behind a gate, which can resolve after the handshake.
    rerender(
      <QueryClientProvider client={client}>
        <ConnectionProvider>
          <Subscriber onOnline={early} />
          <Subscriber onOnline={late} />
        </ConnectionProvider>
      </QueryClientProvider>,
    );
    expect(late).toHaveBeenCalledTimes(1);

    act(() => chat.onstatus("reconnecting"));
    act(() => chat.onstatus("online"));
    expect(early).toHaveBeenCalledTimes(2);
    expect(late).toHaveBeenCalledTimes(2);
  });

  it("leaves nothing of itself on the singletons when it unmounts", () => {
    const { invalidate, chat, telemetry, unmount } = renderProvider();
    act(() => chat.onstatus("online"));

    unmount();

    expect(chat.close).toHaveBeenCalledTimes(1);
    expect(telemetry.close).toHaveBeenCalledTimes(1);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    act(() => telemetry.deliver({ type: "status", detail: "redis_error" }));
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();

    // A foreground return after the unmount is nobody's business now.
    chat.connect.mockClear();
    invalidate.mockClear();
    comeBack();
    expect(chat.connect).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();

    // The close event `close()` produces lands after the cleanup. It must not
    // reach the old callbacks — the next mount starts offline, whatever a stale
    // status says.
    act(() => chat.onstatus("online"));
    const late = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ConnectionProvider>
          <Subscriber onOnline={late} />
        </ConnectionProvider>
      </QueryClientProvider>,
    );
    expect(late).not.toHaveBeenCalled();
  });

  it("refuses to be used outside the provider", () => {
    expect(() => render(<Probe />)).toThrow("useConnection outside ConnectionProvider");
  });
});
