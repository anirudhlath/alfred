import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authEvents } from "@/lib/auth-events";
import type { AttentionDomain } from "@/lib/system";
import {
  attentionFixture,
  authSession,
  credential,
  integration,
  overviewFixture,
} from "@/test/fixtures";
import { useSystem } from "./useSystem";

const OVERVIEW = "/api/admin/overview";
const SESSIONS = "/api/auth/sessions";
const CREDENTIALS = "/api/auth/credentials";
const PAIRING = "/api/auth/pairing";
const INTEGRATIONS = "/api/integrations";
const ATTENTION = "/api/admin/attention";
const DND = "/api/admin/dnd";
const DRAIN = "/api/admin/notifications/drain";
const LIBRARIAN = "/api/admin/librarian/run";

const statusPath = (name: string) => `/api/integrations/${name}/status`;
const credentialsPath = (name: string) => `/api/integrations/${name}/credentials`;
const attentionPath = (domain: string) => `${ATTENTION}/${domain}`;
const sessionPath = (id: string) => `${SESSIONS}/${id}`;

/** The caller's own session, and a second one it may end. */
const PHONE = authSession({ session_id: "sess-phone", device_name: "Phone", current: true });
const LAPTOP = authSession({
  session_id: "sess-laptop",
  credential_id: "cred-laptop",
  device_name: "Laptop",
  ip: "192.168.1.31",
});

const PASSKEY = credential({ current: true });

/** An adapter and a service: `home-service` is the one the health grid reads. */
const WEATHER = integration({
  name: "weather",
  category: "weather",
  kind: "adapter",
  schema: {
    fields: {
      api_key: {
        label: "API key",
        field_type: "password",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
    },
  },
  configured: { api_key: true },
});
const HOME = integration();

/** One canned reply: the status the server answers with, and the body it sends. */
interface Answer {
  status: number;
  body: unknown;
}

/** One request the hook made. */
interface Call {
  url: string;
  method: string;
  body: unknown;
}

let calls: Call[];
/** What each `METHOD url` answers next; tests move entries to prove a re-read. */
let routes: Map<string, Answer>;

const route = (method: string, url: string): string => `${method} ${url}`;
const answer = (method: string, url: string, reply: Answer): void =>
  void routes.set(route(method, url), reply);

const reads = (): string[] => calls.filter((call) => call.method === "GET").map((c) => c.url);
const countOf = (method: string, url: string): number =>
  calls.filter((call) => call.method === method && call.url === url).length;

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const raw = init?.body;
      calls.push({
        url,
        method,
        body: typeof raw === "string" ? (JSON.parse(raw) as unknown) : null,
      });
      // Anything unstaged is a write the route answers `{"status":"ok"}` to —
      // every write on this bench does, bar the two that answer with a record.
      const reply = routes.get(route(method, url)) ?? { status: 200, body: { status: "ok" } };
      return new Response(JSON.stringify(reply.body), { status: reply.status });
    }),
  );
}

const makeClient = (): QueryClient =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });

function renderSystem(enabled = true, client = makeClient()) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(({ on }: { on: boolean }) => useSystem(on), {
    wrapper,
    initialProps: { on: enabled },
  });
}

/** Let every queued microtask run without asserting anything happened. */
const settle = async (): Promise<void> => {
  await act(async () => {
    await Promise.resolve();
  });
};

beforeEach(() => {
  calls = [];
  routes = new Map<string, Answer>([
    [route("GET", OVERVIEW), { status: 200, body: overviewFixture }],
    [route("GET", SESSIONS), { status: 200, body: { sessions: [PHONE, LAPTOP] } }],
    [route("GET", CREDENTIALS), { status: 200, body: { credentials: [PASSKEY] } }],
    [route("GET", INTEGRATIONS), { status: 200, body: [WEATHER, HOME] }],
    [
      route("GET", statusPath("weather")),
      { status: 200, body: { name: "weather", healthy: true, latency_ms: 42 } },
    ],
    [
      route("GET", statusPath("home-service")),
      { status: 200, body: { name: "home-service", healthy: true, latency_ms: 210 } },
    ],
    [route("GET", ATTENTION), { status: 200, body: attentionFixture }],
  ]);
  stubFetch();
});

