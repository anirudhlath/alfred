import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatServerMessage, TelemetryMessage } from "@/lib/types";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import {
  actionResultFixture,
  pendingActionFixture,
  secondPendingActionFixture,
} from "@/test/fixtures";
import { DoorProvider, useDoor } from "./DoorProvider";

const { chats, telemetries } = vi.hoisted(() => ({
  chats: [] as unknown[],
  telemetries: [] as unknown[],
}));

vi.mock("@/lib/chat-socket", () => {
  class ChatSocket {
    onstatus: (status: string) => void = () => {};
    listeners = new Set<(msg: ChatServerMessage) => void>();
    connect = vi.fn();
    close = vi.fn();
    sendText = vi.fn(() => true);
    sendAudio = vi.fn(() => true);
    constructor() {
      chats.push(this);
    }
    listen(fn: (msg: ChatServerMessage) => void): () => void {
      this.listeners.add(fn);
      return () => void this.listeners.delete(fn);
    }
    deliver(msg: ChatServerMessage): void {
      for (const fn of [...this.listeners]) fn(msg);
    }
  }
  return { ChatSocket };
});

vi.mock("@/lib/telemetry-socket", () => {
  class TelemetrySocket {
    onstatus: (status: string) => void = () => {};
    listeners = new Set<(msg: TelemetryMessage) => void>();
    connect = vi.fn();
    close = vi.fn();
    subscribe = vi.fn();
    constructor() {
      telemetries.push(this);
    }
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

interface FakeChat {
  deliver: (msg: ChatServerMessage) => void;
}
interface FakeTelemetry {
  subscribe: ReturnType<typeof vi.fn>;
  deliver: (msg: TelemetryMessage) => void;
}

let pendingBody: unknown = { actions: [] };
let getStatus = 200;
let confirmStatus = 200;
const calls: string[] = [];

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/api/actions/pending")
        return new Response(JSON.stringify(pendingBody), { status: 200 });
      if (url.endsWith("/confirm"))
        return new Response(
          confirmStatus === 200
            ? '{"status":"confirmed"}'
            : '{"detail":"Pending action not found or expired"}',
          { status: confirmStatus },
        );
      if (url.startsWith("/api/actions/"))
        return new Response(
          getStatus === 200
            ? JSON.stringify(pendingActionFixture)
            : '{"detail":"Pending action not found or expired"}',
          { status: getStatus },
        );
      return new Response("{}", { status: 404 });
    }),
  );
}

function Probe() {
  const door = useDoor();
  return (
    <div>
      <span data-testid="phases">
        {door.actions.map((item) => `${item.action.request_id}:${item.phase}`).join(",")}
      </span>
      <span data-testid="pending">{door.pending.length}</span>
      <span data-testid="open">{door.open ? door.current?.action.request_id : "closed"}</span>
      <span data-testid="now">{door.now}</span>
      <button type="button" onClick={() => door.arrived(pendingActionFixture)}>
        arrive
      </button>
      <button type="button" onClick={() => door.openAction("a91f3c2e")}>
        open
      </button>
      <button type="button" onClick={door.close}>
        close
      </button>
      <button type="button" onClick={() => door.confirm("a91f3c2e")}>
        confirm
      </button>
    </div>
  );
}

function renderDoor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <DoorProvider>
          <Probe />
        </DoorProvider>
      </ConnectionProvider>
    </QueryClientProvider>,
  );
  return {
    chat: chats.at(-1) as FakeChat,
    telemetry: telemetries.at(-1) as FakeTelemetry,
  };
}

const phases = () => screen.getByTestId("phases").textContent;
const now = () => Number(screen.getByTestId("now").textContent);

