import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StreamPage, TelemetryMessage } from "@/lib/types";
import { WhySheet } from "@/sheets/WhySheet";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { QUERY_DEFAULTS } from "@/shell/QueryProvider";
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
 * The socket `ConnectionProvider` holds. The fake constructor pushes every
 * instance it builds onto `sockets.telemetries`, which is module-level and so
 * shared by the whole file: the *last* one is the one this test's tree is
 * listening to, and the ones before it belong to trees already unmounted.
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

/** Every path this render has asked for, in order — see "asks for nothing". */
const asked: string[] = [];

/**
 * Paths `stubFetch` should refuse, mapped to the `detail` it answers with.
 * Empty for all but the read-error test: the rest of this file is about a
 * Workshop whose reads all landed, and a bench whose spine read came back
 * refused is the only thing that puts a word in the region below.
 */
const refused = new Map<string, string>();

/**
 * The app's own query policy, not a test-only one. Three trees in this file
 * built a no-retry client instead, which is a *different* client from the one
 * the Workshop ships inside (`QueryProvider`): one attempt where the app makes
 * two, and a zero stale time where the app holds a read for ten seconds. "Asks
 * for nothing" and "one read per bench" are claims about that policy, so they
 * are measured against it. Only the backoff is the harness's — a real 1 s wait
 * between two attempts buys the assertions nothing but seconds.
 */
const makeClient = (): QueryClient =>
  new QueryClient({
    defaultOptions: { ...QUERY_DEFAULTS, queries: { ...QUERY_DEFAULTS.queries, retryDelay: 0 } },
  });

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      asked.push(url);
      const refusal = refused.get(url);
      if (refusal !== undefined) {
        return new Response(JSON.stringify({ detail: refusal }), { status: 503 });
      }
      if (url === "/api/admin/overview") return new Response(JSON.stringify(overviewFixture), { status: 200 });
      if (url === "/api/admin/streams/reflex_observations?count=50") {
        return new Response(JSON.stringify(reflexObservationsPage), { status: 200 });
      }
      // The one read on these benches that answers a **bare array** rather
      // than an envelope (`lib/system.ts`, `fetchIntegrations`).
      if (url === "/api/integrations") return new Response("[]", { status: 200 });
      // Every other read on the three new benches: a body with none of the
      // keys they look for, which each fetcher reads as an empty list. The
      // benches mount and say they have nothing, which is all these tests ask
      // of them — what each one does with a full list is its own file's.
      return new Response(JSON.stringify(empty), { status: 200 });
    }),
  );
}

/**
 * The Workshop's own `tabpanel`. Found by document order rather than by role
 * alone: the Memory bench has a `tabpanel` of its own for its four sub-tabs,
 * and it is a descendant of this one.
 */
function workshopPanel(): HTMLElement {
  return screen.getAllByRole("tabpanel")[0];
}

/**
 * The Workshop under a parent that re-renders on its own: the Room, whose chat
 * frames, 30 s overview poll and once-a-second Door fuse all do exactly this.
 * All four props this stand-in passes are stable — `open`, and three callbacks
 * built once — which is what `memo` needs to bite. The real Room has to earn
 * that with `useCallback`, and `App.test.tsx` is where it is measured; this
 * file proves only that the `memo` itself works.
 */
