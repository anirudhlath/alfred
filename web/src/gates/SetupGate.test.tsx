import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserEvent } from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { defaultDeviceName, DEVICE_KEY } from "@/lib/auth";
import { hhmm } from "@/lib/format";
import { attentionFixture, integrationsFixture } from "@/test/fixtures";
import { SetupGate } from "./SetupGate";

const { registerPasskeyMock } = vi.hoisted(() => ({ registerPasskeyMock: vi.fn() }));
vi.mock("@/lib/webauthn", () => ({ registerPasskey: registerPasskeyMock }));

interface Call {
  url: string;
  method: string;
  body: unknown;
}

interface Route {
  status?: number;
  body?: unknown;
  /** Never answers — for what the gate does while a write is in flight. */
  pending?: boolean;
}

let calls: Call[] = [];

function stubApi(routes: Record<string, Route>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const route = routes[`${method} ${url}`] ?? routes[url];
      if (!route) return new Response(JSON.stringify({ detail: `unrouted ${method} ${url}` }), { status: 404 });
      if (route.pending) return new Promise<Response>(() => {});
      return new Response(JSON.stringify(route.body ?? {}), { status: route.status ?? 200 });
    }),
  );
}

const HAPPY: Record<string, Route> = {
  "/api/integrations": { body: integrationsFixture },
  "/api/admin/attention": { body: attentionFixture },
  "PUT /api/integrations/home-service/credentials": { body: { status: "ok", pushed: true } },
  "PUT /api/admin/attention/light": { body: { domain: "light", members: [], seen: [] } },
  "PUT /api/admin/attention/fan": { body: { domain: "fan", members: [], seen: [] } },
};

function renderSetup() {
  const onDone = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SetupGate onDone={onDone} />
    </QueryClientProvider>,
  );
  return { onDone };
}

/** Step 0 → step 1: register the passkey. */
async function register(user: UserEvent): Promise<void> {
  await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));
  await screen.findByRole("heading", { name: "Registered." });
}

