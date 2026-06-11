import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { MemoryPage } from "./MemoryPage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/api", () => ({ api: vi.fn() }));

import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

function renderPage() {
  return render(
    <QueryClientProvider client={makeQC()}>
      <MemoryPage />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

// HOT browse: significance is a numeric string (Redis hash decode)
const EPISODIC_HOT_ENTRY = {
  store: "hot",
  content: "User turned on lights at 9pm",
  significance: "0.87",
  source: "conversation",
};

// COLD browse: significance is a JSON string (SQLite TEXT column)
const EPISODIC_COLD_ENTRY = {
  store: "cold",
  summary: "Historical summary entry",
  significance: '{"overall":0.3,"safety":0.1}',
};

// SEARCH: significance is a nested object; score is a separate numeric
const EPISODIC_SEARCH_ENTRY_WITH_SIG = {
  store: "hot",
  score: 0.65,
  summary: "Search result with nested sig",
  significance: { overall: 0.42 },
};

// SEARCH: significance absent — should fall back to score
const EPISODIC_SEARCH_ENTRY_SCORE_ONLY = {
  store: "hot",
  score: 0.65,
  summary: "Search result without sig",
};

const EPISODIC_RESPONSE = {
  entries: [EPISODIC_HOT_ENTRY, EPISODIC_COLD_ENTRY],
};

const EPISODIC_SEARCH_RESPONSE = {
  entries: [EPISODIC_SEARCH_ENTRY_WITH_SIG, EPISODIC_SEARCH_ENTRY_SCORE_ONLY],
};

const SEMANTIC_RESPONSE = {
  files: [
    {
      name: "preferences.md",
      dir: "memory/semantic",
      content:
        "---\ntitle: Preferences\n---\n## Preferences\nUser prefers dark mode.",
      modified: "2026-06-10T10:00:00Z",
    },
    {
      name: "profile.md",
      dir: "memory/semantic",
      content: "## Profile\nLikes coffee in the morning.",
      modified: "2026-06-10T09:00:00Z",
    },
  ],
};

const ROUTINES_RESPONSE = {
  routines: [
    {
      name: "Morning lights",
      trigger_pattern: "time:07:00",
      confidence: 0.95,
      state: "active" as const,
      steps: [{ description: "Turn on kitchen lights" }, { description: "Set brightness to 70%" }],
      last_hit: "2026-06-10T07:00:00Z",
    },
    {
      name: "Dimming candidate",
      trigger_pattern: "time:22:00",
      confidence: 0.55,
      state: "candidate" as const,
      steps: [{ description: "Dim living room" }],
      last_hit: null,
    },
  ],
};

const SCRATCHPAD_RESPONSE = {
  content: "2026-06-10 10:00 — User asked about weather\n2026-06-10 10:01 — Answered.",
  pending_queue: 3,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("MemoryPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default: all queries return their fixture
    vi.mocked(api).mockImplementation((url: string) => {
      if (url.startsWith("/api/admin/memory/episodic")) return Promise.resolve(EPISODIC_RESPONSE);
      if (url === "/api/admin/memory/semantic") return Promise.resolve(SEMANTIC_RESPONSE);
      if (url === "/api/admin/memory/routines") return Promise.resolve(ROUTINES_RESPONSE);
      if (url === "/api/admin/memory/scratchpad") return Promise.resolve(SCRATCHPAD_RESPONSE);
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
  });

  // 1. Episodic tab renders hot badge, significance, and content
  it("renders episodic entries with store badge, significance, and content", async () => {
    renderPage();

    // EPISODIC tab is default
    await waitFor(() => {
      expect(screen.getByText("User turned on lights at 9pm")).toBeInTheDocument();
    });

    // Store badges
    expect(screen.getByText("hot")).toBeInTheDocument();
    expect(screen.getByText("cold")).toBeInTheDocument();

    // HOT: significance is numeric string "0.87" → "sig 0.87"
    expect(screen.getByText("sig 0.87")).toBeInTheDocument();
    // COLD: significance is JSON string '{"overall":0.3,...}' → "sig 0.30"
    expect(screen.getByText("sig 0.30")).toBeInTheDocument();

    // Source label on hot entry
    expect(screen.getByText("conversation")).toBeInTheDocument();

    // Cold entry uses summary field
    expect(screen.getByText("Historical summary entry")).toBeInTheDocument();
  });

  // 2. Shape-aware significance: HOT numeric string, COLD JSON string, SEARCH object, SEARCH score-only
  it("renders correct significance for all three backend shapes", async () => {
    vi.mocked(api).mockImplementation((url: string) => {
      if (url.startsWith("/api/admin/memory/episodic"))
        return Promise.resolve(EPISODIC_SEARCH_RESPONSE);
      return Promise.reject(new Error("unexpected"));
    });
    renderPage();

    await waitFor(() => {
      // SEARCH shape with nested object significance → uses .overall = 0.42
      expect(screen.getByText("sig 0.42")).toBeInTheDocument();
    });

    // SEARCH shape with no significance → falls back to score = 0.65
    expect(screen.getByText("sig 0.65")).toBeInTheDocument();
  });

  // 3. Search input fires query with ?q= on Enter
  it("fires episodic search query with ?q= param when Enter is pressed", async () => {
    vi.mocked(api).mockImplementation((url: string) => {
      if (url === "/api/admin/memory/episodic") return Promise.resolve(EPISODIC_RESPONSE);
      if (url === "/api/admin/memory/episodic?q=lights")
        return Promise.resolve(EPISODIC_SEARCH_RESPONSE);
      return Promise.reject(new Error("unexpected"));
    });

    renderPage();

    // Wait for initial load
    await waitFor(() => {
      expect(screen.getByText("User turned on lights at 9pm")).toBeInTheDocument();
    });

    // Type in search box and press Enter
    const input = screen.getByPlaceholderText(
      "Semantic search (Enter) — empty shows recent",
    );
    await userEvent.type(input, "lights");
    await userEvent.keyboard("{Enter}");

    // Now the search result should be shown
    await waitFor(() => {
      expect(screen.getByText("Search result with nested sig")).toBeInTheDocument();
    });

    // Verify the API was called with the query param
    expect(vi.mocked(api)).toHaveBeenCalledWith(
      "/api/admin/memory/episodic?q=lights",
    );
  });

  // 4. Semantic tab renders files with frontmatter stripped
  it("renders semantic files with YAML frontmatter stripped", async () => {
    renderPage();

    // Switch to SEMANTIC tab
    await userEvent.click(screen.getByRole("tab", { name: "SEMANTIC" }));

    await waitFor(() => {
      // Path header rendered
      expect(screen.getByText("memory/semantic/preferences.md")).toBeInTheDocument();
    });

    // Frontmatter (--- title: Preferences ---) should NOT appear
    expect(screen.queryByText("title: Preferences")).not.toBeInTheDocument();

    // Markdown content should render
    expect(screen.getByText(/User prefers dark mode/)).toBeInTheDocument();
    expect(screen.getByText(/Likes coffee in the morning/)).toBeInTheDocument();
  });

  // 5. Routines tab renders routines with state tones and steps
  it("renders routines with correct state badges and steps", async () => {
    renderPage();

    await userEvent.click(screen.getByRole("tab", { name: "ROUTINES" }));

    await waitFor(() => {
      expect(screen.getByText("Morning lights")).toBeInTheDocument();
    });

    // State badges
    const activeBadge = screen.getByText("active");
    expect(activeBadge).toBeInTheDocument();
    // The active badge should have the ok color class
    expect(activeBadge.className).toContain("text-ok");

    const candidateBadge = screen.getByText("candidate");
    expect(candidateBadge.className).toContain("text-warn");

    // Confidence
    expect(screen.getByText("conf 0.95")).toBeInTheDocument();
    expect(screen.getByText("conf 0.55")).toBeInTheDocument();

    // Trigger patterns
    expect(screen.getByText("time:07:00")).toBeInTheDocument();
    expect(screen.getByText("time:22:00")).toBeInTheDocument();

    // Steps
    expect(screen.getByText("Turn on kitchen lights")).toBeInTheDocument();
    expect(screen.getByText("Set brightness to 70%")).toBeInTheDocument();
    expect(screen.getByText("Dim living room")).toBeInTheDocument();
  });

  // 6. Routines empty state message
  it("shows empty state when routines list is empty", async () => {
    vi.mocked(api).mockImplementation((url: string) => {
      if (url === "/api/admin/memory/routines") return Promise.resolve({ routines: [] });
      return Promise.reject(new Error("unexpected"));
    });

    renderPage();

    await userEvent.click(screen.getByRole("tab", { name: "ROUTINES" }));

    await waitFor(() => {
      expect(screen.getByText("No routines learned yet.")).toBeInTheDocument();
    });
  });

  // 7. Scratchpad tab renders content and pending queue count
  it("renders scratchpad content and pending queue count", async () => {
    renderPage();

    await userEvent.click(screen.getByRole("tab", { name: "SCRATCHPAD" }));

    await waitFor(() => {
      expect(screen.getByText(/3 observations queued for drain/)).toBeInTheDocument();
    });

    expect(
      screen.getByText(/User asked about weather/),
    ).toBeInTheDocument();
  });

  // 8. Scratchpad shows empty message when content is empty
  it("shows empty message when scratchpad content is empty string", async () => {
    vi.mocked(api).mockImplementation((url: string) => {
      if (url === "/api/admin/memory/scratchpad")
        return Promise.resolve({ content: "", pending_queue: 0 });
      return Promise.reject(new Error("unexpected"));
    });

    renderPage();

    await userEvent.click(screen.getByRole("tab", { name: "SCRATCHPAD" }));

    await waitFor(() => {
      expect(screen.getByText("Scratchpad is empty.")).toBeInTheDocument();
    });
    expect(screen.getByText("0 observations queued for drain")).toBeInTheDocument();
  });

  // 9. Tab switching works — correct content appears per tab
  it("switches between all four tabs correctly", async () => {
    renderPage();

    // Default: EPISODIC visible
    await waitFor(() => {
      expect(screen.getByPlaceholderText("Semantic search (Enter) — empty shows recent")).toBeInTheDocument();
    });

    // Switch to SEMANTIC
    await userEvent.click(screen.getByRole("tab", { name: "SEMANTIC" }));
    await waitFor(() => {
      expect(screen.getByText("memory/semantic/preferences.md")).toBeInTheDocument();
    });

    // Switch to ROUTINES
    await userEvent.click(screen.getByRole("tab", { name: "ROUTINES" }));
    await waitFor(() => {
      expect(screen.getByText("Morning lights")).toBeInTheDocument();
    });

    // Switch to SCRATCHPAD
    await userEvent.click(screen.getByRole("tab", { name: "SCRATCHPAD" }));
    await waitFor(() => {
      expect(screen.getByText(/observations queued for drain/)).toBeInTheDocument();
    });

    // Back to EPISODIC
    await userEvent.click(screen.getByRole("tab", { name: "EPISODIC" }));
    expect(screen.getByPlaceholderText("Semantic search (Enter) — empty shows recent")).toBeInTheDocument();
  });
});
