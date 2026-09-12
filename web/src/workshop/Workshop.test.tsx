import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StreamPage, TelemetryMessage } from "@/lib/types";
import { WhySheet } from "@/sheets/WhySheet";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { overviewFixture, reflexObservationsPage } from "@/test/fixtures";
import { Workshop } from "./Workshop";

const sockets = vi.hoisted(() => ({ telemetryUp: true, telemetries: [] as unknown[] }));

/**
 * How many rows have been summarised. `memo` on `Workshop` exists so the
 * Room's unrelated re-renders stop above the bench instead of walking every
 * mounted `EventRow` — all of them unmemoised, every one re-running
 * `summarise`. Counting the calls is how that is visible from outside.
 */
const { summarised } = vi.hoisted(() => ({ summarised: { count: 0 } }));

vi.mock("@/lib/streams", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/streams")>();
  return {
    ...actual,
    summarise: (...args: Parameters<typeof actual.summarise>) => {
      summarised.count += 1;
      return actual.summarise(...args);
    },
  };
});

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
    constructor() { sockets.telemetries.push(this); }
    connect() { this.onstatus(sockets.telemetryUp ? "online" : "offline"); }
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
  /** The provider's own `setTelemetryStatus`, so a test can take the pump down. */
  onstatus: (status: string) => void;
  deliver: (msg: TelemetryMessage) => void;
}

/**
 * The socket `ConnectionProvider` holds. A module-level singleton built once
 * per module load — i.e. once for the whole file — so this is always the one
 * the mounted tree is listening to.
 */
const telemetry = (): FakeTelemetry => sockets.telemetries.at(-1) as FakeTelemetry;

/** An `events` frame, which is what a live row on this bench is made of. */
function frame(id: string): TelemetryMessage {
  return { type: "entry", stream: "events", id, event: { event_type: "state_changed", source: "bus" } };
}

const empty: StreamPage = { entries: [], next_before: null };

/** The acted observation on `media_player.tv`, found by what it is rather than where it sits. */
const observation = reflexObservationsPage.entries.find(
  (entry) => (entry.event as { observation_id?: string }).observation_id === "obs-1",
);
/** The same row, non-optional, for the sheet the Escape test opens over the Workshop. */
const anchorEntry = reflexObservationsPage.entries[2];

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/admin/overview") return new Response(JSON.stringify(overviewFixture), { status: 200 });
      if (url === "/api/admin/streams/reflex_observations?count=50") {
        return new Response(JSON.stringify(reflexObservationsPage), { status: 200 });
      }
      return new Response(JSON.stringify(empty), { status: 200 });
    }),
  );
}

/**
 * The Workshop under a parent that re-renders on its own: the Room, whose chat
 * frames, 30 s overview poll and once-a-second Door fuse all do exactly this.
 * Both props it passes are stable, which is what `memo` needs to bite.
 */
function mountInRoom() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = vi.fn();
  const onWhy = vi.fn();
  const bump = { fire: () => {} };
  function Room() {
    const [, setTick] = useState(0);
    bump.fire = () => setTick((tick) => tick + 1);
    return <Workshop open onClose={onClose} onWhy={onWhy} />;
  }
  render(
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <Room />
      </ConnectionProvider>
    </QueryClientProvider>,
  );
  return { rerenderRoom: () => act(() => bump.fire()) };
}

function mount(open: boolean, onClose = vi.fn(), onWhy = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (isOpen: boolean) => (
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <Workshop open={isOpen} onClose={onClose} onWhy={onWhy} />
      </ConnectionProvider>
    </QueryClientProvider>
  );
  const view = render(tree(open));
  return { ...view, onClose, onWhy, reopen: (isOpen: boolean) => view.rerender(tree(isOpen)) };
}

