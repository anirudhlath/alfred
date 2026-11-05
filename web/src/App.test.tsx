import { defaultScheduler, notifyManager } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  deferredFixture,
  firstRunOverviewFixture,
  IPHONE_UA,
  notificationsPage,
  overviewFixture,
  pendingActionFixture,
  reflexObservationsPage,
  userRequestsPage,
  userResponsesPage,
  yesterdayRequestPage,
} from "@/test/fixtures";
import { PresenceSignal } from "@/lib/presence-signal";
import { reachContext, requestAndSubscribe } from "@/lib/push";
import { gateAnswered } from "@/lib/reach-local";
import * as sw from "@/lib/sw";
import { THEME_KEY } from "@/lib/theme";
import type { SocketStatus } from "@/lib/ws";
import App from "./App";

const socket = vi.hoisted(() => ({
  // What the fake ChatSocket reports the moment it is asked to connect. Online
  // unless a test says otherwise: the headline, the note under it, the
  // composer's placeholder and the field's colour all turn on this one word.
  status: "online" as SocketStatus,
}));

vi.mock("@/lib/chat-socket", () => ({
  ChatSocket: class {
    onstatus: (status: SocketStatus) => void = () => {};
    connect() {
      // Report from connect(), which ConnectionProvider calls after it has
      // assigned onstatus — the Room's headline depends on it.
      this.onstatus(socket.status);
    }
    close() {}
    setIdleMs() {}
    sendText() {
      return true;
    }
    sendAudio() {
      return true;
    }
    listen() {
      return () => {};
    }
  },
}));

// A passkey sign-in, which jsdom cannot perform; everything else is the real module.
const { loginPasskeyMock } = vi.hoisted(() => ({ loginPasskeyMock: vi.fn(async () => {}) }));
vi.mock("@/lib/webauthn", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/webauthn")>()),
  loginPasskey: loginPasskeyMock,
}));

// The real answers, a Safari tab in jsdom, until a test says this is the installed app.
vi.mock("@/lib/push", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/push")>();
  return {
    ...actual,
    reachContext: vi.fn(actual.reachContext),
    requestAndSubscribe: vi.fn(actual.requestAndSubscribe),
  };
});

vi.mock("@/lib/telemetry-socket", () => ({
  TelemetrySocket: class {
    onstatus = () => {};
    connect() {}
    close() {}
    subscribe() {}
    unsubscribe() {}
    listen() {
      return () => {};
    }
  },
}));

/**
 * What `Recorder.stop()` hands back. `null` — nothing recorded — is the default
 * and is enough for a hold to begin and end without `getUserMedia`, which jsdom
 * does not have. A test that wants the *send* half of a release sets a take
 * here: `HoldToTalk` drops a null one on the floor, so with only the default a
 * `Room` that passed no `onAudio` at all would look exactly the same.
 */
const mic = vi.hoisted(() => ({
  recording: null as { blob: Blob; durationMs: number } | null,
}));

vi.mock("@/lib/recorder", () => ({
  Recorder: class {
    analyser = null;
    async start() {}
    async stop() {
      return mic.recording;
    }
  },
  blobToDataUrl: async () => "data:audio/mp4;base64,AAAA",
  pickMimeType: () => "audio/mp4",
}));

/**
 * How many rows the Workshop's feed has summarised. `summarise` runs in
 * `EventRow`'s render body, unmemoised, so the count is a direct reading of how
 * many rows re-rendered — which is how the `memo` on `Workshop` is visible from
 * outside it.
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

/**
 * What the house answers, by path. A test that needs a different house copies
 * these into `routes` and overrides; a `Response` is served as it is, so a test
 * can make the house say 404, and a promise is waited for, so a test can hold a
 * read out for as long as it likes.
 */
const ROUTES: Record<string, unknown> = {
  "/api/auth/status": { registered: true, authenticated: true },
  "/api/admin/overview": overviewFixture,
  "/api/admin/streams/user_requests?count=50": userRequestsPage,
  "/api/admin/streams/user_responses?count=50": userResponsesPage,
  "/api/admin/streams/reflex_observations?count=50": reflexObservationsPage,
  "/api/admin/streams/notifications?count=50": notificationsPage,
  "/api/actions/pending": { actions: [] },
  "/api/push/public-key": { public_key: "BHouseKey" },
};

