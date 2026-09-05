import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { OnboardingPage } from "./OnboardingPage";
import type { IntegrationInfo } from "@/lib/types";

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

const SPOTIFY: IntegrationInfo = {
  name: "spotify",
  category: "media",
  schema: {
    fields: {
      client_id: {
        label: "Client ID",
        field_type: "text",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
    },
  },
  configured: { client_id: false },
};

function setupMocks(
  authStatus: unknown = { registered: false, authenticated: false },
  integrations: unknown = [],
) {
  let status = authStatus;
  vi.mocked(api).mockImplementation((url: string) => {
    if (url === "/api/auth/status") return Promise.resolve(status);
    if (url === "/api/integrations") return Promise.resolve(integrations);
    return Promise.reject(new Error(`Unexpected URL: ${url}`));
  });
  vi.mocked(post).mockResolvedValue({});
  // A successful ceremony sets the auth cookie server-side, so /api/auth/status
  // changes with it. The wizard re-reads that status before advancing, so a mock
  // that resolves without granting a session models a *broken* server, not a
  // working one — every test that walks past step 0 needs this to be honest.
  vi.mocked(registerPasskey).mockImplementation(() => {
    status = { registered: true, authenticated: true };
    return Promise.resolve();
  });
}

/** Every path api() was called with. Asserting on this rather than
 * `toHaveBeenCalledWith(path)` keeps the check honest when a caller adds a second
 * argument (api(path, init)) — a whole-arguments matcher silently stops matching. */
const requestedPaths = () => vi.mocked(api).mock.calls.map((c) => c[0]);

/** Register a passkey to advance from step 0 → step 1. */
async function advancePastPasskey() {
  await userEvent.type(screen.getByPlaceholderText("e.g. MacBook Pro"), "My Mac");
  await userEvent.click(screen.getByRole("button", { name: "Register passkey" }));
  await waitFor(() => expect(screen.getByText("A few particulars")).toBeInTheDocument());
}

/** From the personal step (1) to the Connections step (4). */
async function advanceToConnections() {
  await userEvent.click(screen.getByRole("button", { name: "Continue" })); // proactivity
  await userEvent.click(screen.getByRole("button", { name: "Continue" })); // guest
  await userEvent.click(screen.getByRole("button", { name: "Continue" })); // connections
  await screen.findByText("Connections");
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

  it("does not fetch the session-gated integrations list while signed out", async () => {
    setupMocks({ registered: true, authenticated: false });
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Skip — already registered" })).toBeInTheDocument(),
    );
    // GET /api/integrations requires a session; firing it here would 401 and api()
    // would bounce the user to /login mid-wizard.
    expect(requestedPaths()).not.toContain("/api/integrations");
  });

  it("sends a signed-out skipper to /login instead of into the wizard", async () => {
    setupMocks({ registered: true, authenticated: false });
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Skip — already registered" })).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole("button", { name: "Skip — already registered" }));

    // Without a session the wizard's own submit (POST /api/onboarding) 401s, so
    // advancing would waste five steps of input.
    expect(navigate).toHaveBeenCalledWith("/login", { replace: true });
    expect(screen.queryByText("A few particulars")).toBeNull();
    // The 401 this redirect exists to avoid: nothing may reach the submit endpoint.
    expect(vi.mocked(post).mock.calls.map((c) => c[0])).not.toContain("/api/onboarding");
  });

  it("fetches the integrations list once the passkey grants a session", async () => {
    setupMocks(undefined, [SPOTIFY]);

    renderPage();
    await waitFor(() => expect(screen.getByText("Register your device")).toBeInTheDocument());
    expect(requestedPaths()).not.toContain("/api/integrations");

    await advancePastPasskey();
    await waitFor(() => expect(requestedPaths()).toContain("/api/integrations"));

    // ...and it landed before the Connections step needs it. Asserting on the
    // rendered IntegrationCard, not the static "Connections" heading, is what makes
    // this a test of the fetched data — the heading renders either way.
    await advanceToConnections();
    expect(screen.getByText("SPOTIFY")).toBeInTheDocument();
  });

  it("fetches integrations right away on a return visit that is already signed in", async () => {
    // The auto-skip path never runs register.onSuccess, so the query has to be
    // enabled by the initial status alone.
    setupMocks({ registered: true, authenticated: true }, [SPOTIFY]);
    renderPage();
    await waitFor(() => expect(screen.getByText("A few particulars")).toBeInTheDocument());

    await waitFor(() => expect(requestedPaths()).toContain("/api/integrations"));
    await advanceToConnections();
    expect(screen.getByText("SPOTIFY")).toBeInTheDocument();
  });

  it("sends the user to /login when registration leaves the session unauthenticated", async () => {
    // registerPasskey resolving is not proof of a session: a Set-Cookie the browser
    // rejects (Secure over plain http, SameSite, a proxy stripping it) leaves the
    // caller anonymous. Advancing anyway costs five steps of input and a 401 on
    // POST /api/onboarding that discards all of it.
    let authStatus: unknown = { registered: false, authenticated: false };
    vi.mocked(api).mockImplementation((url: string) => {
      if (url === "/api/auth/status") return Promise.resolve(authStatus);
      if (url === "/api/integrations") return Promise.resolve([]);
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.mocked(post).mockResolvedValue({});
    vi.mocked(registerPasskey).mockImplementation(() => {
      authStatus = { registered: true, authenticated: false };
      return Promise.resolve();
    });

    renderPage();
    await waitFor(() => expect(screen.getByText("Register your device")).toBeInTheDocument());
    await userEvent.type(screen.getByPlaceholderText("e.g. MacBook Pro"), "My Mac");
    await userEvent.click(screen.getByRole("button", { name: "Register passkey" }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login", { replace: true }));
    expect(screen.queryByText("A few particulars")).toBeNull();
  });

  it("does not offer a retry when the ceremony succeeded but the session read failed", async () => {
    // The credential already exists server-side, so "Passkey registration failed" is
    // the one thing this must not say: pressing Register again either throws
    // InvalidStateError or mints a duplicate credential.
    let statusReads = 0;
    vi.mocked(api).mockImplementation((url: string) => {
      if (url === "/api/auth/status") {
        statusReads += 1;
        // The initial render's read lands; the post-ceremony confirmation does not.
        return statusReads === 1
          ? Promise.resolve({ registered: false, authenticated: false })
          : Promise.reject(new TypeError("Failed to fetch"));
      }
      if (url === "/api/integrations") return Promise.resolve([]);
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.mocked(post).mockResolvedValue({});
    vi.mocked(registerPasskey).mockResolvedValue(undefined);

    renderPage();
    await waitFor(() => expect(screen.getByText("Register your device")).toBeInTheDocument());
    await userEvent.type(screen.getByPlaceholderText("e.g. MacBook Pro"), "My Mac");
    await userEvent.click(screen.getByRole("button", { name: "Register passkey" }));

    expect(
      await screen.findByText(
        "Passkey registered, but the session couldn't be confirmed — reload the page.",
      ),
    ).toBeInTheDocument();
    // Distinct from a failed ceremony, and the retry-as-new-registration path is gone.
    expect(screen.queryByText("Passkey registration failed")).toBeNull();
    expect(screen.queryByRole("button", { name: "Register passkey" })).toBeNull();
    // Nothing advanced, nothing navigated, nothing submitted.
    expect(screen.getByText("STEP 1/6")).toBeInTheDocument();
    expect(screen.queryByText("A few particulars")).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
    expect(vi.mocked(post).mock.calls.map((c) => c[0])).not.toContain("/api/onboarding");
  });

  it("shows a loading state while the integrations query is in flight", async () => {
    vi.mocked(api).mockImplementation((url: string) => {
      if (url === "/api/auth/status")
        return Promise.resolve({ registered: true, authenticated: true });
      if (url === "/api/integrations") return new Promise(() => {}); // never settles
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.mocked(post).mockResolvedValue({});

    renderPage();
    await waitFor(() => expect(screen.getByText("A few particulars")).toBeInTheDocument());
    await advanceToConnections();

    // Otherwise a slow (or hung) request renders as a wizard step with nothing on it.
    expect(screen.getByText("Loading integrations…")).toBeInTheDocument();
  });

  it("says so when there are no integrations to connect", async () => {
    setupMocks({ registered: true, authenticated: true }, []);
    renderPage();
    await waitFor(() => expect(screen.getByText("A few particulars")).toBeInTheDocument());
    await advanceToConnections();

    // An empty list and a failed fetch look identical without this.
    expect(await screen.findByText("No integrations available")).toBeInTheDocument();
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