// `fetch` is un-stubbed by `unstubGlobals` in vite.config.ts; the clock is this
// file's own business.
afterEach(() => {
  vi.useRealTimers();
});

describe("useSystem", () => {
  it("reads nothing at all while the bench is not showing", async () => {
    const { result } = renderSystem(false);
    await settle();

    // The overview included: `useOverview` takes the same gate, so a closed
    // bench does not start a second poller for a screen nobody is looking at.
    expect(calls).toEqual([]);
    expect(result.current.sessions.list).toEqual([]);
    expect(result.current.credentials.list).toEqual([]);
    expect(result.current.integrations.list).toEqual([]);
    expect(result.current.attention.domains).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it("reads sessions, credentials, integrations and attention when it opens", async () => {
    const { result } = renderSystem();

    await waitFor(() => expect(result.current.sessions.list).toHaveLength(2));
    await waitFor(() => expect(result.current.attention.domains.length).toBeGreaterThan(0));

    expect(reads()).toEqual(
      expect.arrayContaining([SESSIONS, CREDENTIALS, INTEGRATIONS, ATTENTION]),
    );
    expect(result.current.credentials.list).toHaveLength(1);
    expect(result.current.integrations.list.map((row) => row.name)).toEqual([
      "weather",
      "home-service",
    ]);
    expect(result.current.error).toBeNull();
  });

  it("probes each integration's status once", async () => {
    const { result } = renderSystem();

    await waitFor(() => expect(result.current.integrations.state["weather"]).toBe("ok"));
    await waitFor(() => expect(result.current.integrations.state["home-service"]).toBe("ok"));
    await settle();

    expect(countOf("GET", statusPath("weather"))).toBe(1);
    expect(countOf("GET", statusPath("home-service"))).toBe(1);
    expect(result.current.integrations.latency["home-service"]).toBe(210);
  });

  it("keeps an integration whose status refuses to answer, as failed", async () => {
    answer("GET", statusPath("weather"), { status: 503, body: { detail: "probe unavailable" } });
    const { result } = renderSystem();

    await waitFor(() => expect(result.current.integrations.state["weather"]).toBe("failed"));

    // Still in the list: a row that cannot be probed is a row that says so, not
    // a service that has vanished from the house.
    expect(result.current.integrations.list.map((row) => row.name)).toContain("weather");
    expect(result.current.integrations.latency["weather"]).toBeNull();
    expect(countOf("GET", statusPath("weather"))).toBe(1);
  });

  it("derives the health grid from the overview and the home service's probe", async () => {
    const { result } = renderSystem();

    await waitFor(() => expect(result.current.health.home.value).toBe("ok"));

    expect(result.current.health.bus).toEqual({
      value: "alive",
      note: "bus · redis · 3 streams",
      alive: true,
    });
    expect(result.current.health.reflex).toEqual({
      value: "380 ms",
      note: "reflex · reflex-3b",
      alive: true,
    });
    expect(result.current.health.rate).toEqual({
      value: "2.1 ev/s",
      note: "event rate · 5-minute mean",
      alive: true,
    });
    expect(result.current.health.home.note).toBe("home assistant · 210 ms");
  });

  it("says unknown rather than guessing while the overview has not answered", () => {
    const { result } = renderSystem();

    // Synchronous first render: nothing has come back yet.
    expect(result.current.health.bus.value).toBe("unknown");
    expect(result.current.health.bus.alive).toBe(false);
    expect(result.current.health.reflex.value).toBe("—");
    expect(result.current.health.rate.value).toBe("— ev/s");
    expect(result.current.health.home.value).toBe("—");
    expect(result.current.health.home.note).toBe("home assistant · not registered");
  });

  it("moves the do-not-disturb switch once the server's own read confirms it", async () => {
    const { result } = renderSystem();
    await waitFor(() => expect(result.current.quiet.active).toBe(false));
    const before = countOf("GET", OVERVIEW);
    // The route writes Redis before it answers, so the read behind the write
    // reports the new position. Staged first, because the write is what sends
    // this client back for it.
    answer("GET", OVERVIEW, {
      status: 200,
      body: { ...overviewFixture, dnd: { active: true, until: "2026-09-16T22:00:00Z" } },
    });

    act(() => result.current.quiet.set(true, "2026-09-16T22:00:00Z"));

    await waitFor(() => expect(result.current.quiet.active).toBe(true));
    expect(
      calls.find((call) => call.method === "POST" && call.url === DND)?.body,
    ).toEqual({ active: true, until: "2026-09-16T22:00:00Z" });
    expect(result.current.quiet.until).toBe("2026-09-16T22:00:00Z");
    // The overview owns the switch, so it is re-read rather than echoed; the
    // control is busy for exactly as long as that read takes.
    expect(countOf("GET", OVERVIEW)).toBeGreaterThan(before);
    expect(result.current.quiet.setting).toBe(false);
    expect(result.current.quiet.error).toBeNull();
  });

  it("clears do-not-disturb with an active false and no until", async () => {
    answer("GET", OVERVIEW, {
      status: 200,
      body: { ...overviewFixture, dnd: { active: true, until: null } },
    });
    const { result } = renderSystem();
    await waitFor(() => expect(result.current.quiet.active).toBe(true));
    answer("GET", OVERVIEW, { status: 200, body: { ...overviewFixture, dnd: { active: false } } });

    act(() => result.current.quiet.set(false, null));

    await waitFor(() => expect(result.current.quiet.active).toBe(false));
    const sent = calls.find((call) => call.method === "POST" && call.url === DND);
    expect(sent?.body).toEqual({ active: false });
    // `until: null` and no `until` at all are one rule: the key is only ever
    // there when there is an instant to put in it.
    expect(Object.keys(sent?.body as object)).not.toContain("until");
  });

  it("leaves the switch where it was when the write is refused", async () => {
    answer("POST", DND, { status: 503, body: { detail: "Redis unavailable" } });
    const { result } = renderSystem();
    await waitFor(() => expect(result.current.quiet.active).toBe(false));
    const before = countOf("GET", OVERVIEW);

    act(() => result.current.quiet.set(true, null));

    await waitFor(() => expect(result.current.quiet.error).toBe("Redis unavailable"));
    expect(result.current.quiet.active).toBe(false);
    expect(result.current.quiet.setting).toBe(false);
    // Nothing was written, so there is nothing to go back and read.
    expect(countOf("GET", OVERVIEW)).toBe(before);
  });

  it("drops an ended session from the list after the re-read", async () => {
    const { result } = renderSystem();
    await waitFor(() => expect(result.current.sessions.list).toHaveLength(2));

    answer("GET", SESSIONS, { status: 200, body: { sessions: [PHONE] } });
    act(() => result.current.sessions.end("sess-laptop"));

    await waitFor(() => expect(result.current.sessions.list).toHaveLength(1));
    expect(countOf("DELETE", sessionPath("sess-laptop"))).toBe(1);
    // The stamp survives the re-read: it is what this client did, and the row
    // that is now gone said `ended 21:15` while it was still on screen.
    expect(result.current.sessions.ended["sess-laptop"]).toBeGreaterThan(0);
  });

  it("does not come apart when you end the session you are holding", async () => {
    const expired = vi.fn();
    const off = authEvents.on("expired", expired);
    const { result } = renderSystem();
    await waitFor(() => expect(result.current.sessions.list).toHaveLength(2));

    // The route clears the cookie on its way out, so the re-read behind it 401s.
    answer("GET", SESSIONS, { status: 401, body: { detail: "Not authenticated" } });
    act(() => result.current.sessions.end("sess-phone"));

    // The gate rises — `api`'s job, not this hook's — and the bench stays up
    // holding the last true thing it was told.
    await waitFor(() => expect(expired).toHaveBeenCalled());
    expect(result.current.sessions.error).toBe("Not authenticated");
    expect(result.current.credentials.list).toHaveLength(1);
    off();
  });

  it("tests a service's credentials straight after saving them", async () => {
    const { result } = renderSystem();
    await waitFor(() => expect(result.current.integrations.state["weather"]).toBe("ok"));
    const mark = calls.length;

    act(() => result.current.integrations.save("weather", { api_key: "abc" }));

    await waitFor(() => expect(result.current.integrations.saves["weather"]?.saving).toBe(false));
    const after = calls.slice(mark).map((call) => `${call.method} ${call.url}`);
    expect(after.slice(0, 2)).toEqual([
      `PUT ${credentialsPath("weather")}`,
      `GET ${statusPath("weather")}`,
    ]);
    expect(calls[mark].body).toEqual({ api_key: "abc" });
    expect(result.current.integrations.saves["weather"]?.savedAt).toBeGreaterThan(0);
    expect(result.current.integrations.saves["weather"]?.error).toBeNull();
  });

  it("calls a 403 on save a network gate rather than a bad password", async () => {
    answer("PUT", credentialsPath("weather"), {
      status: 403,
      body: { detail: "Network 203.0.113.9 is not trusted" },
    });
    const { result } = renderSystem();
    await waitFor(() => expect(result.current.integrations.state["weather"]).toBe("ok"));

    act(() => result.current.integrations.save("weather", { api_key: "abc" }));

    await waitFor(() =>
      expect(result.current.integrations.saves["weather"]?.error).toBe(
        "Network 203.0.113.9 is not trusted",
      ),
    );
    expect(result.current.integrations.saves["weather"]?.gated).toBe(true);
    expect(result.current.integrations.saves["weather"]?.saving).toBe(false);
    // Nothing was stored, so nothing is worth re-probing.
    expect(countOf("GET", statusPath("weather"))).toBe(1);
  });

  it("queues a drain and a consolidation, and leaves them queued", async () => {
    const { result } = renderSystem();
    await waitFor(() => expect(result.current.quiet.held).toBe(0));

    act(() => result.current.maintenance.drain());
    act(() => result.current.maintenance.run());

    await waitFor(() => expect(result.current.maintenance.drainedAt).toBeGreaterThan(0));
    await waitFor(() => expect(result.current.maintenance.ranAt).toBeGreaterThan(0));
    expect(countOf("POST", DRAIN)).toBe(1);
    expect(countOf("POST", LIBRARIAN)).toBe(1);
    // Neither route did anything yet — it published an internal action. The
    // counts are the server's and must not move on this client's say-so.
    expect(result.current.quiet.held).toBe(0);
    expect(result.current.maintenance.last).toBe("2026-09-07T03:00:00Z");
    expect(result.current.maintenance.reviewed).toBe(42);
  });

  it("puts the one domain an allow or an ask touches", async () => {
    const { result } = renderSystem();
    await waitFor(() => expect(result.current.attention.domains.length).toBeGreaterThan(0));

    act(() => result.current.attention.allow("fan", "fan.bathroom"));
    await waitFor(() => expect(countOf("PUT", attentionPath("fan"))).toBe(1));
    expect(calls.at(-1)?.body).toEqual({ allow: ["fan.bathroom"], ask: [] });

    act(() => result.current.attention.ask("light", "light.hall"));
    await waitFor(() => expect(countOf("PUT", attentionPath("light"))).toBe(1));
    expect(calls.at(-1)?.body).toEqual({ allow: [], ask: ["light.hall"] });
  });

  it("takes the domain the write answered with, rather than reading the list again", async () => {
    const updated: AttentionDomain = {
      domain: "fan",
      members: ["fan.bathroom"],
      seen: ["fan.bathroom", "fan.study", "switch.desk", "switch.lamp"],
    };
    answer("PUT", attentionPath("fan"), { status: 200, body: updated });
    const { result } = renderSystem();
    await waitFor(() => expect(result.current.attention.domains.length).toBeGreaterThan(0));
    const before = countOf("GET", ATTENTION);

    act(() => result.current.attention.allow("fan", "fan.bathroom"));

    await waitFor(() =>
      expect(result.current.attention.domains.find((row) => row.domain === "fan")?.members).toEqual(
        ["fan.bathroom"],
      ),
    );
    // The route reads the domain back inside its own guard, so a second GET
    // would only ask again for what the answer already carried.
    expect(countOf("GET", ATTENTION)).toBe(before);
    expect(result.current.attention.saving["fan"]).toBeFalsy();
    // Every other domain is untouched.
    expect(result.current.attention.domains).toHaveLength(attentionFixture.domains.length);
  });

  it("mints a pairing code, and forgets it when the bench is left", async () => {
    answer("POST", PAIRING, {
      status: 200,
      body: { code: "042317", expires_at: "2026-09-16T21:20:00Z", ttl_seconds: 300 },
    });
    const { result, rerender } = renderSystem();
    await waitFor(() => expect(result.current.credentials.list).toHaveLength(1));

    act(() => result.current.pairing.mint());

    await waitFor(() => expect(result.current.pairing.code).toBe("042317"));
    expect(result.current.pairing.expiresAt).toBe("2026-09-16T21:20:00Z");
    expect(result.current.pairing.minting).toBe(false);

    rerender({ on: false });
    // There is no way to re-show a code, and one left on a screen the reader
    // has walked away from is a secret with nobody watching it.
    expect(result.current.pairing.code).toBeNull();
  });

  it("does not poll: one read each, however long the bench is left open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderSystem();
    await waitFor(() => expect(result.current.sessions.list).toHaveLength(2));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });

    // The overview has its own 30 s interval, which belongs to `useOverview` and
    // is shared with the Room; these five are read when the bench opens and when
    // something this client did means they may have changed.
    expect(countOf("GET", SESSIONS)).toBe(1);
    expect(countOf("GET", CREDENTIALS)).toBe(1);
    expect(countOf("GET", INTEGRATIONS)).toBe(1);
    expect(countOf("GET", ATTENTION)).toBe(1);
    expect(countOf("GET", statusPath("home-service"))).toBe(1);
  });

  it("reports one section's failed read without emptying the others", async () => {
    answer("GET", SESSIONS, { status: 503, body: { detail: "Session store unavailable" } });
    const { result } = renderSystem();

    await waitFor(() => expect(result.current.sessions.error).toBe("Session store unavailable"));

    expect(result.current.sessions.list).toEqual([]);
    expect(result.current.credentials.list).toHaveLength(1);
    expect(result.current.attention.domains.length).toBeGreaterThan(0);
    // The spine answered, so the bench itself has nothing to complain about.
    expect(result.current.error).toBeNull();
  });

  it("keeps what the bench learned across a trip to another bench", async () => {
    const client = makeClient();
    const { result, rerender } = renderSystem(true, client);
    await waitFor(() => expect(result.current.sessions.list).toHaveLength(2));

    rerender({ on: false });
    rerender({ on: true });
    await settle();

    // The hook lives in the panel, not in the bench: the cache is still warm and
    // nothing is read a second time for the walk back.
    expect(result.current.sessions.list).toHaveLength(2);
    expect(countOf("GET", SESSIONS)).toBe(1);
  });
});
