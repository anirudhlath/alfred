import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  deferredFixture,
  firstRunOverviewFixture,
  notificationsPage,
  overviewFixture,
  pendingActionFixture,
  reflexObservationsPage,
  userRequestsPage,
  userResponsesPage,
} from "@/test/fixtures";
import { PresenceSignal } from "@/lib/presence-signal";
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

// A microphone that opens at once and has nothing to say: enough for a hold to
// begin and end without getUserMedia, which jsdom does not have.
vi.mock("@/lib/recorder", () => ({
  Recorder: class {
    analyser = null;
    async start() {}
    async stop() {
      return null;
    }
  },
  blobToDataUrl: async () => "",
  pickMimeType: () => "audio/mp4",
}));

/**
 * What the house answers, by path. A test that needs a different house copies
 * these into `routes` and overrides; a `Response` is served as it is, so a test
 * can make the house say 404.
 */
const ROUTES: Record<string, unknown> = {
  "/api/auth/status": { registered: true, authenticated: true },
  "/api/admin/overview": overviewFixture,
  "/api/admin/streams/user_requests?count=50": userRequestsPage,
  "/api/admin/streams/user_responses?count=50": userResponsesPage,
  "/api/admin/streams/reflex_observations?count=50": reflexObservationsPage,
  "/api/admin/streams/notifications?count=50": notificationsPage,
  "/api/actions/pending": { actions: [] },
};

const EMPTY_PAGE = { entries: [], next_before: null };

let routes: Record<string, unknown>;

/** The paths fetch was asked for, in order. */
function fetched(): string[] {
  return vi.mocked(fetch).mock.calls.map(([input]) => String(input));
}

beforeEach(() => {
  socket.status = "online";
  routes = { ...ROUTES };
  // Four minutes before the fixture's fuse lapses, so the deep link opens a
  // live Door and not the one that expired the morning the fixture was written.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-09-07T07:42:00Z"));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url in routes) {
        const reply = routes[url];
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
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  window.history.pushState({}, "", "/");
});

describe("App", () => {
  it("boots an authenticated device into a listening Room", async () => {
    render(<App />);

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
    expect(screen.getByText("Your parcel arrived")).toBeInTheDocument();
    // The confirmation notification belongs to the Door, not the thread.
    expect(screen.queryByText("Confirmation required")).toBeNull();
  });

  it("offers the composer and the microphone", async () => {
    render(<App />);
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
    expect(await within(sheet).findByText("Bins go out tonight")).toBeInTheDocument();
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
});

describe("App — the socket's word", () => {
  it("shows the last-known Room, and says so, while the house is unreachable", async () => {
    socket.status = "offline";
    const setOffline = vi.spyOn(PresenceSignal.prototype, "setOffline");
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Unreachable." })).toBeInTheDocument();
    expect(screen.getByText(/^No connection to the house since /)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Offline · will send when connected")).toBeInTheDocument();
    // The field goes grey too (spec §5.2.2): the signal is how it hears.
    expect(setOffline).toHaveBeenLastCalledWith(true);
  });

  it("says it is still trying while the socket reconnects", async () => {
    socket.status = "reconnecting";
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Reconnecting…" })).toBeInTheDocument();
    expect(screen.getByText(/^Trying again\. /)).toBeInTheDocument();
  });

  it("turns the headline and the field to a turn in flight", async () => {
    const setThinking = vi.spyOn(PresenceSignal.prototype, "setThinking");
    render(<App />);
    const field = await screen.findByPlaceholderText("Ask or tell Alfred");

    fireEvent.change(field, { target: { value: "Lights off in the study" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("heading", { name: "One moment, sir." })).toBeInTheDocument();
    expect(setThinking).toHaveBeenLastCalledWith(true);
  });

  it("changes the headline the moment a hold begins", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "Listening, sir." });
    const button = screen.getByRole("button", { name: "Hold to talk" });

    fireEvent.pointerDown(button, { pointerId: 1 });
    // Synchronously: the microphone has not opened yet, and the word must not
    // wait for it.
    expect(screen.getByRole("heading", { name: "Go on, sir." })).toBeInTheDocument();

    fireEvent.pointerUp(button, { pointerId: 1 });
    expect(await screen.findByRole("heading", { name: "Listening, sir." })).toBeInTheDocument();
  });
});
