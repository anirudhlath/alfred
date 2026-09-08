import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authEvents } from "@/lib/auth-events";
import { DEVICE_KEY } from "@/lib/auth";
import type { AuthStatus } from "@/lib/types";
import { AuthGate } from "./AuthGate";

const { loginPasskeyMock, registerPasskeyMock } = vi.hoisted(() => ({
  loginPasskeyMock: vi.fn(),
  registerPasskeyMock: vi.fn(),
}));
vi.mock("@/lib/webauthn", () => ({
  loginPasskey: loginPasskeyMock,
  registerPasskey: registerPasskeyMock,
}));

let status: AuthStatus = { registered: true, authenticated: true };

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/auth/status") return new Response(JSON.stringify(status), { status: 200 });
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
      <AuthGate>
        <main>the room</main>
      </AuthGate>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

beforeEach(() => {
  status = { registered: true, authenticated: true };
  loginPasskeyMock.mockReset().mockResolvedValue(undefined);
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

    expect(await screen.findByRole("status")).toHaveTextContent("Face ID was cancelled.");
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