beforeEach(() => {
  calls = [];
  registerPasskeyMock.mockReset().mockResolvedValue(undefined);
  vi.stubGlobal("location", { hostname: "alfred.example.com", pathname: "/" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("SetupGate — step 0, the passkey", () => {
  it("greets with the live hostname and the three-step rail", () => {
    stubApi(HAPPY);
    renderSetup();

    expect(screen.getByText("first run · alfred.example.com")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Good evening. I am Alfred." })).toBeInTheDocument();
    expect(
      screen.getByText(
        "This device will hold the only key to the house. There is no password anywhere; a passkey on this phone, unlocked by Face ID, is how you get in.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Connect Home Assistant")).toBeInTheDocument();
    expect(screen.getByText("Choose what the reflex may touch")).toBeInTheDocument();
    expect(
      screen.getByText("The passkey never leaves the phone. Nothing here phones home."),
    ).toBeInTheDocument();
  });

  it("registers with the device's own name, remembers it, and moves on", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();

    await register(user);

    expect(registerPasskeyMock).toHaveBeenCalledWith(defaultDeviceName());
    const remembered = JSON.parse(localStorage.getItem(DEVICE_KEY) ?? "null") as {
      name: string;
      registeredAt: string;
    };
    expect(remembered.name).toBe(defaultDeviceName());
    expect(Number.isNaN(Date.parse(remembered.registeredAt))).toBe(false);
  });

  it("leaves a 403 to the Denied gate: no error line, no advance", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    registerPasskeyMock.mockRejectedValue(new ApiError(403, "Request from untrusted network"));
    renderSetup();

    await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Create passkey with Face ID" })).toBeEnabled(),
    );
    expect(screen.getByRole("heading", { name: "Good evening. I am Alfred." })).toBeInTheDocument();
    expect(screen.queryByText("Request from untrusted network")).toBeNull();
  });

  it("reports a cancelled Face ID in its own words, not WebKit's", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    registerPasskeyMock.mockRejectedValue(
      new DOMException(
        "The operation either timed out or was not allowed. See: https://www.w3.org/TR/webauthn-2/#sctn-privacy-considerations-client.",
        "NotAllowedError",
      ),
    );
    renderSetup();

    await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Face ID was cancelled.");
    expect(screen.queryByText(/webauthn-2/)).toBeNull();
  });
});

describe("SetupGate — step 1, Home Assistant", () => {
  it("stamps the registration on the rail and moves the current ring on", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();
    await register(user);

    const { registeredAt } = JSON.parse(localStorage.getItem(DEVICE_KEY) ?? "null") as {
      registeredAt: string;
    };
    const registered = screen.getByText(`Register this ${defaultDeviceName()}`).closest("[data-step-state]");
    expect(registered).toHaveAttribute("data-step-state", "done");
    expect(registered).toHaveTextContent(hhmm(registeredAt));
    const connect = screen.getByText("Connect Home Assistant").closest("[data-step-state]");
    expect(connect).toHaveAttribute("data-step-state", "current");
    expect(connect).toHaveAttribute("aria-current", "step");
  });

  it("renders the home-service schema, not the weather adapter's", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();
    await register(user);

    expect(await screen.findByLabelText("Home Assistant URL")).toHaveValue("http://192.168.1.10:8123");
    expect(screen.getByLabelText("Access Token")).toHaveValue("");
    expect(screen.queryByLabelText("API key")).toBeNull();
    expect(
      screen.getByText("Long-lived access token from your HA profile page"),
    ).toBeInTheDocument();
  });

  it("PUTs the edited credentials and advances", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();
    await register(user);

    const token = await screen.findByLabelText("Access Token");
    await user.type(token, "llat-abc123");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByRole("heading", { name: "What may the reflex touch?" });
    const put = calls.find((call) => call.method === "PUT");
    expect(put?.url).toBe("/api/integrations/home-service/credentials");
    expect(put?.body).toEqual({ url: "http://192.168.1.10:8123", token: "llat-abc123" });
  });

  it("advances on a 502 and repeats what the server said", async () => {
    const user = userEvent.setup();
    stubApi({
      ...HAPPY,
      "PUT /api/integrations/home-service/credentials": {
        status: 502,
        body: {
          detail:
            "Credentials stored, but push to home-service failed: connection refused. They will be re-pushed when the service re-registers.",
        },
      },
    });
    renderSetup();
    await register(user);
    await screen.findByLabelText("Access Token");

    await user.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByRole("heading", { name: "What may the reflex touch?" });
    expect(
      screen.getByText(
        "Credentials stored, but push to home-service failed: connection refused. They will be re-pushed when the service re-registers.",
      ),
    ).toBeInTheDocument();
  });

  it("says why the form is empty when the integrations read fails", async () => {
    const user = userEvent.setup();
    stubApi({ ...HAPPY, "/api/integrations": { status: 503, body: { detail: "Keyring locked" } } });
    renderSetup();
    await register(user);

    expect(await screen.findByText("Keyring locked")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  });

  it("holds Do this later while the credentials are being written", async () => {
    const user = userEvent.setup();
    stubApi({ ...HAPPY, "PUT /api/integrations/home-service/credentials": { pending: true } });
    renderSetup();
    await register(user);
    await screen.findByLabelText("Access Token");

    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByRole("button", { name: "Do this later" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  });

  it("skips the write entirely on Do this later", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();
    await register(user);
    await screen.findByLabelText("Access Token");

    await user.click(screen.getByRole("button", { name: "Do this later" }));

    await screen.findByRole("heading", { name: "What may the reflex touch?" });
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });
});

