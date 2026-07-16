import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { CATEGORY_CLASS } from "@/lib/format";
import type { SocketStatus } from "@/lib/ws";
import { TelemetryRail } from "./TelemetryRail";

// ---------------------------------------------------------------------------
// Shared mocks
// ---------------------------------------------------------------------------

const now = Date.now();

const mockFeed = [
  {
    stream: "reflex_observations",
    id: `${now - 5000}-0`,
    event: { event_type: "reflex_observation", action: { tool_name: "turn_on_lights" } },
  },
  {
    stream: "user_requests",
    id: `${now - 15000}-0`,
    event: { event_type: "user_request", content: "What is the temperature?" },
  },
  {
    stream: "notifications",
    id: `${now - 30000}-0`,
    event: { event_type: "notification", title: "Cost alert" },
  },
];

const mockOverview = {
  redis: { connected: true },
  cost: { date: "2026-06-11", spend_usd: 0.42, cap_usd: 5.0, alert_sent: false },
  dnd: { active: false, until: null, reason: null, source: "auto" },
  counts: { sessions: 3, devices: 1, deferred: 0, triggers: 5 },
  streams: {},
  inference: { ollama: true, lmstudio: false },
};

// Mock AlfredProvider — TelemetryRail uses useAlfred()
vi.mock("./AlfredProvider", () => ({
  useAlfred: vi.fn(),
}));

// Mock api module so useQuery can resolve without a real server
vi.mock("@/lib/api", () => ({
  api: vi.fn(),
}));

import { useAlfred } from "./AlfredProvider";
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

