import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { SettingsPage } from "./SettingsPage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: vi.fn(), post: vi.fn(), del: vi.fn() };
});
vi.mock("@/lib/webauthn", () => ({ logout: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

import { api, del } from "@/lib/api";
import { logout } from "@/lib/webauthn";
import { toast } from "sonner";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const WEATHER = {
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

const INTEGRATIONS = [WEATHER];

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

function renderPage() {
  return render(
    <QueryClientProvider client={makeQC()}>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function setupMocks(integrations: unknown = INTEGRATIONS) {
  vi.mocked(api).mockImplementation((url: string) => {
    if (url === "/api/integrations") return Promise.resolve(integrations);
    if (url.endsWith("/status")) return Promise.resolve({ name: "weather", healthy: true });
    if (url.endsWith("/credentials")) return Promise.resolve({ status: "ok" }); // PUT
    return Promise.reject(new Error(`Unexpected URL: ${url}`));
  });
  vi.mocked(del).mockResolvedValue({});
  vi.mocked(logout).mockResolvedValue(undefined);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  it("renders one card per integration with fields from the schema", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("WEATHER")).toBeInTheDocument());
    expect(screen.getByText("API Key")).toBeInTheDocument();
    expect(screen.getByText("City")).toBeInTheDocument();
    expect(screen.getByText("From the provider dashboard")).toBeInTheDocument();
    // Required+configured field shows "configured" badge
    expect(screen.getByText("configured")).toBeInTheDocument();
  });

  it("save PUTs only the non-empty fields the user entered", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("API Key")).toBeInTheDocument());

    // Enter only the city; leave the (configured) password blank.
    const cityInput = screen.getByPlaceholderText("San Francisco");
    await userEvent.type(cityInput, "Boston");
    await userEvent.click(screen.getByRole("button", { name: "SAVE" }));

    await waitFor(() => {
      expect(vi.mocked(api)).toHaveBeenCalledWith("/api/integrations/weather/credentials", {
        method: "PUT",
        body: JSON.stringify({ city: "Boston" }),
      });
    });
  });

  it("clear DELETEs the integration credentials", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("WEATHER")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "CLEAR" }));
    await waitFor(() => {
      expect(vi.mocked(del)).toHaveBeenCalledWith("/api/integrations/weather/credentials");
    });
  });

  it("test button toasts a healthy connection", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("WEATHER")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "TEST CONNECTION" }));
    await waitFor(() => {
      expect(vi.mocked(api)).toHaveBeenCalledWith("/api/integrations/weather/status");
      expect(vi.mocked(toast.success)).toHaveBeenCalledWith("weather: connection healthy");
    });
  });

  it("test button toasts an unhealthy connection", async () => {
    vi.mocked(api).mockImplementation((url: string) => {
      if (url === "/api/integrations") return Promise.resolve(INTEGRATIONS);
      if (url.endsWith("/status")) return Promise.resolve({ name: "weather", healthy: false });
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    renderPage();
    await waitFor(() => expect(screen.getByText("WEATHER")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "TEST CONNECTION" }));
    await waitFor(() => {
      expect(vi.mocked(toast.error)).toHaveBeenCalledWith("weather: unhealthy");
    });
  });

  it("toggles password field visibility", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("API Key")).toBeInTheDocument());
    const pwInput = screen.getByPlaceholderText("••••••••") as HTMLInputElement;
    expect(pwInput.type).toBe("password");
    await userEvent.click(screen.getByRole("button", { name: "SHOW" }));
    expect(pwInput.type).toBe("text");
    await userEvent.click(screen.getByRole("button", { name: "HIDE" }));
    expect(pwInput.type).toBe("password");
  });

  it("logout calls the auth API and navigates to /login", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("SESSION")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "SIGN OUT" }));
    await waitFor(() => {
      expect(vi.mocked(logout)).toHaveBeenCalled();
      expect(navigate).toHaveBeenCalledWith("/login", { replace: true });
    });
  });
});
