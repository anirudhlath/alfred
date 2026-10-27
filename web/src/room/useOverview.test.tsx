import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { useLayoutEffect, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SESSION_IDLE_MS } from "@/lib/history";
import type { Overview } from "@/lib/types";
import { ConnectionProvider, useConnection } from "@/shell/ConnectionProvider";
import { overviewFixture } from "@/test/fixtures";
import { sessionIdleMs, useOverview } from "./useOverview";

// The provider's sockets, quiet: no frame and no open, so nothing but the
// overview moves the stamp in these tests.
vi.mock("@/lib/chat-socket", () => ({
  ChatSocket: class {
    onstatus = () => {};
    connect() {}
    close() {}
    listen() {
      return () => {};
    }
  },
}));
vi.mock("@/lib/telemetry-socket", () => ({
  TelemetrySocket: class {
    onstatus = () => {};
    connect() {}
    close() {}
    subscribe() {}
    listen() {
      return () => {};
    }
  },
}));

/** When set, the overview read 500s — Redis is down behind it. */
let overviewDown = false;
/** When set, each overview read stays in flight until `answer` lets it land. */
let overviewHeld = false;
/** Reads in flight, oldest first, each waiting for `answer`. */
const parked: Array<(reads: number) => void> = [];
/** Every frame the probe committed, as the Room would draw it. See `Probe`. */
const frames: string[] = [];

/** The overview the house sends, told apart by its session count. */
const overviewOf = (reads: number): Overview => ({
  ...overviewFixture,
  counts: { ...overviewFixture.counts, sessions: reads },
});

/** Lets the oldest read in flight land, with the overview `overviewOf(reads)`. */
function answer(reads: number): void {
  parked.shift()?.(reads);
}

function renderOverview(enabled = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, ...renderHook(() => useOverview(enabled), { wrapper }) };
}

/**
 * The overview beside the stamp, as the Room's status line draws them: the
 * overview's session count, then the stamp's time. Logged at every commit,
 * which is every frame React could paint.
 */
function Probe() {
  const { data } = useOverview();
  const { lastTrueAt } = useConnection();
  const read = data ? data.counts.sessions : "none";
  const frame = `${read} @ ${lastTrueAt ? lastTrueAt.getTime() : "none"}`;
  useLayoutEffect(() => void frames.push(frame));
  return <span data-testid="frame">{frame}</span>;
}

function renderRoom() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <Probe />
      </ConnectionProvider>
    </QueryClientProvider>,
  );
  return { client };
}

/** Give react-query's batched notifications (a `setTimeout(0)`) their turn. */
const tick = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));

describe("useOverview", () => {
  beforeEach(() => {
    overviewDown = false;
    overviewHeld = false;
    parked.length = 0;
    frames.length = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        expect(String(input)).toBe("/api/admin/overview");
        if (overviewDown) return new Response('{"detail":"redis is down"}', { status: 500 });
        if (overviewHeld) {
          return new Promise<Response>((resolve) =>
            parked.push((reads) =>
              resolve(new Response(JSON.stringify(overviewOf(reads)), { status: 200 })),
            ),
          );
        }
        return new Response(JSON.stringify(overviewFixture), { status: 200 });
      }),
    );
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("stamps last-true on every successful read: the house answered", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(1_000);
    renderRoom();
    const sessions = overviewFixture.counts.sessions;
    expect(await screen.findByText(`${sessions} @ 1000`)).toBeInTheDocument();
  });

  // A poll under way when the phone comes back to the foreground is cancelled
  // and asked again (`invalidateQueries` in `ConnectionProvider`, with data
  // already read). Its reply still arrives. Stamped then, it moved "last true"
  // with the previous overview still drawn under it, for as long as the read
  // asked again took to land: a stamp for an overview nobody was shown.
  it("leaves the stamp alone for a reply that was cancelled and asked again", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    overviewHeld = true;
    const { client } = renderRoom();
    await waitFor(() => expect(parked).toHaveLength(1));
    vi.setSystemTime(1_000);
    await act(async () => answer(1));
    expect(await screen.findByText("1 @ 1000")).toBeInTheDocument();

    act(() => void client.refetchQueries({ queryKey: ["overview"] }));
    await waitFor(() => expect(parked).toHaveLength(1));
    act(() => void client.invalidateQueries({ queryKey: ["overview"] }));
    await waitFor(() => expect(parked).toHaveLength(2));

    // The cancelled read's reply.
    vi.setSystemTime(2_000);
    await act(async () => answer(2));
    await tick();
    expect(screen.getByTestId("frame")).toHaveTextContent("1 @ 1000");

    vi.setSystemTime(3_000);
    await act(async () => answer(3));
    expect(await screen.findByText("3 @ 3000")).toBeInTheDocument();
    // And no frame in between drew a stamp beside an overview it did not come with.
    expect([...new Set(frames)]).toEqual(["none @ none", "1 @ 1000", "3 @ 3000"]);
  });

  it("does not take a write to the key for the house answering", async () => {
    // Only the house's own reply is proof it is there.
    overviewHeld = true;
    const { client } = renderRoom();
    act(() => client.setQueryData(["overview"], overviewOf(7)));
    expect(await screen.findByText("7 @ none")).toBeInTheDocument();
    await tick();
    expect(screen.getByTestId("frame")).toHaveTextContent("7 @ none");
  });

  it("reads what the key already holds without asking for it again", async () => {
    // What the Memory bench's scratchpad does with the Librarian's schedule:
    // a disabled observer fetches nothing and still sees whoever is polling.
    const { result, client } = renderOverview(false);
    expect(result.current.data).toBeUndefined();
    expect(fetch).not.toHaveBeenCalled();
    client.setQueryData(["overview"], overviewFixture);
    await waitFor(() => expect(result.current.data).toEqual(overviewFixture));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("leaves last-true alone when the read fails", async () => {
    overviewDown = true;
    const { client } = renderRoom();
    await waitFor(() => expect(client.getQueryState(["overview"])?.status).toBe("error"));
    await tick();
    expect(screen.getByTestId("frame")).toHaveTextContent("none @ none");
  });
});

describe("sessionIdleMs", () => {
  it("reads the server's idle timeout in minutes", () => {
    expect(sessionIdleMs({ ...overviewFixture, session: { idle_minutes: 10 } })).toBe(600_000);
  });

  it("falls back to the default before the overview has answered", () => {
    expect(sessionIdleMs(undefined)).toBe(SESSION_IDLE_MS);
  });

  // The house always sends `session`; a cached shell can still meet a server
  // from before it did.
  it("falls back to the default when the overview has no session at all", () => {
    const overview = { ...overviewFixture, session: undefined as unknown as Overview["session"] };
    expect(sessionIdleMs(overview)).toBe(SESSION_IDLE_MS);
  });

  // `Infinity` last: a window that never breaks is a socket that never rotates.
  it.each([0, -5, Number.NaN, Number.POSITIVE_INFINITY])(
    "falls back to the default for %s minutes",
    (minutes) => {
      expect(sessionIdleMs({ ...overviewFixture, session: { idle_minutes: minutes } })).toBe(
        SESSION_IDLE_MS,
      );
    },
  );
});
