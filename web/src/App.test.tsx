import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { overviewFixture } from "@/test/fixtures";
import App from "./App";

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

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/auth/status")
        return new Response('{"registered":true,"authenticated":true}', { status: 200 });
      if (url === "/api/admin/overview")
        return new Response(JSON.stringify(overviewFixture), { status: 200 });
      return new Response("{}", { status: 200 });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("App", () => {
  it("boots an authenticated device straight into the room", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Listening, sir." })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Switch theme" })).toBeInTheDocument();
    expect(await screen.findByText(/cloud 1.42 \/ 5.00/)).toBeInTheDocument();
  });

  it("applies a theme to the document as it mounts", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "Listening, sir." });
    expect(["dark", "light"]).toContain(document.documentElement.dataset.theme);
  });

  it("renders the room on the action deep link too", async () => {
    window.history.pushState({}, "", "/actions/a91f");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Listening, sir." })).toBeInTheDocument();
    window.history.pushState({}, "", "/");
  });
});