/** The app comes back to the foreground. */
function comeBack(): void {
  act(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

function deliverResult(telemetry: FakeTelemetry, msg: Partial<TelemetryMessage>): void {
  act(() =>
    telemetry.deliver({
      type: "entry",
      stream: "home_action_results",
      id: "1757000000000-0",
      event: actionResultFixture as unknown as Record<string, unknown>,
      ...msg,
    } as TelemetryMessage),
  );
}

beforeEach(() => {
  // The sockets are module singletons, so their spies outlive a test.
  (telemetries.at(-1) as FakeTelemetry | undefined)?.subscribe.mockClear();
  calls.length = 0;
  pendingBody = { actions: [] };
  getStatus = 200;
  confirmStatus = 200;
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-09-07T07:42:00Z"));
  stubFetch();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("DoorProvider", () => {
  it("loads whatever is already waiting, oldest first", async () => {
    pendingBody = { actions: [secondPendingActionFixture, pendingActionFixture] };
    renderDoor();

    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending,7c2e0b1d:pending"));
    expect(screen.getByTestId("pending")).toHaveTextContent("2");
  });

  it("subscribes to home_action_results and nothing else", () => {
    const { telemetry } = renderDoor();
    expect(telemetry.subscribe).toHaveBeenCalledWith(["home_action_results"]);
    expect(telemetry.subscribe).toHaveBeenCalledTimes(1);
  });

  it("reads the list again when the app comes back", async () => {
    renderDoor();
    await waitFor(() => expect(calls).toContain("GET /api/actions/pending"));
    expect(phases()).toBe("");

    pendingBody = { actions: [pendingActionFixture] };
    comeBack();

    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));
  });

  it("fetches the action a confirmation notification names", async () => {
    const { chat } = renderDoor();

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Confirmation required",
        body: "Alfred wants to run 'home.lock_unlock' on home-service — confirm?",
        urgency: "urgent",
        notification_id: "ntf-1",
        metadata: { pending_action_id: "a91f3c2e" },
      }),
    );

    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));
    expect(calls).toContain("GET /api/actions/a91f3c2e");
  });

  it("ignores a notification with no action on it", async () => {
    const { chat } = renderDoor();

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Bins go out tonight",
        body: "Collection moved to Friday.",
        urgency: "important",
        notification_id: "ntf-2",
        metadata: {},
      }),
    );

    await waitFor(() => expect(calls.some((call) => call.includes("/api/actions/a"))).toBe(false));
    expect(phases()).toBe("");
  });

  it("ignores an action that has already gone by the time we ask", async () => {
    getStatus = 404;
    const { chat } = renderDoor();

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Confirmation required",
        body: "…",
        urgency: "urgent",
        notification_id: "ntf-3",
        metadata: { pending_action_id: "a91f3c2e" },
      }),
    );

    await waitFor(() => expect(calls).toContain("GET /api/actions/a91f3c2e"));
    expect(phases()).toBe("");
  });

  it("applies only on a result carrying the same request id", async () => {
    pendingBody = { actions: [pendingActionFixture] };
    const { telemetry } = renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    act(() =>
      telemetry.deliver({
        type: "entry",
        stream: "home_action_results",
        id: "1757000000000-0",
        event: { ...actionResultFixture, request_id: "0000" } as unknown as Record<string, unknown>,
      }),
    );
    expect(phases()).toBe("a91f3c2e:pending");

    act(() =>
      telemetry.deliver({
        type: "entry",
        stream: "home_action_results",
        id: "1757000000001-0",
        event: actionResultFixture as unknown as Record<string, unknown>,
      }),
    );
    expect(phases()).toBe("a91f3c2e:applied");
  });

  it("hears nothing from other streams, or from a result with no request id", async () => {
    pendingBody = { actions: [pendingActionFixture] };
    const { telemetry } = renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    deliverResult(telemetry, { stream: "events" });
    expect(phases()).toBe("a91f3c2e:pending");

    deliverResult(telemetry, { event: { status: "success" } });
    expect(phases()).toBe("a91f3c2e:pending");
  });

  it("confirms, and calls it queued rather than applied", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    pendingBody = { actions: [pendingActionFixture] };
    renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    await user.click(screen.getByRole("button", { name: "confirm" }));

    await waitFor(() => expect(phases()).toBe("a91f3c2e:queued"));
    expect(calls).toContain("POST /api/actions/a91f3c2e/confirm");
  });

  it("marks a 404 confirm as already answered", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    confirmStatus = 404;
    pendingBody = { actions: [pendingActionFixture] };
    renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    await user.click(screen.getByRole("button", { name: "confirm" }));

    await waitFor(() => expect(phases()).toBe("a91f3c2e:answered"));
  });

  it("leaves a confirm the server refused for any other reason pending", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    confirmStatus = 500;
    pendingBody = { actions: [pendingActionFixture] };
    renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    await user.click(screen.getByRole("button", { name: "confirm" }));

    await waitFor(() => expect(calls).toContain("POST /api/actions/a91f3c2e/confirm"));
    expect(phases()).toBe("a91f3c2e:pending");
  });

  it("expires a pending action when its fuse runs out", async () => {
    pendingBody = { actions: [pendingActionFixture] };
    renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    // 07:42 → 07:47: past the 07:46 expiry.
    await act(async () => {
      vi.advanceTimersByTime(5 * 60_000);
    });

    expect(phases()).toBe("a91f3c2e:expired");
  });

  it("steps the clock the moment something starts counting", () => {
    renderDoor();
    const mounted = now();

    // A minute of nothing pending: no clock runs, so `now` is still the mount read.
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(now()).toBeLessThan(mounted + 1000);

    // The first frame of an approval must not be drawn from that stale read. A
    // bare DOM click keeps the arrival synchronous, so this reads the very frame.
    act(() => screen.getByRole("button", { name: "arrive" }).click());
    expect(phases()).toBe("a91f3c2e:pending");
    expect(now()).toBeGreaterThanOrEqual(mounted + 60_000);
  });

  it("stops the clock once the approval is queued", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    pendingBody = { actions: [pendingActionFixture] };
    renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    await user.click(screen.getByRole("button", { name: "confirm" }));
    await waitFor(() => expect(phases()).toBe("a91f3c2e:queued"));

    // The handoff freezes the fuse where the confirmation left it.
    const frozen = now();
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(now()).toBe(frozen);
  });

  it("opens and closes one action at a time", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    pendingBody = { actions: [pendingActionFixture] };
    renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    expect(screen.getByTestId("open")).toHaveTextContent("closed");

    await user.click(screen.getByRole("button", { name: "open" }));
    expect(screen.getByTestId("open")).toHaveTextContent("a91f3c2e");

    await user.click(screen.getByRole("button", { name: "close" }));
    expect(screen.getByTestId("open")).toHaveTextContent("closed");
  });

  it("refuses to be used outside the provider", () => {
    expect(() => render(<Probe />)).toThrow("useDoor outside DoorProvider");
  });
});