const EMPTY_PAGE = { entries: [], next_before: null };

let routes: Record<string, unknown>;

/** The paths fetch was asked for, in order. */
function fetched(): string[] {
  return vi.mocked(fetch).mock.calls.map(([input]) => String(input));
}

/**
 * The first reads, landed inside `act`. Left to a `findBy`, one can commit just as
 * that ends, and the effect it wakes then warns: `act`'s environment is back on.
 */
async function settle(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(50);
  });
}

/** The key read stays out until the test answers it, and what it said has then been drawn. */
function holdKey(): () => Promise<void> {
  const key = ROUTES["/api/push/public-key"];
  let answer: (reply: unknown) => void = () => {};
  routes["/api/push/public-key"] = new Promise((resolve) => {
    answer = resolve;
  });
  return () =>
    act(async () => {
      answer(key);
      await vi.advanceTimersByTimeAsync(10);
    });
}

/** An iPhone's first launch from the home screen. Hands back iOS's word, for a test to change. */
function firstLaunch(): { permission: NotificationPermission } {
  vi.mocked(reachContext).mockReturnValue("capable");
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(IPHONE_UA);
  const notification = { permission: "default" as NotificationPermission };
  vi.stubGlobal("Notification", notification);
  return notification;
}

/** The standalone gates up: Reach's and the Room's are the same dialog. */
const frontWayGates = () => screen.queryAllByRole("dialog", { name: "You came in the front way" });

beforeEach(() => {
  socket.status = "online";
  routes = { ...ROUTES };
  mic.recording = null;
  // Module-level, so without this the Door-tick test's `rows` already carries
  // every row the tests ahead of it summarised, and `toBeGreaterThan(0)` is
  // true before that test has rendered a thing.
  summarised.count = 0;
  // Four minutes before the fixture's fuse lapses, so the deep link opens a
  // live Door and not the one that expired the morning the fixture was written.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-09-07T07:42:00Z"));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url in routes) {
        const reply = await routes[url];
        return reply instanceof Response
          ? reply.clone()
          : new Response(JSON.stringify(reply), { status: 200 });
      }
      if (url.startsWith("/api/actions/"))
        return new Response(JSON.stringify(pendingActionFixture), { status: 200 });
      return new Response("{}", { status: 200 });
    }),
  );
});

afterEach(() => {
  notifyManager.setScheduler(defaultScheduler);
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.mocked(reachContext).mockReset();
  vi.mocked(requestAndSubscribe).mockReset();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  window.history.pushState({}, "", "/");
});