function mountInRoom() {
  const client = makeClient();
  const onClose = vi.fn();
  const onWhy = vi.fn();
  const onHeld = vi.fn();
  const bump = { fire: () => {} };
  function Room() {
    const [, setTick] = useState(0);
    // In an effect, not the render body: assigning there is a side effect on
    // every pass, and React may render a component twice before committing it.
    useEffect(() => {
      bump.fire = () => setTick((tick) => tick + 1);
    }, []);
    return <Workshop open onClose={onClose} onWhy={onWhy} onHeld={onHeld} />;
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

function mount(open: boolean, onClose = vi.fn(), onWhy = vi.fn(), onHeld = vi.fn()) {
  const client = makeClient();
  const tree = (isOpen: boolean) => (
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <Workshop open={isOpen} onClose={onClose} onWhy={onWhy} onHeld={onHeld} />
      </ConnectionProvider>
    </QueryClientProvider>
  );
  const view = render(tree(open));
  return { ...view, onClose, onWhy, onHeld, reopen: (isOpen: boolean) => view.rerender(tree(isOpen)) };
}

beforeEach(() => {
  sockets.telemetryUp = true;
  asked.length = 0;
  refused.clear();
  // Module-level, so without this the memo test's `before` already carries
  // every row the tests ahead of it summarised, and `toBeGreaterThan(0)` is
  // true before that test has rendered a thing.
  summarised.count = 0;
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
    // The whole token: `toContain("z-10")` is also true of `z-100`, which is a
    // different layer and would put the Workshop over the sheets it must sit
    // under (`shell/Layer.ts`).
    expect(dialog.classList.contains("z-10")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Room" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("opens on Activity, and each tab brings up the bench it names", async () => {
    mount(true);
    expect(screen.getByRole("tab", { name: "Activity" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Pause feed" })).toBeInTheDocument();

    // Each bench found by the one control only it has, rather than by a
    // heading: the tablist is Memory's, the kind chips are Triggers', and the
    // switch is System's.
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    expect(screen.getByRole("tablist", { name: "Memory" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause feed" })).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Triggers" }));
    expect(screen.getByRole("group", { name: "Trigger kinds" })).toBeInTheDocument();
    expect(screen.queryByRole("tablist", { name: "Memory" })).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "System" }));
    expect(screen.getByRole("switch", { name: "Do-not-disturb" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Trigger kinds" })).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByRole("button", { name: "Pause feed" })).toBeInTheDocument();
    // The System bench's five reads land after the bench has gone; letting them
    // settle here keeps the noise out of the next test.
    await waitFor(() => expect(asked).toContain("/api/auth/sessions"));
  });

  it("keeps a bench's own state through a trip to another bench", async () => {
    mount(true);
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    fireEvent.click(screen.getByRole("tab", { name: "Semantic" }));
    expect(screen.getByRole("tab", { name: "Semantic" })).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));

    // The sub-tab lives in `useMemory`, which is called in `WorkshopPanel`
    // above the bench that was swapped out — the convention every hook here
    // follows, and the reason a reader can leave a bench mid-thought.
    expect(await screen.findByRole("tab", { name: "Semantic" })).toHaveAttribute("aria-selected", "true");
  });

  it("unmounts the bench nobody is looking at, and its clock with it", async () => {
    mount(true);
    fireEvent.click(screen.getByRole("tab", { name: "System" }));
    // Two `setInterval`s live on this bench: the 1 Hz health stamp and the
    // minute tick that dates its session rows. A `switch` that returned all
    // four benches and hid three would leave both running behind a panel
    // nobody can see, for the life of the Workshop.
    expect(await screen.findByTestId("health-stamp")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.queryByTestId("health-stamp")).toBeNull();
  });

  it("asks for nothing on behalf of a bench nobody has opened, and for what the open one needs", async () => {
    mount(true);
    await waitFor(() => expect(asked).toContain("/api/admin/overview"));
    // Every hook is called on every render; each one is gated on its own bench.
    expect(asked).not.toContain("/api/admin/memory/episodic");
    expect(asked).not.toContain("/api/admin/triggers");
    expect(asked).not.toContain("/api/auth/sessions");

    // The other half of each gate. A gate stuck shut satisfies the three lines
    // above and leaves every bench blank for ever, which is why both halves are
    // asserted rather than the closed one alone.
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    await waitFor(() => expect(asked).toContain("/api/admin/memory/episodic"));
    // And no further: `useMemory` gates each sub-tab separately, so arriving on
    // the bench reads the tab that is showing and not the other three — and no
    // other bench's hook wakes up because the reader moved. A gate wired to the
    // wrong bench reads at the wrong time and never at the right one, which
    // both halves above pass on their own.
    expect(asked).not.toContain("/api/admin/memory/semantic");
    expect(asked).not.toContain("/api/admin/triggers");
    expect(asked).not.toContain("/api/auth/sessions");

    fireEvent.click(screen.getByRole("tab", { name: "Triggers" }));
    await waitFor(() => expect(asked).toContain("/api/admin/triggers"));

    fireEvent.click(screen.getByRole("tab", { name: "System" }));
    await waitFor(() => expect(asked).toContain("/api/auth/sessions"));
  });

  it("announces the connection from the header, whichever bench is up", async () => {
    mount(true);
    const state = screen.getByTestId("workshop-state");
    expect(state).toHaveAttribute("role", "status");
    expect(state).toHaveAttribute("aria-live", "polite");

    // The point of moving it: on three of the four benches nothing else on
    // screen says the pump has stopped, because the Activity banner that used
    // to say it is unmounted with its bench.
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    vi.setSystemTime(new Date(2026, 8, 7, 21, 40, 0));
    act(() => telemetry().onstatus("offline"));
    const onMemory = screen.getByTestId("workshop-state");
    expect(onMemory).toHaveAttribute("aria-live", "polite");
    expect(onMemory).toHaveTextContent("last true 21:40 · not live");
    await waitFor(() => expect(asked).toContain("/api/admin/memory/episodic"));
  });

  it("keeps both numbers outside the region that speaks", async () => {
    mount(true);
    await waitFor(() =>
      expect(screen.getByTestId("workshop-status")).toHaveTextContent("live · 2.1 ev/s"),
    );
    // `role="status"` implies `aria-atomic="true"`: a change anywhere inside
    // the region re-presents the whole of it, so a rate ticking every 30 s in
    // there would have a reader hear "live" twice a minute for ever. An
    // `aria-hidden` child does not help — the mutation is still inside. The
    // numbers are a sibling, where there is nothing to diff.
    expect(screen.getByTestId("workshop-state").textContent).toBe("live");
    expect(screen.getByTestId("workshop-state")).not.toContainElement(
      screen.getByTestId("workshop-detail"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    // The held count moves with every frame — twice a second at the fixture's
    // rate. A number to watch, not news to hear, so it stays outside too.
    expect(screen.getByTestId("workshop-state").textContent).toBe("paused");
    expect(screen.getByTestId("workshop-status").textContent).toBe("paused · 0 new");
  });

  it("leaves each bench its own read-error region, and puts the refusal in it", async () => {
    // Both halves of the region, because they are announced differently: the
    // spine read is printed *and* announced, and the four section reads are
    // announced only — each already prints inside the card it belongs to. With
    // every read answering 200 the region is present and empty, and a bench
    // that had dropped the text would pass on the role alone.
    refused.set("/api/admin/overview", "The overview is unavailable.");
    refused.set("/api/auth/sessions", "Sessions could not be read.");
    mount(true);
    fireEvent.click(screen.getByRole("tab", { name: "System" }));
    // A different fact from the header's, and the one thing that would
    // otherwise go unsaid: the header speaks for the connection, this speaks
    // for a read that came back refused.
    const region = await screen.findByRole("status", { name: "Read errors" });
    await waitFor(() => expect(region.textContent).toContain("The overview is unavailable."));
    await waitFor(() => expect(region.textContent).toContain("Sessions could not be read."));
    // Shown, not merely announced — the spine's failure is the one the reader
    // who can see the bench needs, because every card below derives from it.
    expect(region.className).not.toContain("sr-only");
    expect(screen.getByTestId("workshop-state")).toHaveAttribute("role", "status");
  });

  it("hands the Held-back sheet up to the Room", async () => {
    const { onHeld } = mount(true);
    fireEvent.click(screen.getByRole("tab", { name: "System" }));
    fireEvent.click(await screen.findByRole("button", { name: /Held back/ }));
    // The sheet is the Room's — Quiet's third row is the only thing on this
    // bench that leaves it.
    expect(onHeld).toHaveBeenCalledTimes(1);
  });

  it("says live with the overview's rate, and nothing else", async () => {
    mount(true);
    // The whole line, not a substring: `toHaveTextContent` also passes on a
    // header that appends the held count to the rate.
    await waitFor(() =>
      expect(screen.getByTestId("workshop-status").textContent).toBe("live · 2.1 ev/s"),
    );
  });

  it("says paused, and counts what the hold is holding", () => {
    mount(true);
    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    expect(screen.getByTestId("workshop-status").textContent).toBe("paused · 0 new");

    // `paused · 0 new` on its own is true of a header that hardcodes both
    // halves, which is what it used to be asserted against. Two frames arrive
    // behind the hold and the count has to move.
    act(() => {
      telemetry().deliver(frame("1788815700000-0"));
      telemetry().deliver(frame("1788815701000-0"));
    });
    // Exact, because `paused · 2 new · 2.1 ev/s` — a header that stopped
    // choosing between the two numbers — contains this string too.
    expect(screen.getByTestId("workshop-status").textContent).toBe("paused · 2 new");
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
    // The bench's banner says the same thing in its own words, and no longer
    // as a second polite region — the header's is the one that speaks now.
    expect(screen.getByTestId("feed-banner")).toHaveTextContent(
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

    // Exact again: a dead pump has no rate and nothing held, and a header that
    // printed either beside `not live` would be the §5.2 contradiction the
    // ladder above exists to prevent.
    expect(screen.getByTestId("workshop-status").textContent).toBe("last true 21:40 · not live");
    expect(screen.getByTestId("feed-banner")).toHaveTextContent(
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
    const panel = workshopPanel();
    const activity = screen.getByRole("tab", { name: "Activity" });
    expect(activity).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", activity.id);
    expect(panel).toContainElement(screen.getByRole("button", { name: "Pause feed" }));

    // The stop that lands on the bench itself rather than inside it, on every
    // bench, so the keyboard path does not change under the reader as they
    // walk the switcher.
    expect(panel).toHaveAttribute("tabindex", "0");

    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    expect(workshopPanel()).toHaveAttribute("aria-labelledby", screen.getByRole("tab", { name: "Memory" }).id);
    expect(workshopPanel()).toHaveAttribute("tabindex", "0");
  });

  it("keeps the Activity bench's hold through a trip to another bench", () => {
    mount(true);
    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    // Every hook lives in `WorkshopPanel`, above the bench that is swapped out,
    // which is the whole reason it is shaped that way: the hold survives, and
    // so do the rows behind it.
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("keeps the Triggers kind filter through a trip to another bench", async () => {
    mount(true);
    fireEvent.click(screen.getByRole("tab", { name: "Triggers" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sensor" }));
    expect(screen.getByRole("button", { name: "Sensor" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("tab", { name: "System" }));
    fireEvent.click(screen.getByRole("tab", { name: "Triggers" }));
    // The filter lives in `useTriggers`, above the bench that was unmounted.
    // A reader who went to check something and came back is still looking at
    // the list they left.
    expect(await screen.findByRole("button", { name: "Sensor" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("keeps a System write's note through a trip to another bench", async () => {
    mount(true);
    fireEvent.click(screen.getByRole("tab", { name: "System" }));
    fireEvent.click(await screen.findByRole("button", { name: "Send them now" }));
    // The server confirmed the queue-up, and the stamp is of that reply — not
    // of the press (spec §5.2, and `useSystem.ts` on `drainedAt`).
    expect(await screen.findByText(/^queued 21:14 · /)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    fireEvent.click(screen.getByRole("tab", { name: "System" }));
    // Still the same note, and still the same minute: a bench that remounted
    // its own state would be back to `queued only; …` with the write it just
    // sent forgotten.
    expect(await screen.findByText(/^queued 21:14 · /)).toBeInTheDocument();
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
    const client = makeClient();
    render(
      <QueryClientProvider client={client}>
        <ConnectionProvider>
          <Workshop open onClose={onClose} onWhy={vi.fn()} onHeld={vi.fn()} />
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
