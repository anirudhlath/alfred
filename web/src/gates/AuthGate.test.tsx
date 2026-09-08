import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { authEvents } from "@/lib/auth-events";
import { DEVICE_KEY } from "@/lib/auth";
import type { AuthStatus } from "@/lib/types";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { AuthGate } from "./AuthGate";

const { loginPasskeyMock, registerPasskeyMock } = vi.hoisted(() => ({
  loginPasskeyMock: vi.fn(),
  registerPasskeyMock: vi.fn(),
}));
vi.mock("@/lib/webauthn", () => ({
  loginPasskey: loginPasskeyMock,
  registerPasskey: registerPasskeyMock,
}));

// The provider constructs both sockets at module load; jsdom has no WebSocket
// server behind them, and this file is about the gates, not the wire.
vi.mock("@/lib/chat-socket", () => ({
  ChatSocket: class {
    onstatus = () => {};
    connect() {}
    close() {}
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

let status: AuthStatus = { registered: true, authenticated: true };
/** When set, the status read 500s — the store behind it is down. */
let statusDown = false;

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/auth/status") {
        if (statusDown) return new Response('{"detail":"redis is down"}', { status: 500 });
        return new Response(JSON.stringify(status), { status: 200 });
      }
      if (url === "/api/integrations") return new Response("[]", { status: 200 });
      if (url === "/api/admin/attention") return new Response('{"domains":[]}', { status: 200 });
      return new Response("{}", { status: 200 });
    }),
  );
}

function renderGate() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <AuthGate>
          <main>the room</main>
        </AuthGate>
      </ConnectionProvider>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

beforeEach(() => {
  status = { registered: true, authenticated: true };
  statusDown = false;
  loginPasskeyMock.mockReset().mockResolvedValue(undefined);
  registerPasskeyMock.mockReset().mockResolvedValue(undefined);
  vi.stubGlobal("location", { hostname: "alfred.example.com", pathname: "/" });
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("AuthGate routing", () => {
  it("shows a bare field, and no copy, while the status is unknown", () => {
    const { container } = renderGate();
    expect(container.querySelector(".gate-field")).not.toBeNull();
    expect(screen.queryByText("the room")).toBeNull();
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("sends an unregistered house to setup", async () => {
    status = { registered: false, authenticated: false };
    renderGate();
    expect(
      await screen.findByRole("heading", { name: "Good evening. I am Alfred." }),
    ).toBeInTheDocument();
  });

  it("lets a finished setup through to the room", async () => {
    const user = userEvent.setup();
    status = { registered: false, authenticated: false };
    renderGate();
    await screen.findByRole("heading", { name: "Good evening. I am Alfred." });

    // Registering is what makes the server say so; the cached status still says
    // unregistered until the gate publishes the new truth.
    status = { registered: true, authenticated: true };
    await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));
    await screen.findByRole("heading", { name: "Registered." });
    // No integrations to fill in and no attention rows: setup skips straight out.
    await user.click(screen.getByRole("button", { name: "Do this later" }));

    expect(await screen.findByText("the room")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Good evening. I am Alfred." })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Welcome back, sir." })).toBeNull();
  });

  it("raises the denied gate over setup when registration is off-network", async () => {
    const user = userEvent.setup();
    status = { registered: false, authenticated: false };
    // What `api()` does with a 403: says so on the bus, then throws.
    registerPasskeyMock.mockImplementation(async () => {
      authEvents.emit("denied");
      throw new ApiError(403, "Not from here");
    });
    renderGate();
    await screen.findByRole("heading", { name: "Good evening. I am Alfred." });

    await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));

    expect(await screen.findByRole("heading", { name: "Not from here." })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Back to the room" }));
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Not from here." })).toBeNull(),
    );
    // Setup is still where it was, and did not repeat the news in its foot line.
    expect(screen.getByRole("heading", { name: "Good evening. I am Alfred." })).toBeInTheDocument();
    expect(screen.queryByText("Not from here")).toBeNull();
  });

  it("sends a registered but signed-out device to sign-in", async () => {
    status = { registered: true, authenticated: false };
    localStorage.setItem(
      DEVICE_KEY,
      JSON.stringify({ name: "iPhone", registeredAt: new Date(2026, 7, 12).toISOString() }),
    );
    renderGate();

    expect(await screen.findByRole("heading", { name: "Welcome back, sir." })).toBeInTheDocument();
    expect(screen.getByText("alfred.example.com · signed out")).toBeInTheDocument();
    expect(screen.getByText("Passkey · iPhone · registered 12 Aug")).toBeInTheDocument();
    expect(screen.queryByText("the room")).toBeNull();
  });

  it("falls back to a vague foot line with no remembered device", async () => {
    status = { registered: true, authenticated: false };
    renderGate();
    expect(await screen.findByText("Passkey · this phone")).toBeInTheDocument();
  });

  it("lets an authenticated device through to the room", async () => {
    renderGate();
    expect(await screen.findByText("the room")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Welcome back, sir." })).toBeNull();
  });

  it("fails closed to sign-in when the status read errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"detail":"redis is down"}', { status: 500 })),
    );
    renderGate();
    expect(await screen.findByRole("heading", { name: "Welcome back, sir." })).toBeInTheDocument();
  });

  it("keeps the room when a background read of the status fails", async () => {
    const { client } = renderGate();
    await screen.findByText("the room");

    statusDown = true;
    await act(() => client.invalidateQueries({ queryKey: ["auth-status"] }));
    await waitFor(() => expect(client.getQueryState(["auth-status"])?.status).toBe("error"));

    // Nothing should change on screen, so give the observer its tick and look.
    await act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));
    expect(screen.getByText("the room")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Welcome back, sir." })).toBeNull();
  });

  it("signs in with the passkey and reveals the room", async () => {
    const user = userEvent.setup();
    status = { registered: true, authenticated: false };
    renderGate();
    await screen.findByRole("heading", { name: "Welcome back, sir." });

    status = { registered: true, authenticated: true };
    await user.click(screen.getByRole("button", { name: "Sign in with Face ID" }));

    expect(loginPasskeyMock).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("the room")).toBeInTheDocument();
  });

  it("reports a failed sign-in in the foot line, in its own words", async () => {
    const user = userEvent.setup();
    status = { registered: true, authenticated: false };
    loginPasskeyMock.mockRejectedValue(
      new DOMException("The operation either timed out or was not allowed.", "NotAllowedError"),
    );
    renderGate();
    await screen.findByRole("heading", { name: "Welcome back, sir." });

    await user.click(screen.getByRole("button", { name: "Sign in with Face ID" }));

    expect(await screen.findByText("Face ID was cancelled.")).toBeInTheDocument();
  });
});

