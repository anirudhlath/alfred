import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { IntegrationCard } from "./IntegrationCard";
import type { IntegrationInfo } from "@/lib/types";
import { HOME_SERVICE_INTEGRATION } from "./__fixtures__/integrations";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: vi.fn(), del: vi.fn() };
});
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

import { api, del } from "@/lib/api";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const WEATHER_INTEGRATION: IntegrationInfo = {
  name: "weather",
  category: "data",
  schema: {
    fields: {
      api_key: {
        label: "API Key",
        field_type: "password",
        required: true,
        placeholder: "sk-...",
        default: "",
        help_text: "From the provider dashboard",
        transient: false,
      },
      city: {
        label: "City",
        field_type: "text",
        required: false,
        placeholder: "San Francisco",
        default: "",
        help_text: "",
        transient: false,
      },
    },
  },
  configured: { api_key: true, city: false },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

function renderCard(props: Partial<Parameters<typeof IntegrationCard>[0]> = {}) {
  return render(
    <QueryClientProvider client={makeQC()}>
      <IntegrationCard integration={WEATHER_INTEGRATION} {...props} />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("IntegrationCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api).mockResolvedValue({ status: "ok" });
    vi.mocked(del).mockResolvedValue({});
  });

  it("renders SAVE, TEST CONNECTION, and CLEAR by default", async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText("WEATHER")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "SAVE" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "TEST CONNECTION" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "CLEAR" })).toBeInTheDocument();
  });

  it("showActions=false hides CLEAR but keeps SAVE and TEST CONNECTION", async () => {
    renderCard({ showActions: false });
    await waitFor(() => expect(screen.getByText("WEATHER")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "SAVE" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "TEST CONNECTION" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "CLEAR" })).toBeNull();
  });

  it("password toggle has aria-pressed and aria-label", async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText("API Key")).toBeInTheDocument());

    const showBtn = screen.getByRole("button", { name: "Show API Key" });
    expect(showBtn).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(showBtn);

    const hideBtn = screen.getByRole("button", { name: "Hide API Key" });
    expect(hideBtn).toHaveAttribute("aria-pressed", "true");
  });

  it("renders an external service badge and schema-driven fields for kind=service", async () => {
    renderCard({ integration: HOME_SERVICE_INTEGRATION });
    await waitFor(() => expect(screen.getByText("HOME-SERVICE")).toBeInTheDocument());
    expect(screen.getByText("external service")).toBeInTheDocument();
    expect(screen.getByText("Home Assistant URL")).toBeInTheDocument();
    expect(screen.getByText("Access Token")).toBeInTheDocument();
    // Unconfigured field pre-fills its schema default.
    expect(screen.getByDisplayValue("http://homeassistant.local:8123")).toBeInTheDocument();
  });

  it("does not render the service badge for adapters", async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText("WEATHER")).toBeInTheDocument());
    expect(screen.queryByText("external service")).toBeNull();
  });
});