async function renderRail({
  feed = mockFeed,
  telemetryStatus = "online" as SocketStatus,
  overview = mockOverview,
} = {}) {
  vi.mocked(useAlfred).mockReturnValue({
    feed,
    telemetryStatus,
    chatStatus: "online",
    chat: {} as never,
    telemetry: {} as never,
  });
  vi.mocked(api).mockResolvedValue(overview);

  const qc = makeQueryClient();
  const result = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TelemetryRail />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  // Wait for useQuery to resolve: cost value "$X.XX" only appears once the
  // overview query settles (initial render shows "—").
  if (overview?.cost) {
    await screen.findByText(`$${overview.cost.spend_usd.toFixed(2)}`);
  } else {
    // No cost — wait for DND value to be rendered (either ON or OFF)
    await screen.findByText(overview.dnd.active ? "ON" : "OFF");
  }

  return result;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TelemetryRail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders live ticker lines with summaries", async () => {
    await renderRail();

    // summarize() results for each mock entry
    expect(screen.getByText("turn_on_lights")).toBeInTheDocument();
    expect(screen.getByText(/What is the temperature\?/i)).toBeInTheDocument();
    expect(screen.getByText("Cost alert")).toBeInTheDocument();
  });

  it("applies CATEGORY_CLASS color classes to category labels", async () => {
    await renderRail();

    // "reflex" category label should carry the reflex class
    const reflexLabels = screen.getAllByText("reflex");
    expect(reflexLabels.length).toBeGreaterThan(0);
    expect(reflexLabels[0]).toHaveClass(CATEGORY_CLASS.reflex);

    // "user" category label
    const userLabels = screen.getAllByText("user");
    expect(userLabels.length).toBeGreaterThan(0);
    expect(userLabels[0]).toHaveClass(CATEGORY_CLASS.user);

    // "trigger" category (notifications stream maps to trigger)
    const triggerLabels = screen.getAllByText("trigger");
    expect(triggerLabels.length).toBeGreaterThan(0);
    expect(triggerLabels[0]).toHaveClass(CATEGORY_CLASS.trigger);
  });

  it("renders vitals: cost, sessions, DND from overview data", async () => {
    await renderRail();

    // Cost vital
    expect(screen.getByText("$0.42")).toBeInTheDocument();
    // Sessions vital — may also appear as EVENTS/MIN count (both "3"), so use getAllByText
    const threes = screen.getAllByText("3");
    expect(threes.length).toBeGreaterThanOrEqual(1);
    // DND vital — inactive
    expect(screen.getByText("OFF")).toBeInTheDocument();
  });

  it("shows DND ON with warn tone when active", async () => {
    const activeDndOverview = {
      ...mockOverview,
      dnd: { active: true, until: null, reason: null, source: "manual" },
    };
    await renderRail({ overview: activeDndOverview });
    const dndValue = screen.getByText("ON");
    expect(dndValue).toHaveClass("text-warn");
  });

  it("collapse toggle hides the aside and shows expand button", async () => {
    await renderRail();

    const aside = document.querySelector("aside");
    expect(aside).toBeInTheDocument();

    const hideBtn = screen.getByTitle("Hide telemetry");
    await userEvent.click(hideBtn);

    expect(document.querySelector("aside")).not.toBeInTheDocument();
    expect(screen.getByTitle("Show telemetry")).toBeInTheDocument();
  });

  it("expand button restores the aside", async () => {
    await renderRail();

    await userEvent.click(screen.getByTitle("Hide telemetry"));
    expect(document.querySelector("aside")).not.toBeInTheDocument();

    await userEvent.click(screen.getByTitle("Show telemetry"));
    expect(document.querySelector("aside")).toBeInTheDocument();
  });

  it("shows 'Waiting for activity…' when feed is empty", async () => {
    await renderRail({ feed: [] });
    expect(screen.getByText("Waiting for activity…")).toBeInTheDocument();
  });

  it("shows the pulse dot with the reflex class when telemetry is online", async () => {
    await renderRail({ telemetryStatus: "online" });
    const dots = document.querySelectorAll(".pulse-dot");
    expect(dots.length).toBeGreaterThan(0);
    const onlineDot = Array.from(dots).find((d) => d.classList.contains("bg-reflex"));
    expect(onlineDot).toBeInTheDocument();
  });

  it("shows the pulse dot with bad class when telemetry is offline", async () => {
    await renderRail({ telemetryStatus: "offline" });
    const dots = document.querySelectorAll(".pulse-dot");
    const badDot = Array.from(dots).find((d) => d.classList.contains("bg-bad"));
    expect(badDot).toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Selective-refetch tests (vital-affecting categories only, 5 s throttle)
  // ---------------------------------------------------------------------------

  it("refetches overview when a qualifying (user) entry arrives at feed head", async () => {
    // Render with an empty feed and a SHARED QueryClient so we can track api call
    // counts across rerenders without triggering a second initial fetch.
    vi.mocked(useAlfred).mockReturnValue({
      feed: [],
      telemetryStatus: "online",
      chatStatus: "online",
      chat: {} as never,
      telemetry: {} as never,
    });
    vi.mocked(api).mockResolvedValue(mockOverview);

    const sharedQc = makeQueryClient();
    const { rerender } = render(
      <QueryClientProvider client={sharedQc}>
        <MemoryRouter>
          <TelemetryRail />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // Wait for the initial useQuery fetch to settle (cost "—" or overview value).
    await screen.findByText("—", { selector: ".font-mono" }).catch(() => {
      // cost may already be visible from cached value — that's fine
    });

    // Record api call count after the initial query settles.
    const callsAfterMount = vi.mocked(api).mock.calls.length;

    // Simulate a new user_requests entry arriving at the feed head.
    const newEntry = {
      stream: "user_requests",
      id: `${Date.now()}-0`,
      event: { event_type: "user_request", content: "Turn on the lights" },
    };

    vi.mocked(useAlfred).mockReturnValue({
      feed: [newEntry],
      telemetryStatus: "online",
      chatStatus: "online",
      chat: {} as never,
      telemetry: {} as never,
    });

    // Reuse the SAME QueryClient — no second initial fetch is triggered.
    rerender(
      <QueryClientProvider client={sharedQc}>
        <MemoryRouter>
          <TelemetryRail />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // The effect fires and calls refetch() → api count increases.
    await vi.waitFor(() => {
      expect(vi.mocked(api).mock.calls.length).toBeGreaterThan(callsAfterMount);
    });
  });

  it("does NOT refetch overview when feed head is a home_state entry", async () => {
    // Render with an empty feed and a SHARED QueryClient.
    vi.mocked(useAlfred).mockReturnValue({
      feed: [],
      telemetryStatus: "online",
      chatStatus: "online",
      chat: {} as never,
      telemetry: {} as never,
    });
    vi.mocked(api).mockResolvedValue(mockOverview);

    const sharedQc = makeQueryClient();
    const { rerender } = render(
      <QueryClientProvider client={sharedQc}>
        <MemoryRouter>
          <TelemetryRail />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // Wait for the initial query to settle.
    await screen.findByText("—", { selector: ".font-mono" }).catch(() => {});

    const callsAfterMount = vi.mocked(api).mock.calls.length;

    // Simulate a home_state entry arriving — categorize() maps this to "home".
    const homeEntry = {
      stream: "home_state",
      id: `${Date.now()}-0`,
      event: { event_type: "state_changed", entity_id: "light.living_room", new_state: "on" },
    };

    vi.mocked(useAlfred).mockReturnValue({
      feed: [homeEntry],
      telemetryStatus: "online",
      chatStatus: "online",
      chat: {} as never,
      telemetry: {} as never,
    });

    rerender(
      <QueryClientProvider client={sharedQc}>
        <MemoryRouter>
          <TelemetryRail />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // Give React a tick to flush any effects.
    await new Promise((r) => setTimeout(r, 50));

    // api call count must NOT have increased — home_state must not trigger a refetch.
    expect(vi.mocked(api).mock.calls.length).toBe(callsAfterMount);
  });
});