describe("SetupGate — step 2, the attention set", () => {
  async function reachAttention(user: UserEvent): Promise<void> {
    await register(user);
    await screen.findByLabelText("Access Token");
    await user.click(screen.getByRole("button", { name: "Do this later" }));
    await screen.findByRole("heading", { name: "What may the reflex touch?" });
  }

  it("lists what the reflex may be trusted with, and never the locks", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();
    await reachAttention(user);

    expect(screen.getByRole("button", { name: /Light · 6 found/ })).toHaveTextContent("allowed");
    expect(screen.getByRole("button", { name: /Media player · 2 found/ })).toHaveTextContent(
      "allowed",
    );
    expect(screen.getByRole("button", { name: /Fan · 4 found/ })).toHaveTextContent("ask me");

    expect(screen.queryByText(/Lock ·/)).toBeNull();
    expect(screen.queryByText(/Alarm control panel ·/)).toBeNull();
    expect(screen.queryByText(/Cover ·/)).toBeNull();
  });

  it("writes only the rows the user touched, in the right direction", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    const { onDone } = renderSetup();
    await reachAttention(user);

    await user.click(screen.getByRole("button", { name: /Fan · 4 found/ }));
    await user.click(screen.getByRole("button", { name: /Light · 6 found/ }));
    await user.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    const puts = calls.filter((call) => call.url.startsWith("/api/admin/attention/"));
    expect(puts).toHaveLength(2);
    expect(puts.find((call) => call.url.endsWith("/fan"))?.body).toEqual({
      allow: ["fan.bathroom", "fan.study", "switch.desk", "switch.lamp"],
    });
    expect(puts.find((call) => call.url.endsWith("/light"))?.body).toEqual({
      ask: ["light.hall", "light.kitchen", "light.living_room"],
    });
  });

  it("writes nothing for a row tapped back to where it started", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    const { onDone } = renderSetup();
    await reachAttention(user);

    await user.click(screen.getByRole("button", { name: /Light · 6 found/ }));
    await user.click(screen.getByRole("button", { name: /Light · 6 found/ }));
    expect(screen.getByRole("button", { name: /Light · 6 found/ })).toHaveTextContent("allowed");
    await user.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(calls.filter((call) => call.url.startsWith("/api/admin/attention/"))).toHaveLength(0);
  });

  it("pages a long list through the endpoint's 200-entity cap", async () => {
    const user = userEvent.setup();
    const seen = Array.from({ length: 250 }, (_, index) => `sensor.s${index}`);
    stubApi({
      ...HAPPY,
      "/api/admin/attention": {
        body: { domains: [...attentionFixture.domains, { domain: "sensor", members: [], seen }] },
      },
      "PUT /api/admin/attention/sensor": { body: { domain: "sensor", members: [], seen: [] } },
    });
    const { onDone } = renderSetup();
    await reachAttention(user);

    await user.click(screen.getByRole("button", { name: /Sensor · 250 found/ }));
    await user.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    const puts = calls.filter((call) => call.url === "/api/admin/attention/sensor");
    expect(puts.map((call) => (call.body as { allow: string[] }).allow.length)).toEqual([200, 50]);
    expect(puts.flatMap((call) => (call.body as { allow: string[] }).allow)).toEqual(seen);
  });

  it("scrolls a long list under the pinned footer", async () => {
    const user = userEvent.setup();
    const domains = Array.from({ length: 30 }, (_, index) => ({
      domain: `domain_${index}`,
      members: [],
      seen: [`domain_${index}.one`],
    }));
    stubApi({ ...HAPPY, "/api/admin/attention": { body: { domains } } });
    renderSetup();
    await reachAttention(user);

    expect(screen.getAllByRole("button", { name: /· 1 found/ })).toHaveLength(30);
    expect(screen.getByRole("list").parentElement).toHaveClass("overflow-y-auto");
  });

  it("finishes with no writes when nothing was touched", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    const { onDone } = renderSetup();
    await reachAttention(user);

    await user.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(calls.filter((call) => call.url.startsWith("/api/admin/attention/"))).toHaveLength(0);
  });

  it("skips the step when no domain has been seen yet", async () => {
    const user = userEvent.setup();
    stubApi({ ...HAPPY, "/api/admin/attention": { body: { domains: [] } } });
    const { onDone } = renderSetup();
    await register(user);
    await screen.findByLabelText("Access Token");
    await user.click(screen.getByRole("button", { name: "Do this later" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "Finish" })).toBeNull();
  });

  it("skips once, even when the parent hands it a new onDone every render", async () => {
    const user = userEvent.setup();
    stubApi({ ...HAPPY, "/api/admin/attention": { body: { domains: [] } } });
    const spy = vi.fn();
    // What AuthGate does: an inline closure, so `onDone` is new on every render.
    function Parent() {
      const [renders, setRenders] = useState(0);
      return (
        <SetupGate
          onDone={() => {
            spy();
            if (renders < 3) setRenders(renders + 1);
          }}
        />
      );
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <Parent />
      </QueryClientProvider>,
    );
    await register(user);
    await screen.findByLabelText("Access Token");
    await user.click(screen.getByRole("button", { name: "Do this later" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("skips the step when the attention store is down", async () => {
    const user = userEvent.setup();
    stubApi({
      ...HAPPY,
      "/api/admin/attention": { status: 503, body: { detail: "Attention store unavailable" } },
    });
    const { onDone } = renderSetup();
    await register(user);
    await screen.findByLabelText("Access Token");
    await user.click(screen.getByRole("button", { name: "Do this later" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  });

  it("reports a failed write and stays put", async () => {
    const user = userEvent.setup();
    stubApi({
      ...HAPPY,
      "PUT /api/admin/attention/fan": { status: 503, body: { detail: "Attention store unavailable" } },
    });
    const { onDone } = renderSetup();
    await reachAttention(user);

    await user.click(screen.getByRole("button", { name: /Fan · 4 found/ }));
    await user.click(screen.getByRole("button", { name: "Finish" }));

    expect(await screen.findByText("Attention store unavailable")).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });
});
