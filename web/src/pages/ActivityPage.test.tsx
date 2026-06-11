import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { FeedEntry } from "@/shell/AlfredProvider";
import { ActivityPage } from "./ActivityPage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/shell/AlfredProvider", () => ({
  useAlfredFeed: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: vi.fn(),
}));

import { useAlfredFeed } from "@/shell/AlfredProvider";
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const now = Date.now();

const makeFeed = (): FeedEntry[] => [
  {
    stream: "reflex_observations",
    id: `${now - 3000}-0`,
    event: { event_type: "reflex_observation", action: { tool_name: "turn_on_lights" } },
  },
  {
    stream: "user_requests",
    id: `${now - 6000}-0`,
    event: { event_type: "user_request", content: "What's the temperature?" },
  },
  {
    stream: "home_state",
    id: `${now - 9000}-0`,
    event: { event_type: "state_changed", entity_id: "sensor.temp", new_state: "21°C" },
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

function renderPage(feed = makeFeed()) {
  vi.mocked(useAlfredFeed).mockReturnValue(feed);
  return render(
    <QueryClientProvider client={makeQC()}>
      <ActivityPage />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ActivityPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // 1. Entries render with categories and summaries
  it("renders live feed entries with categories and summaries", () => {
    renderPage();

    // summarize() outputs
    expect(screen.getByText("turn_on_lights")).toBeInTheDocument();
    expect(screen.getByText(/What's the temperature\?/)).toBeInTheDocument();
    expect(screen.getByText("sensor.temp → 21°C")).toBeInTheDocument();

    // category labels
    expect(screen.getByText("reflex")).toBeInTheDocument();
    expect(screen.getByText("user")).toBeInTheDocument();
    expect(screen.getByText("home")).toBeInTheDocument();
  });

  // 2. Clicking a stream badge filters + fires the backfill query
  it("filters entries and fires backfill query when a stream badge is clicked", async () => {
    const mockPage = {
      entries: [
        {
          id: `${now - 60000}-0`,
          event: { event_type: "reflex_observation", action: { tool_name: "archived_action" } },
        },
      ],
      next_before: null,
    };
    vi.mocked(api).mockResolvedValue(mockPage);

    renderPage();

    // Multiple elements may contain "reflex_observations" (badge + feed row stream span).
    // Target the badge specifically by its data-slot attribute.
    const reflexBadges = screen.getAllByText("reflex_observations");
    const reflexBadge = reflexBadges.find((el) => el.getAttribute("data-slot") === "badge")!;
    await userEvent.click(reflexBadge);

    // The api must have been called for the stream history
    await waitFor(() => {
      expect(vi.mocked(api)).toHaveBeenCalledWith(
        "/api/admin/streams/reflex_observations?count=100",
      );
    });

    // Only reflex_observations entries visible (plus backfill after query resolves)
    await waitFor(() => {
      expect(screen.getByText("archived_action")).toBeInTheDocument();
    });

    // Other-stream summaries should no longer appear
    expect(screen.queryByText(/What's the temperature\?/)).not.toBeInTheDocument();
  });

  // 3. Pause freezes the list; resume un-freezes
  it("pause freezes the feed; resume shows updated entries", async () => {
    const initialFeed = makeFeed();
    vi.mocked(useAlfredFeed).mockReturnValue(initialFeed);

    const qc = makeQC();
    const { rerender } = render(
      <QueryClientProvider client={qc}>
        <ActivityPage />
      </QueryClientProvider>,
    );

    // Verify initial entry visible
    expect(screen.getByText("turn_on_lights")).toBeInTheDocument();

    // Click PAUSE
    const pauseBtn = screen.getByText("PAUSE");
    await userEvent.click(pauseBtn);
    expect(screen.getByText("RESUME")).toBeInTheDocument();

    // Grow the feed while paused — new entry added
    const grownFeed: FeedEntry[] = [
      {
        stream: "notifications",
        id: `${now}-0`,
        event: { event_type: "notification", title: "Cost alert" },
      },
      ...initialFeed,
    ];
    vi.mocked(useAlfredFeed).mockReturnValue(grownFeed);

    rerender(
      <QueryClientProvider client={qc}>
        <ActivityPage />
      </QueryClientProvider>,
    );

    // New entry should NOT be visible while paused
    expect(screen.queryByText("Cost alert")).not.toBeInTheDocument();
    // Old entries still visible
    expect(screen.getByText("turn_on_lights")).toBeInTheDocument();

    // Click RESUME
    const resumeBtn = screen.getByText("RESUME");
    await userEvent.click(resumeBtn);

    // Now the new entry should be visible
    expect(screen.getByText("Cost alert")).toBeInTheDocument();
  });

  // 4. Clicking an entry opens the inspector with pretty JSON
  it("clicking an entry opens the EventInspector showing JSON", async () => {
    renderPage();

    const entryBtn = screen.getByText("turn_on_lights").closest("button");
    expect(entryBtn).toBeInTheDocument();
    await userEvent.click(entryBtn!);

    // Inspector sheet title contains the stream name.
    // Multiple elements may contain "reflex_observations" (badge, feed row, sheet title).
    // Target the SheetTitle via its data-slot attribute.
    await waitFor(() => {
      const titles = screen.getAllByText(/reflex_observations/);
      const sheetTitle = titles.find((el) => el.getAttribute("data-slot") === "sheet-title");
      expect(sheetTitle).toBeInTheDocument();
    });

    // Pretty-printed JSON should contain the event_type key
    expect(screen.getByText(/"event_type"/)).toBeInTheDocument();
  });

  // 5. Empty state message
  it("shows empty-state message when feed has no entries", () => {
    renderPage([]);
    expect(
      screen.getByText("No activity yet — the system is quiet."),
    ).toBeInTheDocument();
  });

  // 6. Merged entries are sorted newest-first when backfill interleaves with live
  it("sorts merged live + backfill entries newest-first by stream id", async () => {
    const stream = "events";
    // Live has ids 5000-0 and 1000-0
    const liveFeed: FeedEntry[] = [
      { stream, id: "5000-0", event: { event_type: "state_changed", entity_id: "a", new_state: "live-5000" } },
      { stream, id: "1000-0", event: { event_type: "state_changed", entity_id: "b", new_state: "live-1000" } },
    ];
    // Backfill has id 3000-0 — newer than live 1000-0 but older than live 5000-0
    const mockPage = {
      entries: [
        { id: "3000-0", event: { event_type: "state_changed", entity_id: "c", new_state: "backfill-3000" } },
      ],
      next_before: null,
    };
    vi.mocked(api).mockResolvedValue(mockPage);
    vi.mocked(useAlfredFeed).mockReturnValue(liveFeed);

    render(
      <QueryClientProvider client={makeQC()}>
        <ActivityPage />
      </QueryClientProvider>,
    );

    // Click the "events" stream badge
    const badges = screen.getAllByText("events");
    const badge = badges.find((el) => el.getAttribute("data-slot") === "badge")!;
    await userEvent.click(badge);

    // Wait for backfill to resolve and render
    await waitFor(() => {
      expect(screen.getByText("c → backfill-3000")).toBeInTheDocument();
    });

    // Assert DOM order: 5000 first, then 3000, then 1000
    const summaries = screen
      .getAllByText(/live-5000|backfill-3000|live-1000/)
      .map((el) => el.textContent);
    expect(summaries).toEqual(["a → live-5000", "c → backfill-3000", "b → live-1000"]);
  });
});