describe("AuthGate events", () => {
  it("raises the expired gate over the room, with the real eight-hour TTL", async () => {
    renderGate();
    await screen.findByText("the room");

    act(() => authEvents.emit("expired"));

    expect(screen.getByRole("heading", { name: "Your session lapsed." })).toBeInTheDocument();
    expect(
      screen.getByText(
        "The passkey session on this phone ran out after eight hours. Anything below is last-known until you sign in again.",
      ),
    ).toBeInTheDocument();
    // The room is still mounted underneath — that is the whole point.
    expect(screen.getByText("the room")).toBeInTheDocument();
  });

  it("dismisses the expired gate once the passkey signs in again", async () => {
    const user = userEvent.setup();
    renderGate();
    await screen.findByText("the room");
    act(() => authEvents.emit("expired"));

    await user.click(screen.getByRole("button", { name: "Sign in with Face ID" }));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Your session lapsed." })).toBeNull(),
    );
  });

  it("drops the expired gate when the status says signed out, for good", async () => {
    const user = userEvent.setup();
    const { client } = renderGate();
    await screen.findByText("the room");
    act(() => authEvents.emit("expired"));
    expect(screen.getByRole("heading", { name: "Your session lapsed." })).toBeInTheDocument();

    // A refetch (the app refocused) confirms it: the sign-in gate takes over.
    status = { registered: true, authenticated: false };
    await act(() => client.invalidateQueries({ queryKey: ["auth-status"] }));
    expect(await screen.findByRole("heading", { name: "Welcome back, sir." })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Your session lapsed." })).toBeNull(),
    );

    status = { registered: true, authenticated: true };
    await user.click(screen.getByRole("button", { name: "Sign in with Face ID" }));

    expect(await screen.findByText("the room")).toBeInTheDocument();
    // The gate stays down: the latch went with the room it was raised over.
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Your session lapsed." })).toBeNull(),
    );
  });

  it("raises the denied gate, which only offers a way back", async () => {
    const user = userEvent.setup();
    renderGate();
    await screen.findByText("the room");

    act(() => authEvents.emit("denied"));

    expect(screen.getByRole("heading", { name: "Not from here." })).toBeInTheDocument();
    expect(screen.getByText("403 · off-network")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign in with Face ID" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Back to the room" }));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Not from here." })).toBeNull(),
    );
    expect(screen.getByText("the room")).toBeInTheDocument();
  });
});