describe("App", () => {
  it("boots an authenticated device into a listening Room", async () => {
    render(<App />);
    await settle();

    expect(await screen.findByRole("heading", { name: "Listening, sir." })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Switch theme" })).toBeInTheDocument();
    expect(await screen.findByText(/cloud 1.42 \/ 5.00/)).toBeInTheDocument();
  });

  it("shows the thread the four streams describe", async () => {
    render(<App />);

    expect(
      await screen.findByText("What have I got tomorrow morning?"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "The dentist at nine, sir. I'd leave by twenty to; there's rain forecast from eight.",
      ),
    ).toBeInTheDocument();
    // The row reads the notification's body, not its title.
    expect(screen.getByText("The door sensor saw it at 18:20.")).toBeInTheDocument();
    // The confirmation notification belongs to the Door, not the thread.
    expect(screen.queryByText("Confirmation required")).toBeNull();
  });

  it("shows only the current session, and the house's rows for the day", async () => {
    // 21:20 local: the fixture's 20:52–21:14 turns are one live session, the
    // 18:20 parcel notification is today's, and yesterday's request is not.
    vi.setSystemTime(new Date(2026, 8, 7, 21, 20));
    routes["/api/admin/streams/user_requests?count=50"] = {
      entries: [...userRequestsPage.entries, ...yesterdayRequestPage.entries],
      next_before: null,
    };
    render(<App />);

    expect(await screen.findByText("What have I got tomorrow morning?")).toBeInTheDocument();
    expect(screen.getByText("The door sensor saw it at 18:20.")).toBeInTheDocument();
    expect(screen.queryByText("Lock up for the night.")).toBeNull();
  });

  it("offers the composer and the microphone", async () => {
    render(<App />);
    await settle();
    expect(await screen.findByPlaceholderText("Ask or tell Alfred")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hold to talk" })).toBeInTheDocument();
  });

  it("holds a signed-out device at the gate, short of the room", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"registered":true,"authenticated":false}', { status: 200 })),
    );
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Welcome back, sir." })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Listening, sir." })).not.toBeInTheDocument();
    // Nothing behind the gate has asked the house for anything — the Door in
    // particular, whose 401 would raise the Expired gate over the sign-in.
    // `every` is true of an empty list, so the gate's own read is named first:
    // without it a build that fetched nothing at all would pass this line.
    expect(fetched()).toContain("/api/auth/status");
    expect(fetched().every((url) => url.startsWith("/api/auth/"))).toBe(true);
  });

  it("lands an unknown path in the Room", async () => {
    window.history.pushState({}, "", "/nowhere");
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Listening, sir." })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/");
  });

  it("applies the stored theme to the document as it mounts", async () => {
    // Late at night, when the clock alone would say dark: only the stored choice
    // can make this light.
    vi.setSystemTime(new Date(2026, 8, 7, 23, 0));
    localStorage.setItem(THEME_KEY, "light");
    render(<App />);
    await settle();
    await screen.findByRole("heading", { name: "Listening, sir." });
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("opens the Door on the action deep link", async () => {
    window.history.pushState({}, "", "/actions/a91f3c2e");
    render(<App />);

    expect(await screen.findByRole("dialog", { name: "Critical approval" })).toBeInTheDocument();
    expect(screen.getByText("CRITICAL · a91f")).toBeInTheDocument();
    // The fuse is still running: the clock is pinned inside it (see beforeEach).
    expect(screen.getByText("until it lapses")).toBeInTheDocument();
  });

  it("keeps the deep link through a lapsed session, and opens the Door once signed back in", async () => {
    window.history.pushState({}, "", "/actions/a91f3c2e");
    routes["/api/actions/a91f3c2e"] = new Response('{"detail":"Not authenticated"}', {
      status: 401,
    });
    render(<App />);

    // The Expired gate, over a Room that kept the link and opened nothing.
    const gate = await screen.findByRole("dialog", { name: "Session lapsed" });
    expect(window.location.pathname).toBe("/actions/a91f3c2e");
    expect(screen.queryByRole("dialog", { name: "Critical approval" })).toBeNull();

    delete routes["/api/actions/a91f3c2e"];
    fireEvent.click(within(gate).getByRole("button", { name: "Sign in with Face ID" }));

    expect(await screen.findByRole("dialog", { name: "Critical approval" })).toBeInTheDocument();
    expect(loginPasskeyMock).toHaveBeenCalledOnce();
    expect(fetched().filter((url) => url === "/api/actions/a91f3c2e")).toHaveLength(2);
  });

  it("leaves a tombstone in the thread when the deep link's action is gone", async () => {
    window.history.pushState({}, "", "/actions/a91f3c2e");
    routes["/api/actions/a91f3c2e"] = new Response('{"detail":"not found"}', { status: 404 });
    render(<App />);

    // Named from the notification that asked, not from the id.
    expect(await screen.findByText("Lock unlock")).toBeInTheDocument();
    expect(screen.getByText("already answered · nothing was done")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps a new message the thread already has the words of", async () => {
    // The history is the line between what predates this session and what the
    // house reads back: a "yes" from this morning must not swallow one typed
    // now. It would, if the Room handed useRoom an empty thread before the
    // first read had answered.
    routes["/api/admin/streams/user_requests?count=50"] = {
      entries: [
        {
          id: "1788811923000-0",
          event: {
            event_id: "ur-1",
            event_type: "user_request",
            timestamp: "2026-09-07T20:52:03",
            source: "web-pwa",
            channel: "web_pwa",
            session_id: "s_9f3",
            content_type: "text",
            content: "yes",
          },
        },
      ],
      next_before: null,
    };
    render(<App />);
    expect(await screen.findAllByText("yes")).toHaveLength(1);

    fireEvent.change(screen.getByPlaceholderText("Ask or tell Alfred"), {
      target: { value: "yes" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findAllByText("yes")).toHaveLength(2);
  });

  it("leaves a lapsed approval in the thread as a tombstone, with no banner", async () => {
    // Asked and gone before the pinned clock (see beforeEach).
    routes["/api/actions/pending"] = {
      actions: [
        {
          ...pendingActionFixture,
          timestamp: "2026-09-07T07:30:00Z",
          expires_at: "2026-09-07T07:35:00Z",
        },
      ],
    };
    render(<App />);

    expect(
      await screen.findByText(/^expired \d{2}:\d{2} · not done · asked \d{2}:\d{2}$/),
    ).toBeInTheDocument();
    expect(screen.getByText("Lock unlock")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Lock unlock/ })).toBeNull();
  });

  it("raises the banner for a pending approval and opens the Door from it", async () => {
    routes["/api/actions/pending"] = { actions: [pendingActionFixture] };
    render(<App />);

    const banner = await screen.findByRole("button", { name: /^Lock unlock/ });
    expect(banner).toHaveTextContent("asked by Alfred, for you");
    fireEvent.click(banner);
    expect(await screen.findByRole("dialog", { name: "Critical approval" })).toBeInTheDocument();
  });

  it("says the house is quiet, counts what it held back, and shows it on a tap", async () => {
    routes["/api/admin/overview"] = {
      ...overviewFixture,
      dnd: { active: true, until: "2026-09-07T09:30:00" },
      counts: { ...overviewFixture.counts, deferred: 2 },
    };
    routes["/api/admin/notifications/deferred"] = deferredFixture;
    render(<App />);

    expect(await screen.findByRole("heading", { name: /^Quiet until / })).toBeInTheDocument();
    const row = screen.getByRole("button", { name: /^Do-not-disturb until / });
    expect(row).toHaveTextContent("2 held ›");
    fireEvent.click(row);
    const sheet = await screen.findByRole("dialog", { name: "Held back" });
    expect(
      await within(sheet).findByText("The council moved collection to Friday."),
    ).toBeInTheDocument();
  });

  it("offers a worker that installs behind the open Room, and takes it on the tap", async () => {
    // Quiet as well, so the header carries every row the note can sit among.
    routes["/api/admin/overview"] = {
      ...overviewFixture,
      dnd: { active: true, until: "2026-09-07T09:30:00" },
    };
    let announce: (ready: boolean) => void = () => {};
    vi.spyOn(sw, "onUpdateReady").mockImplementation((fn) => {
      announce = fn;
      fn(false);
      return () => {};
    });
    const apply = vi.spyOn(sw, "applyUpdate").mockImplementation(() => {});
    render(<App />);

    expect(await screen.findByRole("heading", { name: /^Quiet until / })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reload" })).not.toBeInTheDocument();

    // A changed sw.js finishes installing behind the running worker.
    act(() => announce(true));
    const note = screen.getByText(
      "A newer Alfred is waiting. Nothing is lost; I pick up where we left off.",
    );
    // Directly under OfflineNote, the header's other live region, and over the
    // quiet row.
    const region = note.closest('[role="status"]');
    expect(region?.previousElementSibling).toHaveAttribute("role", "status");
    expect(region?.nextElementSibling).toBe(
      screen.getByRole("button", { name: /^Do-not-disturb until / }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(apply).toHaveBeenCalledOnce();
  });

  it("greets a house on its first day, and keeps up with the clock", async () => {
    vi.setSystemTime(new Date(2026, 8, 7, 9, 0));
    routes["/api/admin/overview"] = firstRunOverviewFixture;
    for (const name of ["user_requests", "user_responses", "reflex_observations", "notifications"])
      routes[`/api/admin/streams/${name}?count=50`] = EMPTY_PAGE;
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Good morning, sir." })).toBeInTheDocument();
    expect(screen.getByText(/Nothing has happened yet/)).toBeInTheDocument();

    // Left open until the afternoon, then brought back to the foreground.
    vi.setSystemTime(new Date(2026, 8, 7, 14, 0));
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(await screen.findByRole("heading", { name: "Good afternoon, sir." })).toBeInTheDocument();
  });

  it("opens the Workshop from the handle and returns to the Room", async () => {
    render(<App />);
    await screen.findByText("What have I got tomorrow morning?");

    fireEvent.click(screen.getByRole("button", { name: "Open the Workshop" }));
    const workshop = await screen.findByRole("dialog", { name: "Workshop" });
    expect(within(workshop).getByRole("tab", { name: "Activity" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // The bench reads the eight streams' heads; the four the Room also reads
    // come from the same routes, so the reflex observation is a row here too.
    expect(
      await within(workshop).findByText("observed media_player.tv · acted"),
    ).toBeInTheDocument();

    fireEvent.click(within(workshop).getByRole("button", { name: "Room" }));
    act(() => vi.advanceTimersByTime(400));
    expect(screen.queryByRole("dialog", { name: "Workshop" })).toBeNull();
    expect(screen.getByRole("log", { name: "Conversation" })).toBeInTheDocument();
  });

  it("asks why on a reflex act row and sees the thread sheet", async () => {
    // The candidate reads are their own paths (`count=100`, `before` the
    // anchor's id plus the join window), so the house must answer them
    // separately from the Room's `count=50` pages. One real join: the
    // observation's action carries `request_id: "4b1d"`, and so does this.
    routes["/api/admin/streams/actions?count=100&before=1788800880001-0"] = {
      entries: [
        {
          id: "1788800279000-0",
          event: {
            event_id: "1f22b7c0",
            request_id: "4b1d",
            tool_name: "home.light_set",
            parameters: { entity_id: "light.living_room" },
            target_service: "home-service",
            source: "reflex-engine",
          },
        },
      ],
      next_before: null,
    };
    render(<App />);

    // Named for its row, so this needs no knowledge of ActRow's DOM.
    fireEvent.click(
      await screen.findByRole("button", { name: /movie started, evening, user home/ }),
    );
    const sheet = await screen.findByRole("dialog", { name: "Why Alfred did that" });
    // The observation, and the action joined to it by `request_id` — drawn
    // from eight fully searched streams.
    expect(await within(sheet).findByText("observed media_player.tv · acted")).toBeInTheDocument();
    expect(within(sheet).getByText("home.light_set light.living_room")).toBeInTheDocument();
    expect(within(sheet).getAllByRole("listitem")).toHaveLength(2);
    expect(within(sheet).getByText(/joined by request_id 4b1d/)).toBeInTheDocument();
    expect(
      within(sheet).getByText("searched 8 streams · 100 entries each · ±10 min"),
    ).toBeInTheDocument();
    expect(fetched().filter((url) => url.includes("count=100&before=1788800880001-0"))).toHaveLength(8);

    fireEvent.click(within(sheet).getByRole("button", { name: "Done" }));
    act(() => vi.advanceTimersByTime(380));
    expect(screen.queryByRole("dialog", { name: "Why Alfred did that" })).toBeNull();
  });

  it("asks why from the Workshop, and the sheet paints over it", async () => {
    render(<App />);
    await screen.findByText("What have I got tomorrow morning?");

    fireEvent.click(screen.getByRole("button", { name: "Open the Workshop" }));
    const workshop = await screen.findByRole("dialog", { name: "Workshop" });
    // `Why · causal thread` is offered on an open row only.
    fireEvent.click(await within(workshop).findByText("observed media_player.tv · acted"));
    fireEvent.click(within(workshop).getByRole("button", { name: "Why · causal thread" }));

    const sheet = await screen.findByRole("dialog", { name: "Why Alfred did that" });
    // The stack, from the outside: the sheet is over the Workshop, and the
    // Workshop under it cannot be reached.
    expect(workshop).toHaveAttribute("inert");
    expect(sheet.closest("[inert]")).toBeNull();

    fireEvent.click(within(sheet).getByRole("button", { name: "Done" }));
    act(() => vi.advanceTimersByTime(380));
    expect(screen.queryByRole("dialog", { name: "Why Alfred did that" })).toBeNull();
    // The Workshop is still up, and live again.
    expect(screen.getByRole("dialog", { name: "Workshop" })).not.toHaveAttribute("inert");
  });

  it("opens the Room's own Held-back sheet from System › Quiet", async () => {
    routes["/api/admin/notifications/deferred"] = deferredFixture;
    render(<App />);
    await screen.findByText("What have I got tomorrow morning?");

    fireEvent.click(screen.getByRole("button", { name: "Open the Workshop" }));
    const workshop = await screen.findByRole("dialog", { name: "Workshop" });
    fireEvent.click(within(workshop).getByRole("tab", { name: "System" }));
    fireEvent.click(await within(workshop).findByRole("button", { name: /^Held back/ }));

    // The whole chain, which no unit test can see: the bench asks
    // `system.quiet.onHeld`, `Workshop` hands it up as `onHeld`, and the Room
    // opens the one sheet it already keeps for its own DND row. A second copy
    // rendered inside the Workshop would look identical from the bench and
    // would not paint over it.
    const sheet = await screen.findByRole("dialog", { name: "Held back" });
    expect(
      await within(sheet).findByText("The council moved collection to Friday."),
    ).toBeInTheDocument();
    expect(workshop).toHaveAttribute("inert");
  });

  it("keeps the Room's once-a-second Door tick out of the Workshop", async () => {
    // A pending approval is what makes the Room re-render on a clock
    // (`DoorProvider` runs its tick only while something is counting), which is
    // the case `Workshop.tsx` names for its `memo`.
    routes["/api/actions/pending"] = { actions: [pendingActionFixture] };
    render(<App />);
    await settle();

    fireEvent.click(await screen.findByRole("button", { name: "Open the Workshop" }));
    const workshop = await screen.findByRole("dialog", { name: "Workshop" });
    await within(workshop).findByText("observed media_player.tv · acted");

    const fuse = screen.getByRole("button", { name: /^Lock unlock/ });
    const rows = summarised.count;
    const countdown = fuse.textContent;
    expect(rows).toBeGreaterThan(0);

    act(() => vi.advanceTimersByTime(3000));

    // The Room did re-render — its fuse is counting down.
    expect(fuse.textContent).not.toBe(countdown);
    // And not one row under the Workshop re-rendered with it. Measured against
    // the real Room rather than a stand-in, because the memo only holds while
    // every prop the Room passes is stable: an inline `onClose`, `onWhy` or
    // `onHeld` in `Room.tsx` would defeat it and no test in
    // `workshop/Workshop.test.tsx` could tell.
    expect(summarised.count).toBe(rows);
  });

  it("an iPhone's first launch from the home screen raises the gate, and a decline is written into the Room", async () => {
    // 21:20 local: the fixture's turns are one live session, and the row lands in it.
    vi.setSystemTime(new Date(2026, 8, 7, 21, 20));
    vi.mocked(reachContext).mockReturnValue("capable");
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(IPHONE_UA);
    vi.stubGlobal("Notification", {
      permission: "default",
      requestPermission: async () => "denied",
    });
    render(<App />);

    const gate = await screen.findByRole("dialog", { name: "You came in the front way" });
    expect(
      within(gate).getByRole("heading", { name: "You came in the front way." }),
    ).toBeInTheDocument();
    fireEvent.click(within(gate).getByRole("button", { name: "Let me reach you" }));

    expect(await screen.findByText("Notifications stay off on this iPhone")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(400));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "You came in the front way" })).toBeNull(),
    );
  });

  it("a key read answering over Reach's own gate raises no second gate, and asks once", async () => {
    // 21:20 local: the fixture's turns are one live session, and the row lands in it.
    vi.setSystemTime(new Date(2026, 8, 7, 21, 20));
    const notification = firstLaunch();
    vi.mocked(requestAndSubscribe).mockImplementation(async () => {
      notification.permission = "granted";
      return "granted";
    });
    const answerKey = holdKey();
    render(<App />);
    await screen.findByText("What have I got tomorrow morning?");

    // Through Reach, which a slow key read leaves time for.
    fireEvent.click(screen.getByRole("button", { name: "Open the Workshop" }));
    const workshop = await screen.findByRole("dialog", { name: "Workshop" });
    fireEvent.click(within(workshop).getByRole("tab", { name: "System" }));
    fireEvent.click(await within(workshop).findByRole("button", { name: "Enable" }));
    expect(frontWayGates()).toHaveLength(1);

    await answerKey();
    expect(frontWayGates()).toHaveLength(1);

    const primaries = () =>
      frontWayGates().map((gate) => within(gate).getByRole("button", { name: "Let me reach you" }));
    fireEvent.click(primaries()[0]);
    expect(await screen.findByText("Notifications on for this iPhone")).toBeInTheDocument();

    // Whatever is still standing, tapped again: a lower gate would ask a second time.
    primaries().forEach((primary) => fireEvent.click(primary));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(frontWayGates()).toHaveLength(0);

    expect(requestAndSubscribe).toHaveBeenCalledOnce();
    expect(screen.getAllByText("Notifications on for this iPhone")).toHaveLength(1);
  });

  it("a key read answering over an open Door raises nothing, and the next launch offers it", async () => {
    firstLaunch();
    routes["/api/actions/pending"] = { actions: [pendingActionFixture] };
    const answerKey = holdKey();
    const { unmount } = render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /^Lock unlock/ }));
    const door = await screen.findByRole("dialog", { name: "Critical approval" });

    await answerKey();

    // The Door keeps its fuse, on top and live.
    expect(frontWayGates()).toHaveLength(0);
    expect(door).not.toHaveAttribute("inert");
    expect(within(door).getByText("until it lapses")).toBeInTheDocument();
    // Skipped, which is not answered: `Not yet` is what ends the opening for good.
    expect(gateAnswered()).toBe(false);
    unmount();

    routes = { ...routes, "/api/push/public-key": ROUTES["/api/push/public-key"] };
    render(<App />);

    expect(
      await screen.findByRole("dialog", { name: "You came in the front way" }),
    ).toBeInTheDocument();
  });

  it("a Door tapped open as the key settles raises nothing, with its answer yet to notify", async () => {
    firstLaunch();
    routes["/api/actions/pending"] = { actions: [pendingActionFixture] };
    const answerKey = holdKey();
    render(<App />);
    const banner = await screen.findByRole("button", { name: /^Lock unlock/ });
    // The Room's other first reads have landed, so none of them renders it inside the
    // gap below. That render would find the key answered with nothing up, and open the
    // gate for the right reason ahead of the tap.
    await settle();
    const dialogs = () => screen.queryAllByRole("dialog").map((d) => d.getAttribute("aria-label"));

    // The key has settled and react-query's notify is still out, so the tap's own
    // render is the first to read it as answered, ahead of the Door joining the stack.
    const held: (() => void)[] = [];
    notifyManager.setScheduler((notify) => void held.push(notify));
    await answerKey();
    expect(dialogs()).toEqual([]);
    fireEvent.click(banner);
    expect(dialogs()).toEqual(["Critical approval"]);

    notifyManager.setScheduler(defaultScheduler);
    await act(async () => {
      held.forEach((notify) => notify());
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(dialogs()).toEqual(["Critical approval"]);
    // Skipped, which is not answered: the next launch offers it.
    expect(gateAnswered()).toBe(false);
  });

  it("a waiting Door in an iPhone tab offers the install path", async () => {
    // 21:20 local, the thread's day, with the fuse of a Door asked a minute ago running.
    vi.setSystemTime(new Date(2026, 8, 7, 21, 20));
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(IPHONE_UA);
    routes["/api/actions/pending"] = {
      actions: [
        {
          ...pendingActionFixture,
          timestamp: new Date(2026, 8, 7, 21, 19).toISOString(),
          expires_at: new Date(2026, 8, 7, 21, 24).toISOString(),
        },
      ],
    };
    const text =
      "That door is waiting on you below. Had this tab been closed, I would have had no way to tell you. Add me to the home screen and I can knock when something cannot wait.";
    render(<App />);

    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.getByText("notifications · unavailable in Safari · 21:19")).toBeInTheDocument();
    const log = screen.getByRole("log", { name: "Conversation" });

    fireEvent.click(within(log).getByRole("button", { name: "Show me how" }));
    const gate = await screen.findByRole("dialog", { name: "Add me to the home screen" });
    expect(
      within(gate).getByRole("heading", { name: "Add me to the home screen." }),
    ).toBeInTheDocument();

    fireEvent.click(within(gate).getByRole("button", { name: "Not now" }));
    act(() => vi.advanceTimersByTime(400));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Add me to the home screen" })).toBeNull(),
    );
    expect(screen.getByText(text)).toBeInTheDocument();

    const lines = () => Array.from(log.firstElementChild?.children ?? [], (row) => row.textContent);
    const thread = lines();
    fireEvent.click(within(log).getByRole("button", { name: "Not now" }));

    expect(screen.queryByText(text)).toBeNull();
    // The row went, and no line took its place (the spec's D11).
    expect(lines()).toEqual(thread.filter((line) => !line?.includes(text)));
  });

  it("no such offer off an iPhone", async () => {
    routes["/api/actions/pending"] = { actions: [pendingActionFixture] };
    render(<App />);

    expect(await screen.findByRole("button", { name: /^Lock unlock/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show me how" })).toBeNull();
    expect(fetched()).not.toContain("/api/push/public-key");
  });
});

describe("App — the socket's word", () => {
  it("shows the last-known Room, and says so, while the house is unreachable", async () => {
    socket.status = "offline";
    const setOffline = vi.spyOn(PresenceSignal.prototype, "setOffline");
    render(<App />);
    await settle();

    expect(await screen.findByRole("heading", { name: "Unreachable." })).toBeInTheDocument();
    expect(screen.getByText(/^No connection to the house since /)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Offline · will send when connected")).toBeInTheDocument();
    // The field goes grey too (spec §5.2.2): the signal is how it hears.
    expect(setOffline).toHaveBeenLastCalledWith(true);
  });

  it("says it is still trying while the socket reconnects", async () => {
    socket.status = "reconnecting";
    render(<App />);
    await settle();

    expect(await screen.findByRole("heading", { name: "Reconnecting…" })).toBeInTheDocument();
    expect(screen.getByText(/^Trying again\. /)).toBeInTheDocument();
  });

  it("turns the headline and the field to a turn in flight", async () => {
    const setThinking = vi.spyOn(PresenceSignal.prototype, "setThinking");
    render(<App />);
    await settle();
    const field = await screen.findByPlaceholderText("Ask or tell Alfred");

    fireEvent.change(field, { target: { value: "Lights off in the study" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("heading", { name: "One moment, sir." })).toBeInTheDocument();
    expect(setThinking).toHaveBeenLastCalledWith(true);
  });

  it("changes the headline the moment a hold begins", async () => {
    render(<App />);
    await settle();
    await screen.findByRole("heading", { name: "Listening, sir." });
    const button = screen.getByRole("button", { name: "Hold to talk" });

    fireEvent.pointerDown(button, { pointerId: 1 });
    // Synchronously: the microphone has not opened yet, and the word must not
    // wait for it.
    expect(screen.getByRole("heading", { name: "Go on, sir." })).toBeInTheDocument();

    fireEvent.pointerUp(button, { pointerId: 1 });
    expect(await screen.findByRole("heading", { name: "Listening, sir." })).toBeInTheDocument();
  });

  it("sends the take the microphone came back with, and shows it going", async () => {
    // Two seconds, which clears `HoldToTalk`'s one-second floor. The test above
    // releases an *empty* microphone, so it walks the same path whether or not
    // `Room` wires `onAudio` to anything: this is the half that does not.
    mic.recording = { blob: new Blob(["take"], { type: "audio/mp4" }), durationMs: 2000 };
    render(<App />);
    await settle();
    await screen.findByRole("heading", { name: "Listening, sir." });
    const button = screen.getByRole("button", { name: "Hold to talk" });

    fireEvent.pointerDown(button, { pointerId: 1 });
    fireEvent.pointerUp(button, { pointerId: 1 });

    // The dashed bubble is only drawn once the frame has left, so its length is
    // the audio the Room actually handed the socket, not the button's own idea
    // of how long a finger was down.
    expect(await screen.findByText("audio sent · 2.0 s · waiting on server")).toBeInTheDocument();
  });
});
