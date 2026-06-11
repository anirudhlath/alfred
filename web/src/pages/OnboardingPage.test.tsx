import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { OnboardingPage } from "./OnboardingPage";

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
vi.mock("@/lib/webauthn", () => ({ registerPasskey: vi.fn() }));
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

import { api, post } from "@/lib/api";
import { registerPasskey } from "@/lib/webauthn";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

function renderPage() {
  return render(
    <QueryClientProvider client={makeQC()}>
      <MemoryRouter>
        <OnboardingPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function setupMocks(authStatus: unknown = { registered: false, authenticated: false }) {
  vi.mocked(api).mockImplementation((url: string) => {
    if (url === "/api/auth/status") return Promise.resolve(authStatus);
    if (url === "/api/integrations") return Promise.resolve([]);
    return Promise.reject(new Error(`Unexpected URL: ${url}`));
  });
  vi.mocked(post).mockResolvedValue({});
  vi.mocked(registerPasskey).mockResolvedValue(undefined);
}

/** Register a passkey to advance from step 0 → step 1. */
async function advancePastPasskey() {
  await userEvent.type(screen.getByPlaceholderText("e.g. MacBook Pro"), "My Mac");
  await userEvent.click(screen.getByRole("button", { name: "Register passkey" }));
  await waitFor(() => expect(screen.getByText("A few particulars")).toBeInTheDocument());
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("OnboardingPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  it("starts on the passkey step", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("STEP 1/6")).toBeInTheDocument());
    expect(screen.getByText("Register your device")).toBeInTheDocument();
  });

  it("shows a skip button only when already registered (not authenticated)", async () => {
    setupMocks({ registered: true, authenticated: false });
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Skip — already registered" })).toBeInTheDocument(),
    );
  });

  it("auto-skips the passkey step when already registered and authenticated", async () => {
    setupMocks({ registered: true, authenticated: true });
    renderPage();
    // Should jump directly to step 2 (personal) without any user interaction.
    await waitFor(() => expect(screen.getByText("A few particulars")).toBeInTheDocument());
    expect(screen.getByText("STEP 2/6")).toBeInTheDocument();
    expect(screen.queryByText("Register your device")).toBeNull();
  });

  it("advances to the proactivity step with ONE Continue click after auto-skip", async () => {
    setupMocks({ registered: true, authenticated: true });
    renderPage();
    // Auto-skip lands on step 2/6 (activeStep=1, Personal).
    await waitFor(() => expect(screen.getByText("A few particulars")).toBeInTheDocument());
    expect(screen.getByText("STEP 2/6")).toBeInTheDocument();

    // A single Continue click must reach step 3/6 (Proactivity).
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() =>
      expect(screen.getByText("How proactive shall I be?")).toBeInTheDocument(),
    );
    expect(screen.getByText("STEP 3/6")).toBeInTheDocument();
  });

  it("does not show Back on the auto-skipped personal step", async () => {
    setupMocks({ registered: true, authenticated: true });
    renderPage();
    await waitFor(() => expect(screen.getByText("A few particulars")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
  });

  it("registers a passkey and advances to the personal step", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Register your device")).toBeInTheDocument());
    await advancePastPasskey();
    expect(vi.mocked(registerPasskey)).toHaveBeenCalledWith("My Mac");
    expect(screen.getByText("STEP 2/6")).toBeInTheDocument();
  });

  it("navigates forward and backward through steps", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Register your device")).toBeInTheDocument());
    await advancePastPasskey();

    await userEvent.click(screen.getByRole("button", { name: "Continue" })); // → proactivity
    expect(screen.getByText("How proactive shall I be?")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Back" })); // → personal
    expect(screen.getByText("A few particulars")).toBeInTheDocument();
  });

  it("accumulates guest-control checkbox selections and submits them", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Register your device")).toBeInTheDocument());
    await advancePastPasskey();

    await userEvent.click(screen.getByRole("button", { name: "Continue" })); // proactivity
    await userEvent.click(screen.getByRole("button", { name: "Continue" })); // guest mode
    await waitFor(() => expect(screen.getByText("Guest access")).toBeInTheDocument());

    // Lighting + Media are checked by default; toggle Climate on, Lighting off.
    const checks = screen.getAllByRole("checkbox") as HTMLInputElement[];
    // GUEST_CONTROLS order: Lighting, Media, Climate, Door locks
    await userEvent.click(checks[0]); // Lighting off
    await userEvent.click(checks[2]); // Climate on

    // Advance through integrations → done, then submit.
    await userEvent.click(screen.getByRole("button", { name: "Continue" })); // integrations
    await userEvent.click(screen.getByRole("button", { name: "Continue" })); // done
    await waitFor(() => expect(screen.getByText("Very good, sir.")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Begin" }));

    await waitFor(() => {
      expect(vi.mocked(post)).toHaveBeenCalledWith(
        "/api/onboarding",
        expect.objectContaining({
          guest_controls: ["Media playback", "Climate control"],
        }),
      );
    });
  });

  it("submits the collected payload and navigates home", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Register your device")).toBeInTheDocument());
    await advancePastPasskey();

    // Personal step: change wake time + fill address.
    const wake = screen.getByDisplayValue("07:00");
    await userEvent.clear(wake);
    await userEvent.type(wake, "08:30");
    await userEvent.type(screen.getByPlaceholderText("123 Main St, City"), "1 Infinite Loop");

    await userEvent.click(screen.getByRole("button", { name: "Continue" })); // proactivity
    // Pick "Opinionated"
    await userEvent.click(screen.getByText("Opinionated"));

    await userEvent.click(screen.getByRole("button", { name: "Continue" })); // guest
    await userEvent.click(screen.getByRole("button", { name: "Continue" })); // integrations
    await userEvent.click(screen.getByRole("button", { name: "Continue" })); // done
    await userEvent.click(screen.getByRole("button", { name: "Begin" }));

    await waitFor(() => {
      expect(vi.mocked(post)).toHaveBeenCalledWith(
        "/api/onboarding",
        expect.objectContaining({
          wake_time: "08:30",
          work_address: "1 Infinite Loop",
          proactivity_level: "opinionated",
        }),
      );
      expect(navigate).toHaveBeenCalledWith("/", { replace: true });
    });
  });
});