beforeEach(() => {
  sockets.telemetryUp = true;
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(2026, 8, 7, 21, 14, 0));
  stubFetch();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Workshop", () => {
  it("rises under the sheets as the Workshop, and Room closes it", () => {
    const { onClose } = mount(true);
    const dialog = screen.getByRole("dialog", { name: "Workshop" });
    expect(dialog.className).toContain("z-10");
    fireEvent.click(screen.getByRole("button", { name: "Room" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("opens on Activity; the other benches say they are not built", () => {
    mount(true);
    expect(screen.getByRole("tab", { name: "Activity" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Pause feed" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    expect(screen.getByText("not built yet · phase 3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause feed" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByRole("button", { name: "Pause feed" })).toBeInTheDocument();
  });

  it("says live with the overview's rate", async () => {
    mount(true);
    await waitFor(() => expect(screen.getByTestId("workshop-status")).toHaveTextContent("live · 2.1 ev/s"));
  });

  it("says paused, and counts what the hold is holding", () => {
    mount(true);
    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    expect(screen.getByTestId("workshop-status")).toHaveTextContent("paused · 0 new");

    // `paused · 0 new` on its own is true of a header that hardcodes both
    // halves, which is what it used to be asserted against. Two frames arrive
    // behind the hold and the count has to move.
    act(() => {
      telemetry().deliver(frame("1788815700000-0"));
      telemetry().deliver(frame("1788815701000-0"));
    });
    expect(screen.getByTestId("workshop-status")).toHaveTextContent("paused · 2 new");
    // The same number, in the one place a screen reader hears it.
    expect(screen.getByRole("button", { name: "Resume · 2 new" })).toBeInTheDocument();
  });

  it("says last true --:-- when the pump has never been up", () => {
    sockets.telemetryUp = false;
    mount(true);
    // The *feed's* stamp, not the connection's: the chat socket is up and has
    // set `lastTrueAt` to 21:14, but the pump has never been live, and the
    // header may not say a thing the banner below it contradicts.
    expect(screen.getByTestId("workshop-status")).toHaveTextContent("last true --:-- · not live");
    expect(screen.getByRole("status", { name: "Feed status" })).toHaveTextContent(
      "Feed has not been live yet. Nothing below is live.",
    );
    // Pause is honoured underneath, but the status line still says not live.
    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    expect(screen.getByTestId("workshop-status")).toHaveTextContent("last true --:-- · not live");
  });

  it("names the minute the pump went quiet once it has been live", () => {
    // `--:--` above is the same line whether the header reads the clock or
    // hardcodes the dashes. This is the branch beside it: a feed that *was*
    // live has a stamp, and the header has to print it.
    mount(true);
    expect(screen.getByTestId("workshop-status")).toHaveTextContent("live ·");

    vi.setSystemTime(new Date(2026, 8, 7, 21, 40, 0));
    act(() => telemetry().onstatus("offline"));

    expect(screen.getByTestId("workshop-status")).toHaveTextContent("last true 21:40 · not live");
    expect(screen.getByRole("status", { name: "Feed status" })).toHaveTextContent(
      "Feed stopped at 21:40. Nothing below is live.",
    );
  });

  it("does not report a rate it has never read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 500 })));
    mount(true);
    // `evs({})` is a bare `0`, which would call a house it cannot reach silent.
    await waitFor(() => expect(screen.getByTestId("workshop-status")).toHaveTextContent("live · — ev/s"));
  });

  it("reads the streams once open and hands a reflex row's why up", async () => {
    const { onWhy } = mount(true);
    await screen.findByText("observed media_player.tv · acted");
    fireEvent.click(screen.getByText("observed media_player.tv · acted"));
    fireEvent.click(screen.getByRole("button", { name: "Why · causal thread" }));
    expect(onWhy).toHaveBeenCalledWith({ stream: "reflex_observations", entry: observation });
  });

  it("hangs the bench off its own tab", () => {
    mount(true);
    const panel = screen.getByRole("tabpanel");
    const activity = screen.getByRole("tab", { name: "Activity" });
    expect(activity).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", activity.id);
    expect(panel).toContainElement(screen.getByRole("button", { name: "Pause feed" }));

    // The APG makes the panel's tab stop optional when it holds something
    // focusable and required when it does not — and three of the four benches
    // are a bare `<p>` with nothing to reach. Always on, so the rule does not
    // change under the reader as they walk the switcher.
    expect(panel).toHaveAttribute("tabindex", "0");

    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      screen.getByRole("tab", { name: "Memory" }).id,
    );
    expect(screen.getByRole("tabpanel")).toHaveAttribute("tabindex", "0");
  });

  it("keeps the Activity bench's state through a trip to another bench", () => {
    mount(true);
    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    // Every hook lives in `WorkshopPanel`, above the bench that is swapped out,
    // which is the whole reason it is shaped that way: the hold survives, and
    // so do the rows behind it.
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("closes on Escape, which is all a standalone app has", () => {
    const { onClose } = mount(true);
    fireEvent.keyDown(screen.getByRole("dialog", { name: "Workshop" }), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("leaves Escape to a sheet opened over it", async () => {
    // The test above passes with the listener moved to `document`, so it proves
    // nothing about the scoping `Workshop.tsx` spends six lines on. This is
    // what that scoping is for: `Sheet` portals to `document.body`, a sibling
    // of the Workshop's dialog rather than a descendant, so an Escape meant for
    // the sheet must not dismiss the surface underneath it as well.
    const onClose = vi.fn();
    const sheetClose = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ConnectionProvider>
          <Workshop open onClose={onClose} onWhy={vi.fn()} />
          <WhySheet anchor={{ stream: "reflex_observations", entry: anchorEntry }} onClose={sheetClose} />
        </ConnectionProvider>
      </QueryClientProvider>,
    );
    await screen.findByText(/^searched 8 streams/);

    fireEvent.keyDown(screen.getByRole("dialog", { name: "Why Alfred did that" }), { key: "Escape" });
    expect(sheetClose).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("stops the Room's own re-renders above the bench", async () => {
    // What `memo` on `Workshop` is for (commit 09c6598), and it was removable
    // with the whole file green. Without it a chat frame, the 30 s overview
    // poll or the Door's once-a-second `now` walks `WorkshopPanel` ->
    // `ActivityBench` -> every mounted `EventRow`, all unmemoised, every one
    // re-running `summarise` — 400 rows per stream, eight streams, and no
    // ceiling at all once `↑ older` has been pressed.
    const { rerenderRoom } = mountInRoom();
    await screen.findByText("observed media_player.tv · acted");

    const before = summarised.count;
    expect(before).toBeGreaterThan(0);
    rerenderRoom();
    rerenderRoom();
    expect(summarised.count).toBe(before);
  });

  it("stays for its leave, then unmounts", () => {
    const { reopen } = mount(true);
    reopen(false);
    expect(screen.getByRole("dialog").className).toContain("rise-out");
    act(() => vi.advanceTimersByTime(400));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
