import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  notificationsPage,
  overviewFixture,
  pendingActionFixture,
  reflexObservationsPage,
  userRequestsPage,
  userResponsesPage,
} from "@/test/fixtures";
import { THEME_KEY } from "@/lib/theme";
import App from "./App";

vi.mock("@/lib/chat-socket", () => ({
  ChatSocket: class {
    onstatus: (status: string) => void = () => {};
    connect() {
      // Report online from connect(), which ConnectionProvider calls after it has
      // assigned onstatus — the Room's headline depends on it.
      this.onstatus("online");
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

const ROUTES: Record<string, unknown> = {
  "/api/auth/status": { registered: true, authenticated: true },
  "/api/admin/overview": overviewFixture,
  "/api/admin/streams/user_requests?count=50": userRequestsPage,
  "/api/admin/streams/user_responses?count=50": userResponsesPage,
  "/api/admin/streams/reflex_observations?count=50": reflexObservationsPage,
  "/api/admin/streams/notifications?count=50": notificationsPage,
  "/api/actions/pending": { actions: [] },
};

beforeEach(() => {
  // Four minutes before the fixture's fuse lapses, so the deep link opens a
  // live Door and not the one that expired the morning the fixture was written.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-09-07T07:42:00Z"));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url in ROUTES) return new Response(JSON.stringify(ROUTES[url]), { status: 200 });
      if (url.startsWith("/api/actions/"))
        return new Response(JSON.stringify(pendingActionFixture), { status: 200 });
      return new Response("{}", { status: 200 });
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
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
});
