import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { TriggersPage } from "./TriggersPage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/api", () => ({ api: vi.fn(), post: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

import { api, post } from "@/lib/api";
import { toast } from "sonner";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

function renderPage() {
  return render(
    <QueryClientProvider client={makeQC()}>
      <TriggersPage />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const TRIGGER_NORMAL = {
  trigger_id: "t1",
  name: "Morning Routine",
  trigger_type: "time",
  enabled: true,
  one_shot: false,
  created_by: "alfred",
  last_fired: "2026-06-10T07:00:00Z",
};

const TRIGGER_ONE_SHOT = {
  trigger_id: "t2",
  name: "One-Off Alert",
  trigger_type: "event",
  enabled: false,
  one_shot: true,
  created_by: "user",
  last_fired: null,
};

const TRIGGERS_RESPONSE = { triggers: [TRIGGER_NORMAL, TRIGGER_ONE_SHOT] };

const OVERVIEW_RESPONSE = {
  redis: { connected: true },
  cost: null,
  dnd: { active: false, until: null, reason: null, source: "manual" },
  counts: { sessions: 1, devices: 1, deferred: 2, triggers: 2 },
  streams: {},
  inference: { ollama: true, lmstudio: false },
};

const OVERVIEW_DND_ON = {
  ...OVERVIEW_RESPONSE,
  dnd: { active: true, until: null, reason: "sleeping", source: "manual" },
};

const DEFERRED_RESPONSE = {
  notifications: [
    {
      notification_id: "n1",
      title: "Low battery",
      body: "Living room sensor at 5%",
      urgency: "normal",
      source: "trigger",
      timestamp: "2026-06-10T22:00:00Z",
    },
  ],
};

const DEFERRED_EMPTY = { notifications: [] };

// Stream IDs encode ms timestamp as the prefix
// 1749542400000 → 2026-06-10T08:00:00.000Z → 08:00:00 in en-GB
const HISTORY_RESPONSE = {
  entries: [
    {
      id: "1749542400000-0",
      event: { title: "Sunset alert", body: "Sun has set", urgency: "low" },
    },
  ],
};

const HISTORY_EMPTY = { entries: [] };

// ---------------------------------------------------------------------------
// Default mock setup
// ---------------------------------------------------------------------------

function setupMocks(overrides: Partial<{
  triggers: unknown;
  overview: unknown;
  deferred: unknown;
  history: unknown;
}> = {}) {
  vi.mocked(api).mockImplementation((url: string) => {
    if (url === "/api/admin/triggers")
      return Promise.resolve(overrides.triggers ?? TRIGGERS_RESPONSE);
    if (url === "/api/admin/overview")
      return Promise.resolve(overrides.overview ?? OVERVIEW_RESPONSE);
    if (url === "/api/admin/notifications/deferred")
      return Promise.resolve(overrides.deferred ?? DEFERRED_RESPONSE);
    if (url === "/api/admin/streams/notifications?count=20")
      return Promise.resolve(overrides.history ?? HISTORY_RESPONSE);
    return Promise.reject(new Error(`Unexpected URL: ${url}`));
  });
  vi.mocked(post).mockResolvedValue({});
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TriggersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  // 1. Triggers render with name, type, created_by, last_fired
  it("renders trigger rows with name, type, created_by, and last_fired", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Morning Routine")).toBeInTheDocument();
    });

    // Trigger type badge
    expect(screen.getAllByText("time").length).toBeGreaterThan(0);

    // Metadata line
    expect(screen.getByText(/by alfred/)).toBeInTheDocument();
    expect(screen.getByText(/2026-06-10T07:00:00Z/)).toBeInTheDocument();

    // Second trigger
    expect(screen.getByText("One-Off Alert")).toBeInTheDocument();
    expect(screen.getAllByText("event").length).toBeGreaterThan(0);
    expect(screen.getByText(/by user/)).toBeInTheDocument();
    expect(screen.getByText(/last fired never/)).toBeInTheDocument();
  });

  // 2. one-shot badge appears only for one-shot triggers
  it("shows one-shot badge only for triggers with one_shot=true", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Morning Routine")).toBeInTheDocument();
    });

    // one-shot badge should exist exactly once (for TRIGGER_ONE_SHOT)
    expect(screen.getAllByText("one-shot")).toHaveLength(1);
  });

  // 3. Enable switch toggle POSTs to /api/admin/triggers/<id>/enabled with { enabled }
  it("toggles enable switch and POSTs enabled state", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Morning Routine")).toBeInTheDocument();
    });

    // There are 2 triggers + 1 DND switch = 3 switches total
    // Morning Routine is checked=true (index 0), One-Off is checked=false (index 1)
    const switches = screen.getAllByRole("switch");
    // First switch = Morning Routine (enabled=true) — toggle it OFF
    await userEvent.click(switches[0]);

    await waitFor(() => {
      expect(vi.mocked(post)).toHaveBeenCalledWith(
        "/api/admin/triggers/t1/enabled",
        { enabled: false },
      );
    });
  });

  it("optimistically flips the switch before the queued mutation settles", async () => {
    // post() never resolves → the mutation stays pending; the switch must still
    // flip immediately via the optimistic cache update.
    vi.mocked(post).mockReturnValue(new Promise(() => {}));
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Morning Routine")).toBeInTheDocument();
    });

    const switches = screen.getAllByRole("switch");
    // Morning Routine starts enabled (aria-checked=true) — toggle it OFF.
    expect(switches[0]).toBeChecked();
    await userEvent.click(switches[0]);

    await waitFor(() => {
      expect(screen.getAllByRole("switch")[0]).not.toBeChecked();
    });
  });

  it("rolls back the optimistic switch when the mutation fails", async () => {
    vi.mocked(post).mockRejectedValue(new Error("boom"));
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Morning Routine")).toBeInTheDocument();
    });

    const switches = screen.getAllByRole("switch");
    expect(switches[0]).toBeChecked();
    await userEvent.click(switches[0]);

    // After the error the switch returns to its original checked state.
    await waitFor(() => {
      expect(vi.mocked(toast).error).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getAllByRole("switch")[0]).toBeChecked();
    });
  });

  it("toggles disabled trigger ON and POSTs enabled=true", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("One-Off Alert")).toBeInTheDocument();
    });

    const switches = screen.getAllByRole("switch");
    // Switch at index 1 = One-Off Alert (enabled=false) — toggle it ON
    await userEvent.click(switches[1]);

    await waitFor(() => {
      expect(vi.mocked(post)).toHaveBeenCalledWith(
        "/api/admin/triggers/t2/enabled",
        { enabled: true },
      );
    });
  });

  // 4. FIRE button POSTs to .../fire
  it("fires trigger when FIRE button is clicked", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Morning Routine")).toBeInTheDocument();
    });

    const fireButtons = screen.getAllByRole("button", { name: "FIRE" });
    await userEvent.click(fireButtons[0]);

    await waitFor(() => {
      expect(vi.mocked(post)).toHaveBeenCalledWith("/api/admin/triggers/t1/fire");
    });
  });

  it("shows toast after firing a trigger", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Morning Routine")).toBeInTheDocument();
    });

    const fireButtons = screen.getAllByRole("button", { name: "FIRE" });
    await userEvent.click(fireButtons[0]);

    await waitFor(() => {
      expect(vi.mocked(toast)).toHaveBeenCalledWith("Trigger fired");
    });
  });

  // 5. DND switch POSTs { active }
  it("toggles DND switch and POSTs active state", async () => {
    renderPage();

    // Wait for triggers to load so all switches are in the DOM
    await waitFor(() => {
      expect(screen.getByText("Morning Routine")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText("OFF")).toBeInTheDocument();
    });

    // DND switch is the 3rd switch (index 2, after 2 trigger switches)
    const switches = screen.getAllByRole("switch");
    expect(switches).toHaveLength(3);
    const dndSwitch = switches[2];
    await userEvent.click(dndSwitch);

    await waitFor(() => {
      expect(vi.mocked(post)).toHaveBeenCalledWith("/api/admin/dnd", { active: true });
    });
  });

  it("shows DND ON with reason when dnd.active=true and reason present", async () => {
    setupMocks({ overview: OVERVIEW_DND_ON });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("ON — sleeping")).toBeInTheDocument();
    });
  });

  // 6. DRAIN disabled when queue empty, POSTs when non-empty
  it("disables DRAIN NOW button when deferred queue is empty", async () => {
    setupMocks({ deferred: DEFERRED_EMPTY });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Queue empty.")).toBeInTheDocument();
    });

    const drainBtn = screen.getByRole("button", { name: "DRAIN NOW" });
    expect(drainBtn).toBeDisabled();
  });

  it("enables DRAIN NOW button when deferred queue is non-empty and POSTs on click", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Low battery")).toBeInTheDocument();
    });

    const drainBtn = screen.getByRole("button", { name: "DRAIN NOW" });
    expect(drainBtn).not.toBeDisabled();

    await userEvent.click(drainBtn);

    await waitFor(() => {
      expect(vi.mocked(post)).toHaveBeenCalledWith("/api/admin/notifications/drain");
    });
  });

  // 7. History entries render
  it("renders notification history entries with time, urgency, title, body", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Sunset alert")).toBeInTheDocument();
    });

    expect(screen.getByText("Sun has set")).toBeInTheDocument();
    // urgency appears in the header line
    expect(screen.getByText(/low/)).toBeInTheDocument();
    // timeOf("1749542400000-0") → formatted time string (locale-dependent, just check it renders something)
    // We only verify the entry rendered successfully via title/body above
  });

  // 8. Empty states
  it("shows empty trigger state when triggers list is empty", async () => {
    setupMocks({ triggers: { triggers: [] } });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("No active triggers.")).toBeInTheDocument();
    });
  });

  it("shows empty history state when history entries are empty", async () => {
    setupMocks({ history: HISTORY_EMPTY });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("No notifications yet.")).toBeInTheDocument();
    });
  });

  // 9. Deferred notification body renders
  it("renders deferred notification urgency, title, and body", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Low battery")).toBeInTheDocument();
    });

    expect(screen.getByText("Living room sensor at 5%")).toBeInTheDocument();
    expect(screen.getByText("normal")).toBeInTheDocument();
  });
});
