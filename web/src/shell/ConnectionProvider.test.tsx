import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
    connect = vi.fn();
    close = vi.fn();
    subscribe = vi.fn();
    constructor() {
      telemetries.push(this);
    }
    listen(): () => void {
      return () => {};
    }
  }
  return { TelemetrySocket };
});

function Probe() {
  const { online, lastTrueAt, chatStatus } = useConnection();
  return (
    <div>
      <span data-testid="online">{String(online)}</span>
      <span data-testid="status">{chatStatus}</span>
      <span data-testid="last-true">{lastTrueAt ? "stamped" : "none"}</span>
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
  render(
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <Probe />
      </ConnectionProvider>
    </QueryClientProvider>,
  );
  return { invalidate, chat: chats[0] as FakeSocket, telemetry: telemetries[0] as FakeSocket };
}

beforeEach(() => {
  // One module load means one pair of singletons for the whole file — which is the
  // behaviour under test, so reset their spies rather than expecting new instances.
  (chats[0] as FakeSocket | undefined)?.connect.mockClear();
  (telemetries[0] as FakeSocket | undefined)?.connect.mockClear();
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

    act(() => chat.onstatus("online"));

    expect(screen.getByTestId("online")).toHaveTextContent("true");
    expect(screen.getByTestId("status")).toHaveTextContent("online");
    expect(screen.getByTestId("last-true")).toHaveTextContent("stamped");

    act(() => chat.onstatus("reconnecting"));
    expect(screen.getByTestId("online")).toHaveTextContent("false");
  });

  it("stamps last-true on every frame from the house", () => {
    const { chat } = renderProvider();
    act(() => chat.deliver({ type: "response", text: "Quite so, sir.", session_id: "s_1" }));
    expect(screen.getByTestId("last-true")).toHaveTextContent("stamped");
  });

  it("turns a 4001 close into the expired gate", () => {
    const expired = vi.fn();
    const off = authEvents.on("expired", expired);
    const { chat } = renderProvider();

    act(() => chat.onstatus("unauthorized"));

    expect(expired).toHaveBeenCalled();
    off();
  });

  it("reconnects and re-reads everything on the way back from the background", () => {
    const { invalidate, chat } = renderProvider();
    chat.connect.mockClear();
    invalidate.mockClear();

    act(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(chat.connect).toHaveBeenCalled();
    const keys = invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toEqual([
      '["overview"]',
      '["room-history"]',
      '["pending-actions"]',
      '["deferred"]',
    ]);
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

  it("refuses to be used outside the provider", () => {
    expect(() => render(<Probe />)).toThrow("useConnection outside ConnectionProvider");
  });
});
