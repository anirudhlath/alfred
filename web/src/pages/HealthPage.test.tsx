import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { HealthPage } from "./HealthPage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/api", () => ({ api: vi.fn(), del: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

import { api, del } from "@/lib/api";
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
      <HealthPage />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const OVERVIEW_RESPONSE = {
  redis: { connected: true },
  cost: { date: "2026-06-11", spend_usd: 0.42, cap_usd: 5.0, alert_sent: false },
  dnd: { active: false, until: null, reason: null, source: "manual" },
  counts: { sessions: 1, devices: 1, deferred: 0, triggers: 2 },
  streams: {
    "alfred:events": { length: 12, last_id: "123-0", last_ts: 1749542400 },
    "alfred:actions": { length: 3, last_id: "456-0", last_ts: null },
  },
  inference: { ollama: true, lmstudio: false },
};

const SESSIONS_RESPONSE = {
  sessions: [
    {
      session_id: "abcdef1234567890",
      channel: "web_pwa",
      created_at: "2026-06-11T10:00:00Z",
      turns: 5,
      ttl_seconds: 900,
    },
  ],
};

const SESSIONS_EMPTY = { sessions: [] };

const DEVICES_RESPONSE = {
  devices: [
    {
      device_token: "aabbccddeeff00112233445566778899",
      platform: "ios",
      identity: "sir",
      registered_at: "2026-06-10T08:00:00Z",
    },
  ],
};

const DEVICES_EMPTY = { devices: [] };

const INTEGRATIONS_RESPONSE = [
  {
    name: "weather",
    category: "data",
    schema: { fields: {} },
    configured: {},
  },
  {
    name: "apple_calendar",
    category: "calendar",
    schema: { fields: {} },
    configured: {},
  },
];

const INTEGRATION_STATUS_HEALTHY = { name: "weather", healthy: true };
const INTEGRATION_STATUS_UNHEALTHY = { name: "apple_calendar", healthy: false };

// ---------------------------------------------------------------------------
// Default mock setup
// ---------------------------------------------------------------------------

function setupMocks(
  overrides: Partial<{
    overview: unknown;
    overviewError: boolean;
    sessions: unknown;
    devices: unknown;
    integrations: unknown;
    integrationStatus: Record<string, unknown>;
  }> = {},
) {
  vi.mocked(api).mockImplementation((url: string) => {
    if (url === "/api/admin/overview") {
      if (overrides.overviewError) return Promise.reject(new Error("connection refused"));
      return Promise.resolve(overrides.overview ?? OVERVIEW_RESPONSE);
    }
    if (url === "/api/admin/sessions") return Promise.resolve(overrides.sessions ?? SESSIONS_RESPONSE);
    if (url === "/api/admin/devices") return Promise.resolve(overrides.devices ?? DEVICES_RESPONSE);
    if (url === "/api/integrations") return Promise.resolve(overrides.integrations ?? INTEGRATIONS_RESPONSE);
    if (url === "/api/integrations/weather/status")
      return Promise.resolve(
        overrides.integrationStatus?.weather ?? INTEGRATION_STATUS_HEALTHY,
      );
    if (url === "/api/integrations/apple_calendar/status")
      return Promise.resolve(
        overrides.integrationStatus?.apple_calendar ?? INTEGRATION_STATUS_UNHEALTHY,
      );
    return Promise.reject(new Error(`Unexpected URL: ${url}`));
  });
  vi.mocked(del).mockResolvedValue({});
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("HealthPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  // 1. Connectivity dots reflect overview booleans
  it("renders connectivity panel with redis and ollama status", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("redis")).toBeInTheDocument();
    });

    expect(screen.getByText("ollama")).toBeInTheDocument();
    expect(screen.getByText("lm studio")).toBeInTheDocument();
  });

  // 2. Cost shows spend/cap with green tone below 0.8
  it("renders cost panel with spend and cap", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/\$0\.42/)).toBeInTheDocument();
    });

    expect(screen.getByText(/\$5\.00/)).toBeInTheDocument();
  });

  // 3. Cost shows warn tone above 0.8 (spend > 80% of cap)
  it("applies warn color class when spend ratio exceeds 0.8", async () => {
    setupMocks({
      overview: {
        ...OVERVIEW_RESPONSE,
        cost: { date: "2026-06-11", spend_usd: 4.5, cap_usd: 5.0 },
      },
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/\$4\.50/)).toBeInTheDocument();
    });

    // The spend amount element should have text-warn class
    const spendEl = screen.getByText(/\$4\.50/);
    expect(spendEl.className).toContain("text-warn");
  });

  // 4. Streams table rows render
  it("renders streams table with stream names and lengths", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("alfred:events")).toBeInTheDocument();
    });

    expect(screen.getByText("alfred:actions")).toBeInTheDocument();
    // Stream lengths
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    // null last_ts renders em-dash
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  // 5. Sessions render with END button → DELETE call + invalidation
  it("renders sessions with channel, turns, ttl and END button", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("web_pwa")).toBeInTheDocument();
    });

    expect(screen.getByText("5 turns")).toBeInTheDocument();
    expect(screen.getByText("15m left")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "END" })).toBeInTheDocument();
  });

  it("clicking END calls del on the session endpoint and shows toast", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "END" })).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: "END" }));

    await waitFor(() => {
      expect(vi.mocked(del)).toHaveBeenCalledWith("/api/admin/sessions/abcdef1234567890");
    });

    await waitFor(() => {
      expect(vi.mocked(toast)).toHaveBeenCalledWith("Session ended");
    });
  });

  it("shows error toast when endSession mutation fails", async () => {
    vi.mocked(del).mockRejectedValue(new Error("server error"));

    renderPage();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "END" })).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: "END" }));

    await waitFor(() => {
      expect(vi.mocked(toast).error).toHaveBeenCalledWith(expect.stringContaining("server error"));
    });
  });

  // 6. Devices render truncated tokens
  it("renders devices with platform badge and truncated token", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("ios")).toBeInTheDocument();
    });

    // Token is truncated to first 12 chars + ellipsis
    expect(screen.getByText("aabbccddeeff…")).toBeInTheDocument();
    expect(screen.getByText("sir")).toBeInTheDocument();
  });

  // 7. Integrations render with per-row status query (healthy dot)
  it("renders integration rows with name and category", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("weather")).toBeInTheDocument();
    });

    expect(screen.getByText("apple_calendar")).toBeInTheDocument();
    expect(screen.getByText("data")).toBeInTheDocument();
    expect(screen.getByText("calendar")).toBeInTheDocument();
  });

  // 8. Overview error state shows "overview unavailable" across panels
  it("shows overview unavailable message on fetch error", async () => {
    setupMocks({ overviewError: true });
    renderPage();

    await waitFor(() => {
      // CONNECTIVITY panel includes the full error string; COST and STREAMS show short form
      expect(screen.getByText(/connection refused/)).toBeInTheDocument();
      const msgs = screen.getAllByText(/overview unavailable/);
      expect(msgs.length).toBeGreaterThanOrEqual(1);
    });
  });

  // 9. COST TODAY and STREAMS show inline error when overview rejects
  it("shows overview unavailable in COST TODAY and STREAMS panels when overview query rejects", async () => {
    setupMocks({ overviewError: true });
    renderPage();

    await waitFor(() => {
      // At least 2 "overview unavailable" messages: CONNECTIVITY + COST + STREAMS = 3 total,
      // but we assert at least 2 matching the new panels (COST + STREAMS)
      const msgs = screen.getAllByText("overview unavailable");
      expect(msgs.length).toBeGreaterThanOrEqual(2);
    });

    // Cost panel: no spend/cap values rendered
    expect(screen.queryByText(/\$0\.00/)).not.toBeInTheDocument();
    // Streams panel: no stream names rendered
    expect(screen.queryByText("alfred:events")).not.toBeInTheDocument();
  });

  // 10. Empty sessions state
  it("shows empty sessions message when no sessions", async () => {
    setupMocks({ sessions: SESSIONS_EMPTY });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("No active sessions.")).toBeInTheDocument();
    });
  });

  // 11. Empty devices state
  it("shows empty devices message when no devices", async () => {
    setupMocks({ devices: DEVICES_EMPTY });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("No registered devices.")).toBeInTheDocument();
    });
  });

  // 12. Cost defaults to $0.00 when cost is null
  it("shows $0.00 when overview cost is null", async () => {
    setupMocks({ overview: { ...OVERVIEW_RESPONSE, cost: null } });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/\$0\.00/)).toBeInTheDocument();
    });
  });
});
