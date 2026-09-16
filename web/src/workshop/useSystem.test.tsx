import {
  focusManager,
  onlineManager,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authEvents, type AuthEventKind } from "@/lib/auth-events";
import type { AttentionDomain } from "@/lib/system";
import { QUERY_DEFAULTS } from "@/shell/QueryProvider";
import {
  attentionFixture,
  authSession,
  credential,
  integration,
  overviewFixture,
} from "@/test/fixtures";
import { OVERVIEW_POLL_MS } from "@/room/useOverview";
import { STALE_AFTER_MS, useSystem } from "./useSystem";

const OVERVIEW = "/api/admin/overview";
const SESSIONS = "/api/auth/sessions";
const CREDENTIALS = "/api/auth/credentials";
const PAIRING = "/api/auth/pairing";
const INTEGRATIONS = "/api/integrations";
const ATTENTION = "/api/admin/attention";
const DND = "/api/admin/dnd";
const DRAIN = "/api/admin/notifications/drain";
const LIBRARIAN = "/api/admin/librarian/run";
const LOGOUT = "/api/auth/logout";

const statusPath = (name: string) => `${INTEGRATIONS}/${name}/status`;
const credentialsPath = (name: string) => `${INTEGRATIONS}/${name}/credentials`;
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

/** Every read the bench makes when it opens, in the order the hook asks for them. */
const OPENING_READS = [
  OVERVIEW,
  SESSIONS,
  CREDENTIALS,
  INTEGRATIONS,
  ATTENTION,
  statusPath("weather"),
  statusPath("home-service"),
];

/** One canned reply: the status the server answers with, and the body it sends. */
interface Answer {
  status: number;
  body: unknown;
}

const OK: Answer = { status: 200, body: { status: "ok" } };

/** One request the hook made. */
interface Call {
  url: string;
  method: string;
  body: unknown;
  signal: AbortSignal | null;
}

let calls: Call[];
/** What each `METHOD url` answers next; tests move entries to prove a re-read. */
let routes: Map<string, Answer>;
/** Routes whose next requests are held open rather than answered. */
let holding: Set<string>;
/** The resolvers of the held requests, oldest first, by route. */
let parked: Map<string, ((answer: Answer) => void)[]>;
/** Every auth-event listener a test added, torn down in cleanup. */
let listeners: (() => void)[];

const route = (method: string, url: string): string => `${method} ${url}`;
const answer = (method: string, url: string, reply: Answer): void =>
  void routes.set(route(method, url), reply);

const reads = (): string[] => calls.filter((call) => call.method === "GET").map((c) => c.url);
const countOf = (method: string, url: string): number =>
  calls.filter((call) => call.method === method && call.url === url).length;
const sent = (method: string, url: string): Call | undefined =>
  calls.find((call) => call.method === method && call.url === url);

/** Hold every further request to this route open, so a test can read mid-flight state. */
const hold = (method: string, url: string): void => void holding.add(route(method, url));
/** Let the route answer normally again. Requests already parked stay parked. */
const unhold = (method: string, url: string): void => void holding.delete(route(method, url));

/**
 * Answer one held request on a route, oldest first by default. `index` is how a
 * test answers them out of order — the case every supersession guard exists for.
 */
async function releaseNext(
  method: string,
  url: string,
  reply?: Answer,
  index = 0,
): Promise<void> {
  const key = route(method, url);
  await waitFor(() => expect((parked.get(key) ?? []).length).toBeGreaterThan(index));
  const [resolve] = (parked.get(key) ?? []).splice(index, 1);
  await act(async () => {
    resolve?.(reply ?? routes.get(key) ?? OK);
    await Promise.resolve();
  });
}

/** Watch an auth event, and unsubscribe in cleanup rather than leaking into the next test. */
function listen(event: AuthEventKind): ReturnType<typeof vi.fn> {
  const heard = vi.fn();
  listeners.push(authEvents.on(event, heard));
  return heard;
}

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
        signal: init?.signal ?? null,
      });
      const key = route(method, url);
      // Anything unstaged is a write the route answers `{"status":"ok"}` to —
      // every write on this bench does, bar the two that answer with a record.
      const staged = routes.get(key) ?? OK;
      const reply = holding.has(key)
        ? await new Promise<Answer>((resolve) => {
            parked.set(key, [...(parked.get(key) ?? []), resolve]);
          })
        : staged;
      return new Response(JSON.stringify(reply.body), { status: reply.status });
    }),
  );
}

/**
 * A client on the app's own policy. A test client with its own `retry: false`
 * would prove the harness's default rather than the source's: the probe retry
 * this bench leans on and the attention retry it turns off are both invisible
 * under one. Only the backoff is the harness's — a real 1 s wait between two
 * attempts buys the assertions nothing but seconds.
 */
const makeClient = (): QueryClient =>
  new QueryClient({
    defaultOptions: { ...QUERY_DEFAULTS, queries: { ...QUERY_DEFAULTS.queries, retryDelay: 0 } },
  });

const onHeld = vi.fn();

function renderSystem(enabled = true, client = makeClient()) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(({ on }: { on: boolean }) => useSystem(on, onHeld), {
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

/** The bench, open and fully read. */
async function openBench(client = makeClient()) {
  const rendered = renderSystem(true, client);
  await waitFor(() => expect(rendered.result.current.integrations.rows["home-service"]?.state).toBe("ok"));
  await settle();
  return rendered;
}

beforeEach(() => {
  calls = [];
  holding = new Set<string>();
  parked = new Map<string, ((answer: Answer) => void)[]>();
  listeners = [];
  onHeld.mockClear();
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

// `fetch` is un-stubbed by `unstubGlobals` in vite.config.ts; the clock, the
// listeners and anything still parked are this file's own business.
afterEach(() => {
  for (const off of listeners) off();
  for (const queue of parked.values()) for (const resolve of queue) resolve(OK);
  focusManager.setFocused(undefined);
  onlineManager.setOnline(true);
  vi.useRealTimers();
});

describe("useSystem", () => {
  it("reads nothing at all while the bench is not showing", async () => {
    const { result } = renderSystem(false);
    await settle();

    // The overview included: `useOverview` takes the same gate, so a closed
    // bench does not start a second poller for a screen nobody is looking at.
    expect(calls).toEqual([]);
    expect(result.current.overview).toBeUndefined();
    expect(result.current.sessions.list).toEqual([]);
    expect(result.current.credentials.list).toEqual([]);
    expect(result.current.integrations.list).toEqual([]);
    expect(result.current.integrations.rows).toEqual({});
    expect(result.current.attention.domains).toEqual([]);
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("stays shut however long it is closed, and however often the window is focused", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderSystem(false);
    await settle();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    act(() => focusManager.setFocused(true));
    await settle();

    // Every read on this bench is gated, the per-integration probes included:
    // a closed bench must not poll the house and must not answer a focus.
    expect(calls).toEqual([]);
  });

  it("stops reading the moment the bench closes, cache and all", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { rerender } = await openBench();
    const mark = calls.length;

    rerender({ on: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    act(() => focusManager.setFocused(true));
    await settle();

    // Now every query is cached and every one of them is stale: the gate is the
    // only thing standing between a closed bench and a refetch of all seven.
    expect(calls).toHaveLength(mark);
  });

  it("reads its six sources once when it opens, and nothing else", async () => {
    const { result } = await openBench();

    expect(reads()).toEqual(OPENING_READS);
    // The raw vitals, handed on for the spend card's own formatters.
    expect(result.current.overview).toEqual(overviewFixture);
    expect(result.current.sessions.list.map((row) => row.session_id)).toEqual([
      "sess-phone",
      "sess-laptop",
    ]);
    expect(result.current.credentials.list).toHaveLength(1);
    expect(result.current.integrations.list.map((row) => row.name)).toEqual([
      "weather",
      "home-service",
    ]);
    expect(result.current.attention.domains).toHaveLength(attentionFixture.domains.length);
    expect(result.current.error).toBeNull();
  });

  it("reads an empty envelope as an empty list rather than as nothing at all", async () => {
    answer("GET", SESSIONS, { status: 200, body: {} });
    answer("GET", CREDENTIALS, { status: 200, body: {} });
    answer("GET", ATTENTION, { status: 200, body: {} });
    answer("GET", INTEGRATIONS, { status: 200, body: [] });
    const { result } = renderSystem();

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.sessions.list).toEqual([]);
    expect(result.current.credentials.list).toEqual([]);
    expect(result.current.attention.domains).toEqual([]);
    expect(result.current.sessions.error).toBeNull();
    // A registry with no home service in it is a fact about the house — and a
    // different sentence from one whose registry has not been read.
    expect(result.current.health.home.note).toBe("home assistant · not registered");
  });

  it("probes each integration once and reports its round trip", async () => {
    const { result } = await openBench();

    expect(countOf("GET", statusPath("weather"))).toBe(1);
    expect(countOf("GET", statusPath("home-service"))).toBe(1);
    expect(result.current.integrations.rows["weather"]).toEqual({
      state: "ok",
      latency: 42,
      status: null,
    });
    expect(result.current.integrations.rows["home-service"]?.latency).toBe(210);
  });

  it("retries a probe that never reached the service, and keeps the row", async () => {
    answer("GET", statusPath("weather"), { status: 503, body: { detail: "probe unavailable" } });
    const { result } = renderSystem();

    await waitFor(() => expect(result.current.integrations.rows["weather"]?.state).toBe("failed"));

    // A thrown probe is the request failing — the proxy reloading under us — and
    // is retried like any other transport failure.
    expect(countOf("GET", statusPath("weather"))).toBe(2);
    expect(result.current.integrations.list.map((row) => row.name)).toContain("weather");
    expect(result.current.integrations.rows["weather"]?.latency).toBeNull();
    expect(result.current.integrations.rows["weather"]?.status).toBe(503);
  });

  it("does not ask a sick service twice", async () => {
    answer("GET", statusPath("weather"), {
      status: 200,
      body: { name: "weather", healthy: false, latency_ms: 18 },
    });
    const { result } = renderSystem();

    await waitFor(() => expect(result.current.integrations.rows["weather"]?.state).toBe("failed"));
    await settle();

    // A sick service is a 200 with `healthy: false`, which is an answer.
    expect(countOf("GET", statusPath("weather"))).toBe(1);
    expect(result.current.integrations.rows["weather"]?.latency).toBe(18);
  });

  it("does not ask the attention store twice when it is down", async () => {
    answer("GET", ATTENTION, { status: 503, body: { detail: "Attention store unavailable" } });
    const { result } = renderSystem();

    await waitFor(() =>
      expect(result.current.attention.error).toBe("Attention store unavailable"),
    );
    await settle();

    // 503 here is the store being unreachable — a fact to report, not a question
    // to ask twice.
    expect(countOf("GET", ATTENTION)).toBe(1);
    expect(result.current.attention.domains).toEqual([]);
  });

  it("derives the health grid from the overview and the home service's probe", async () => {
    const { result } = await openBench();

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
      note: "event rate · 5-min mean",
      alive: true,
    });
    expect(result.current.health.home).toEqual({
      value: "ok",
      note: "home assistant · 210 ms",
      alive: true,
    });
  });

  it("claims nothing before the reads that would settle it", () => {
    const { result } = renderSystem();

    // Synchronous first render: nothing has come back yet.
    expect(result.current.health.bus.value).toBe("unknown");
    expect(result.current.health.bus.note).toBe("bus · redis · not read yet");
    expect(result.current.health.reflex.value).toBe("—");
    expect(result.current.health.rate.value).toBe("— ev/s");
    expect(result.current.health.home.note).toBe("home assistant · not read yet");
    expect(result.current.maintenance.idleMinutes).toBeNull();
    // Zero and not one: the held count is a number the bench prints beside a
    // control, and a default of anything else is a queue nobody reported.
    expect(result.current.quiet.held).toBe(0);
    expect(result.current.loading).toBe(true);
  });

  it("is loading for its own reads, and not for the overview's poll", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = await openBench();
    expect(result.current.loading).toBe(false);
    hold("GET", OVERVIEW);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });
    await waitFor(() => expect((parked.get(route("GET", OVERVIEW)) ?? []).length).toBe(1));

    // The overview polls every 30 s and is shared with the Room; anything bound
    // to `loading` would blink on an idle bench for a request nobody made — and
    // this is the frame it would blink on.
    expect(countOf("GET", OVERVIEW)).toBe(2);
    expect(result.current.loading).toBe(false);
    unhold("GET", OVERVIEW);
    await releaseNext("GET", OVERVIEW);
  });

  it("moves the do-not-disturb switch on the server's own read, not on its own echo", async () => {
    const { result } = await openBench();
    expect(result.current.quiet.active).toBe(false);
    // The route writes Redis before it answers, so the read behind the write
    // reports the new position. Staged first, because the write is what sends
    // this client back for it.
    answer("GET", OVERVIEW, {
      status: 200,
      body: { ...overviewFixture, dnd: { active: true, until: "2026-09-16T22:00:00Z" } },
    });
    hold("GET", OVERVIEW);

    act(() => result.current.quiet.set(true, "2026-09-16T22:00:00Z"));
    await waitFor(() => expect((parked.get(route("GET", OVERVIEW)) ?? []).length).toBe(1));

    // The write has answered and the confirmation has not: busy tracks the write
    // alone, and the switch has not moved.
    expect(result.current.quiet.setting).toBe(false);
    expect(result.current.quiet.active).toBe(false);

    unhold("GET", OVERVIEW);
    await releaseNext("GET", OVERVIEW);

    await waitFor(() => expect(result.current.quiet.active).toBe(true));
    expect(sent("POST", DND)?.body).toEqual({
      active: true,
      until: "2026-09-16T22:00:00Z",
    });
    expect(result.current.quiet.until).toBe("2026-09-16T22:00:00Z");
    expect(result.current.quiet.error).toBeNull();
  });

  it("clears do-not-disturb with an active false and no until", async () => {
    answer("GET", OVERVIEW, {
      status: 200,
      body: { ...overviewFixture, dnd: { active: true, until: null } },
    });
    const { result } = await openBench();
    expect(result.current.quiet.active).toBe(true);
    answer("GET", OVERVIEW, { status: 200, body: { ...overviewFixture, dnd: { active: false } } });

    act(() => result.current.quiet.set(false, null));

    await waitFor(() => expect(result.current.quiet.active).toBe(false));
    const body = sent("POST", DND)?.body as object;
    expect(body).toEqual({ active: false });
    // `until: null` and no `until` at all are one rule: the key is only ever
    // there when there is an instant to put in it.
    expect(Object.keys(body)).not.toContain("until");
  });

  it("does not print an expiry for a switch that is off", async () => {
    // A cleared quiet can still carry the instant it was last set for.
    answer("GET", OVERVIEW, {
      status: 200,
      body: { ...overviewFixture, dnd: { active: false, until: "2026-09-16T22:00:00Z" } },
    });
    const { result } = await openBench();

    expect(result.current.quiet.active).toBe(false);
    expect(result.current.quiet.until).toBeNull();
  });

  it("leaves the switch where it was when the write is refused", async () => {
    answer("POST", DND, { status: 503, body: { detail: "Redis unavailable" } });
    const { result } = await openBench();
    const before = countOf("GET", OVERVIEW);

    act(() => result.current.quiet.set(true, null));

    await waitFor(() => expect(result.current.quiet.error).toBe("Redis unavailable"));
    expect(result.current.quiet.active).toBe(false);
    expect(result.current.quiet.setting).toBe(false);
    // Nothing was written, so there is nothing to go back and read.
    expect(countOf("GET", OVERVIEW)).toBe(before);
  });

  it("says so when the write landed and the read behind it did not", async () => {
    const { result } = await openBench();
    answer("GET", OVERVIEW, { status: 503, body: { detail: "Redis unavailable" } });

    act(() => result.current.quiet.set(true, null));

    await waitFor(() =>
      expect(result.current.quiet.error).toBe("Set, but the house has not confirmed it yet."),
    );
    // A switch that visibly snapped back with no explanation on it would be the
    // worse lie: the write did land.
    expect(result.current.quiet.active).toBe(false);
    expect(result.current.quiet.setting).toBe(false);
  });

  it("lets the newest tap on the switch have the last word", async () => {
    const { result } = await openBench();
    hold("POST", DND);

    act(() => result.current.quiet.set(true, "2026-09-16T22:00:00Z"));
    act(() => result.current.quiet.set(false, null));
    await waitFor(() => expect((parked.get(route("POST", DND)) ?? []).length).toBe(2));
    unhold("POST", DND);

    // The superseded tap answers first, and with a refusal: neither its failure
    // nor its position may reach the switch.
    await releaseNext("POST", DND, { status: 503, body: { detail: "Redis unavailable" } });
    await releaseNext("POST", DND);

    await waitFor(() => expect(result.current.quiet.setting).toBe(false));
    expect(result.current.quiet.error).toBeNull();
    expect(result.current.quiet.active).toBe(false);
  });

  it("stamps a session ended when the server says so, and not when the button is pressed", async () => {
    const { result } = await openBench();
    hold("DELETE", sessionPath("sess-laptop"));

    act(() => result.current.sessions.end("sess-laptop"));
    await waitFor(() => expect(result.current.sessions.ending["sess-laptop"]).toBe(true));

    // `applied` is a confirmed-state word, and a client that claimed it before
    // the answer would have to take it back.
    expect(result.current.sessions.ended["sess-laptop"]).toBeUndefined();

    unhold("DELETE", sessionPath("sess-laptop"));
    answer("GET", SESSIONS, { status: 200, body: { sessions: [PHONE] } });
    await releaseNext("DELETE", sessionPath("sess-laptop"), { status: 200, body: { deleted: true } });

    await waitFor(() => expect(result.current.sessions.list).toHaveLength(1));
    expect(result.current.sessions.ended["sess-laptop"]).toBeGreaterThan(0);
    expect(result.current.sessions.ending["sess-laptop"]).toBeUndefined();
    expect(countOf("DELETE", sessionPath("sess-laptop"))).toBe(1);
  });

  it("does not come apart when you end the session you are holding", async () => {
    const expired = listen("expired");
    const { result } = await openBench();

    // The route clears the cookie on its way out, so the re-read behind it 401s.
    answer("GET", SESSIONS, { status: 401, body: { detail: "Not authenticated" } });
    act(() => result.current.sessions.end("sess-phone"));

    // The gate rises — `api`'s job, not this hook's — and the bench stays up
    // holding the last true thing it was told.
    await waitFor(() => expect(expired).toHaveBeenCalled());
    expect(result.current.sessions.error).toBe("Not authenticated");
    expect(result.current.credentials.list).toHaveLength(1);
    // A 401 is an answer, not a failure to reach the house.
    expect(countOf("GET", SESSIONS)).toBe(2);
  });

  it("reports a refused end, and clears the complaint on the next try", async () => {
    answer("DELETE", sessionPath("sess-laptop"), { status: 503, body: { detail: "Redis unavailable" } });
    const { result } = await openBench();

    act(() => result.current.sessions.end("sess-laptop"));
    await waitFor(() => expect(result.current.sessions.error).toBe("Redis unavailable"));
    expect(result.current.sessions.ending["sess-laptop"]).toBeUndefined();
    expect(result.current.sessions.list).toHaveLength(2);

    answer("DELETE", sessionPath("sess-laptop"), { status: 200, body: { deleted: true } });
    act(() => result.current.sessions.end("sess-laptop"));

    await waitFor(() => expect(result.current.sessions.ended["sess-laptop"]).toBeGreaterThan(0));
    expect(result.current.sessions.error).toBeNull();
  });

  it("lets the newest end of one session have the last word", async () => {
    const { result } = await openBench();
    hold("DELETE", sessionPath("sess-laptop"));

    act(() => result.current.sessions.end("sess-laptop"));
    act(() => result.current.sessions.end("sess-laptop"));
    await waitFor(() =>
      expect((parked.get(route("DELETE", sessionPath("sess-laptop"))) ?? []).length).toBe(2),
    );
    unhold("DELETE", sessionPath("sess-laptop"));

    await releaseNext("DELETE", sessionPath("sess-laptop"), {
      status: 503,
      body: { detail: "Redis unavailable" },
    });
    await releaseNext("DELETE", sessionPath("sess-laptop"), { status: 200, body: { deleted: true } });

    await waitFor(() => expect(result.current.sessions.ended["sess-laptop"]).toBeGreaterThan(0));
    expect(result.current.sessions.error).toBeNull();
  });

  it("encodes a session id into the path it deletes", async () => {
    answer("GET", SESSIONS, {
      status: 200,
      body: { sessions: [authSession({ session_id: "sess/one two" })] },
    });
    const { result } = await openBench();

    act(() => result.current.sessions.end("sess/one two"));

    await waitFor(() => expect(countOf("DELETE", sessionPath("sess%2Fone%20two"))).toBe(1));
  });

  it("tests a service's credentials straight after saving them", async () => {
    const { result } = await openBench();
    const mark = calls.length;
    hold("GET", statusPath("weather"));

    act(() => result.current.integrations.save("weather", { api_key: "abc" }));

    // Saved is not working: the row says `saved · testing` until the round trip
    // it promises has answered.
    await waitFor(() => expect(result.current.integrations.rows["weather"]?.state).toBe("queued"));
    expect(result.current.integrations.saves["weather"]?.savedAt).toBeGreaterThan(0);
    expect(result.current.integrations.saves["weather"]?.saving).toBe(true);
    // The probe is one of this bench's own reads, and the only one in flight.
    expect(result.current.loading).toBe(true);

    unhold("GET", statusPath("weather"));
    await releaseNext("GET", statusPath("weather"));

    await waitFor(() => expect(result.current.integrations.saves["weather"]?.saving).toBe(false));
    expect(calls.slice(mark).map((call) => `${call.method} ${call.url}`).slice(0, 2)).toEqual([
      `PUT ${credentialsPath("weather")}`,
      `GET ${statusPath("weather")}`,
    ]);
    expect(calls[mark].body).toEqual({ api_key: "abc" });
    expect(result.current.integrations.rows["weather"]?.state).toBe("ok");
    expect(result.current.integrations.saves["weather"]?.error).toBeNull();
    // `configured` moved, and the form's placeholders are drawn from it.
    await waitFor(() => expect(countOf("GET", INTEGRATIONS)).toBe(2));
  });

  it("says testing, not queued, while the credentials are still going", async () => {
    const { result } = await openBench();
    hold("PUT", credentialsPath("weather"));

    act(() => result.current.integrations.save("weather", { api_key: "abc" }));

    await waitFor(() => expect(result.current.integrations.saves["weather"]?.saving).toBe(true));
    expect(result.current.integrations.saves["weather"]?.savedAt).toBeNull();
    expect(result.current.integrations.rows["weather"]?.state).toBe("testing");
  });

  it("owes the reader a probe when they walk away mid-save", async () => {
    const { result, rerender } = await openBench();
    hold("PUT", credentialsPath("weather"));

    act(() => result.current.integrations.save("weather", { api_key: "abc" }));
    await waitFor(() => expect(result.current.integrations.saves["weather"]?.saving).toBe(true));
    rerender({ on: false });
    unhold("PUT", credentialsPath("weather"));
    await releaseNext("PUT", credentialsPath("weather"));

    // A closed bench probes nothing, so the round trip cannot happen now — but
    // the credentials did change, and the cached answer is from before them.
    await waitFor(() => expect(result.current.integrations.saves["weather"]?.saving).toBe(false));
    expect(countOf("GET", statusPath("weather"))).toBe(1);

    rerender({ on: true });

    // Well inside the 30 s the probe would otherwise stay fresh for.
    await waitFor(() => expect(countOf("GET", statusPath("weather"))).toBe(2));
    expect(countOf("GET", statusPath("home-service"))).toBe(1);
  });

  it("calls a 403 on save a network gate rather than a bad password", async () => {
    const denied = listen("denied");
    answer("PUT", credentialsPath("weather"), {
      status: 403,
      body: { detail: "Network 203.0.113.9 is not trusted" },
    });
    const { result } = await openBench();

    act(() => result.current.integrations.save("weather", { api_key: "abc" }));

    await waitFor(() =>
      expect(result.current.integrations.saves["weather"]?.error).toBe(
        "Network 203.0.113.9 is not trusted",
      ),
    );
    expect(result.current.integrations.saves["weather"]?.gated).toBe(true);
    expect(result.current.integrations.saves["weather"]?.saving).toBe(false);
    expect(result.current.integrations.saves["weather"]?.savedAt).toBeNull();
    // The Denied gate is `api`'s doing and rises over the whole screen; the row
    // says which of the two gates refused once it has been dismissed.
    expect(denied).toHaveBeenCalled();
    // Nothing was stored, so nothing is worth re-probing.
    expect(countOf("GET", statusPath("weather"))).toBe(1);
  });

  it("does not call every other refusal a network gate", async () => {
    answer("PUT", credentialsPath("weather"), { status: 400, body: { detail: "url is required" } });
    const { result } = await openBench();

    act(() => result.current.integrations.save("weather", { api_key: "" }));

    await waitFor(() =>
      expect(result.current.integrations.saves["weather"]?.error).toBe("url is required"),
    );
    expect(result.current.integrations.saves["weather"]?.gated).toBe(false);
  });

  it("lets the newest save of one service have the last word, and leaves the rest alone", async () => {
    const { result } = await openBench();
    hold("PUT", credentialsPath("weather"));

    act(() => result.current.integrations.save("weather", { api_key: "old" }));
    act(() => result.current.integrations.save("weather", { api_key: "new" }));
    await waitFor(() =>
      expect((parked.get(route("PUT", credentialsPath("weather"))) ?? []).length).toBe(2),
    );
    unhold("PUT", credentialsPath("weather"));

    await releaseNext("PUT", credentialsPath("weather"), {
      status: 403,
      body: { detail: "Network 203.0.113.9 is not trusted" },
    });
    await releaseNext("PUT", credentialsPath("weather"));

    await waitFor(() => expect(result.current.integrations.saves["weather"]?.saving).toBe(false));
    expect(result.current.integrations.saves["weather"]?.error).toBeNull();
    expect(result.current.integrations.saves["weather"]?.gated).toBe(false);
    // One service refusing a credential says nothing about the other.
    expect(result.current.integrations.saves["home-service"]).toBeUndefined();
    expect(result.current.integrations.rows["home-service"]?.state).toBe("ok");
  });

  it("encodes a service name into the paths it writes and probes", async () => {
    const odd = integration({ name: "home service/1" });
    answer("GET", INTEGRATIONS, { status: 200, body: [odd] });
    answer("GET", statusPath("home%20service%2F1"), {
      status: 200,
      body: { name: "home service/1", healthy: true, latency_ms: 12 },
    });
    const { result } = renderSystem();
    await waitFor(() =>
      expect(result.current.integrations.rows["home service/1"]?.state).toBe("ok"),
    );

    act(() => result.current.integrations.save("home service/1", { url: "x" }));

    await waitFor(() =>
      expect(countOf("PUT", credentialsPath("home%20service%2F1"))).toBe(1),
    );
    expect(countOf("GET", statusPath("home%20service%2F1"))).toBe(2);
  });

  it("puts the one domain an allow or an ask touches", async () => {
    const { result } = await openBench();

    act(() => result.current.attention.allow("fan", "fan.bathroom"));
    await waitFor(() => expect(countOf("PUT", attentionPath("fan"))).toBe(1));
    expect(sent("PUT", attentionPath("fan"))?.body).toEqual({
      allow: ["fan.bathroom"],
      ask: [],
    });

    act(() => result.current.attention.ask("light", "light.hall"));
    await waitFor(() => expect(countOf("PUT", attentionPath("light"))).toBe(1));
    expect(sent("PUT", attentionPath("light"))?.body).toEqual({ allow: [], ask: ["light.hall"] });
  });

  it("encodes a domain into the path it writes", async () => {
    const { result } = await openBench();

    act(() => result.current.attention.allow("input boolean/x", "input_boolean.guest"));

    await waitFor(() => expect(countOf("PUT", attentionPath("input%20boolean%2Fx"))).toBe(1));
  });

  it("takes the domain the write answered with, rather than reading the list again", async () => {
    const updated: AttentionDomain = {
      domain: "fan",
      members: ["fan.bathroom"],
      seen: ["fan.bathroom", "fan.study"],
    };
    answer("PUT", attentionPath("fan"), { status: 200, body: updated });
    const { result } = await openBench();
    const before = countOf("GET", ATTENTION);
    hold("PUT", attentionPath("fan"));

    act(() => result.current.attention.allow("fan", "fan.bathroom"));
    await waitFor(() => expect(result.current.attention.saving["fan"]).toBe(true));
    unhold("PUT", attentionPath("fan"));
    await releaseNext("PUT", attentionPath("fan"));

    await waitFor(() =>
      expect(result.current.attention.domains.find((row) => row.domain === "fan")).toEqual(updated),
    );
    // The route reads the domain back inside its own guard, so a second GET
    // would only ask again for what the answer already carried.
    expect(countOf("GET", ATTENTION)).toBe(before);
    expect(result.current.attention.saving["fan"]).toBeUndefined();
    expect(result.current.attention.error).toBeNull();
    // The whole list, not the row a `find` turns up: mapping the answer onto
    // the rows it does *not* describe leaves the length right and the domain
    // still findable, while overwriting every other domain in the house.
    expect(result.current.attention.domains).toEqual(
      attentionFixture.domains.map((row) => (row.domain === "fan" ? updated : row)),
    );
  });

  it("appends a domain the cached list had never seen", async () => {
    const fresh: AttentionDomain = {
      domain: "vacuum",
      members: ["vacuum.downstairs"],
      seen: ["vacuum.downstairs"],
    };
    answer("PUT", attentionPath("vacuum"), { status: 200, body: fresh });
    const { result } = await openBench();

    act(() => result.current.attention.allow("vacuum", "vacuum.downstairs"));

    // A domain the Reflex has only just observed is not in a list read before
    // it existed; dropping it would lose the grant that was just made.
    await waitFor(() =>
      expect(result.current.attention.domains.at(-1)).toEqual(fresh),
    );
    expect(result.current.attention.domains).toHaveLength(attentionFixture.domains.length + 1);
  });

  it("goes back and looks when an attention write is refused", async () => {
    answer("PUT", attentionPath("fan"), { status: 503, body: { detail: "Attention store unavailable" } });
    const { result } = await openBench();
    const before = countOf("GET", ATTENTION);

    act(() => result.current.attention.allow("fan", "fan.bathroom"));

    await waitFor(() =>
      expect(result.current.attention.error).toBe("Attention store unavailable"),
    );
    // The route writes one entity at a time and is explicit that the writes are
    // not transactional: what is on screen is no longer evidence.
    await waitFor(() => expect(countOf("GET", ATTENTION)).toBe(before + 1));
    expect(result.current.attention.saving["fan"]).toBeUndefined();
  });

  it("lets the newest write to one domain have the last word", async () => {
    const second: AttentionDomain = { domain: "fan", members: [], seen: ["fan.bathroom"] };
    const { result } = await openBench();
    hold("PUT", attentionPath("fan"));

    act(() => result.current.attention.allow("fan", "fan.bathroom"));
    act(() => result.current.attention.ask("fan", "fan.bathroom"));
    await waitFor(() =>
      expect((parked.get(route("PUT", attentionPath("fan"))) ?? []).length).toBe(2),
    );
    unhold("PUT", attentionPath("fan"));

    await releaseNext("PUT", attentionPath("fan"), {
      status: 200,
      body: { domain: "fan", members: ["fan.bathroom"], seen: ["fan.bathroom"] },
    });
    await releaseNext("PUT", attentionPath("fan"), { status: 200, body: second });

    await waitFor(() =>
      expect(result.current.attention.domains.find((row) => row.domain === "fan")).toEqual(second),
    );
    expect(result.current.attention.saving["fan"]).toBeUndefined();
  });

  it("mints a pairing code, and forgets it when the bench is left", async () => {
    answer("POST", PAIRING, {
      status: 200,
      body: { code: "042317", expires_at: "2026-09-16T21:20:00Z", ttl_seconds: 300 },
    });
    const { result, rerender } = await openBench();
    hold("POST", PAIRING);

    act(() => result.current.pairing.mint());
    await waitFor(() => expect(result.current.pairing.minting).toBe(true));
    unhold("POST", PAIRING);
    await releaseNext("POST", PAIRING);

    await waitFor(() => expect(result.current.pairing.code).toBe("042317"));
    expect(result.current.pairing.expiresAt).toBe("2026-09-16T21:20:00Z");
    expect(result.current.pairing.minting).toBe(false);

    rerender({ on: false });
    // There is no way to re-show a code, and one left on a screen the reader
    // has walked away from is a secret with nobody watching it.
    expect(result.current.pairing.code).toBeNull();
    expect(result.current.pairing.expiresAt).toBeNull();
  });

  it("keeps the newest of two codes, whichever lands first", async () => {
    const { result } = await openBench();
    hold("POST", PAIRING);

    act(() => result.current.pairing.mint());
    act(() => result.current.pairing.mint());
    await waitFor(() => expect((parked.get(route("POST", PAIRING)) ?? []).length).toBe(2));
    unhold("POST", PAIRING);

    // The newer mint answers first; the older one must not overwrite it, or the
    // screen would show a code the server has already replaced — and the live
    // one would be nowhere.
    await releaseNext(
      "POST",
      PAIRING,
      { status: 200, body: { code: "222222", expires_at: "2026-09-16T21:25:00Z" } },
      1,
    );
    await waitFor(() => expect(result.current.pairing.code).toBe("222222"));
    await releaseNext("POST", PAIRING, {
      status: 200,
      body: { code: "111111", expires_at: "2026-09-16T21:20:00Z" },
    });
    await settle();

    expect(result.current.pairing.code).toBe("222222");
    expect(result.current.pairing.minting).toBe(false);
  });

  it("abandons a mint the reader has walked away from", async () => {
    const { result, unmount } = await openBench();
    hold("POST", PAIRING);

    act(() => result.current.pairing.mint());
    await waitFor(() => expect(sent("POST", PAIRING)).toBeDefined());
    expect(sent("POST", PAIRING)?.signal?.aborted).toBe(false);

    unmount();

    // A code minted for a screen that has closed is live on the server for five
    // minutes with no surface able to show it.
    expect(sent("POST", PAIRING)?.signal?.aborted).toBe(true);
  });

  it("clears a refused mint's complaint on the next attempt", async () => {
    answer("POST", PAIRING, { status: 503, body: { detail: "Redis unavailable" } });
    const { result } = await openBench();

    act(() => result.current.pairing.mint());
    await waitFor(() => expect(result.current.pairing.error).toBe("Redis unavailable"));
    expect(result.current.pairing.minting).toBe(false);
    expect(result.current.pairing.code).toBeNull();

    answer("POST", PAIRING, { status: 200, body: { code: "042317", expires_at: "2026-09-16T21:20:00Z" } });
    act(() => result.current.pairing.mint());

    await waitFor(() => expect(result.current.pairing.code).toBe("042317"));
    expect(result.current.pairing.error).toBeNull();
  });

  it("forgets a refused mint when the bench is left, not just while it is shut", async () => {
    answer("POST", PAIRING, { status: 503, body: { detail: "Redis unavailable" } });
    const { result, rerender } = await openBench();
    act(() => result.current.pairing.mint());
    await waitFor(() => expect(result.current.pairing.error).toBe("Redis unavailable"));

    rerender({ on: false });
    rerender({ on: true });

    // The complaint belongs to a tap nobody made this time round: a stale one
    // waiting on the bench would read as a mint that had just failed.
    expect(result.current.pairing.error).toBeNull();
    expect(result.current.pairing.code).toBeNull();
  });

  it("signs this device out, and lets the 401 behind it raise the gate", async () => {
    const { result } = await openBench();
    const expired = listen("expired");
    hold("POST", LOGOUT);

    act(() => result.current.credentials.signOut());
    await waitFor(() => expect(result.current.credentials.signingOut).toBe(true));
    // Staged before the route answers, because the cookie is cleared on the way
    // out: every read after it is a read without one.
    answer("GET", SESSIONS, { status: 401, body: { detail: "Not authenticated" } });
    unhold("POST", LOGOUT);
    await releaseNext("POST", LOGOUT);

    await waitFor(() => expect(result.current.credentials.signingOut).toBe(false));
    expect(sent("POST", LOGOUT)).toBeDefined();
    // The re-read behind it is the request that 401s. Raising the gate is
    // `api`'s job, not the bench's: the sections stay up behind it holding what
    // they were last told.
    await waitFor(() => expect(expired).toHaveBeenCalled());
  });

  it("says why a refused sign-out did not land, and stays signed in", async () => {
    answer("POST", LOGOUT, { status: 503, body: { detail: "Session store unavailable" } });
    const { result } = await openBench();

    act(() => result.current.credentials.signOut());

    await waitFor(() => expect(result.current.credentials.error).toBe("Session store unavailable"));
    expect(result.current.credentials.signingOut).toBe(false);
    // The passkeys are still on screen: a refused sign-out changed nothing.
    expect(result.current.credentials.list).toHaveLength(1);
  });

  it("queues a drain and a consolidation, and leaves them queued", async () => {
    const { result } = await openBench();

    act(() => result.current.maintenance.drain());
    await waitFor(() => expect(result.current.maintenance.drainedAt).toBeGreaterThan(0));
    expect(countOf("POST", DRAIN)).toBe(1);
    expect(countOf("POST", LIBRARIAN)).toBe(0);
    // Neither route did anything yet — it published an internal action. The
    // counts are the server's and must not move on this client's say-so.
    expect(result.current.quiet.held).toBe(0);
    expect(result.current.maintenance.ranAt).toBeNull();

    act(() => result.current.maintenance.run());
    await waitFor(() => expect(result.current.maintenance.ranAt).toBeGreaterThan(0));
    expect(countOf("POST", LIBRARIAN)).toBe(1);
    expect(countOf("POST", DRAIN)).toBe(1);
    expect(result.current.maintenance.last).toBe("2026-09-07T03:00:00Z");
    expect(result.current.maintenance.reviewed).toBe(42);
    expect(result.current.maintenance.next).toBe("2026-09-08T03:00:00Z");
  });

  it("does not stamp a queue the server refused, and clears the complaint next time", async () => {
    answer("POST", DRAIN, { status: 503, body: { detail: "Redis unavailable" } });
    const { result } = await openBench();

    act(() => result.current.maintenance.drain());
    await waitFor(() => expect(result.current.maintenance.drainError).toBe("Redis unavailable"));
    expect(result.current.maintenance.drainedAt).toBeNull();

    answer("POST", DRAIN, OK);
    act(() => result.current.maintenance.drain());

    await waitFor(() => expect(result.current.maintenance.drainedAt).toBeGreaterThan(0));
    expect(result.current.maintenance.drainError).toBeNull();
  });

  // One slot each. The two controls sit in different sections of the bench, and
  // a shared string would print the notifier's refusal under the Librarian's
  // button — two failures neither the reader nor a test could tell apart.
  it("keeps a refused drain and a refused run apart", async () => {
    answer("POST", DRAIN, { status: 503, body: { detail: "Redis unavailable" } });
    answer("POST", LIBRARIAN, { status: 409, body: { detail: "A run is already going" } });
    const { result } = await openBench();

    act(() => result.current.maintenance.drain());
    await waitFor(() => expect(result.current.maintenance.drainError).toBe("Redis unavailable"));
    expect(result.current.maintenance.runError).toBeNull();

    act(() => result.current.maintenance.run());
    await waitFor(() => expect(result.current.maintenance.runError).toBe("A run is already going"));
    // The drain's refusal is still the drain's; the run did not clear it.
    expect(result.current.maintenance.drainError).toBe("Redis unavailable");
  });

  it("reports the idle timeout the house named", async () => {
    const { result } = await openBench();
    expect(result.current.maintenance.idleMinutes).toBe(30);
  });

  it("says nothing about an idle timeout the house reported as zero", async () => {
    // A zero would print `0 minutes`, which is a guess wearing a number — and
    // the row has nothing to say until it is told.
    answer("GET", OVERVIEW, {
      status: 200,
      body: { ...overviewFixture, session: { idle_minutes: 0 } },
    });
    const { result } = await openBench();

    expect(result.current.maintenance.idleMinutes).toBeNull();
  });

  it("keeps a one-minute idle timeout, which is a real setting and not a zero", async () => {
    answer("GET", OVERVIEW, {
      status: 200,
      body: { ...overviewFixture, session: { idle_minutes: 1 } },
    });
    const { result } = await openBench();

    expect(result.current.maintenance.idleMinutes).toBe(1);
  });

  it("counts the notifications the house is holding back", async () => {
    answer("GET", OVERVIEW, {
      status: 200,
      body: { ...overviewFixture, counts: { ...overviewFixture.counts, deferred: 4 } },
    });
    const { result } = await openBench();

    expect(result.current.quiet.held).toBe(4);
    act(() => result.current.quiet.onHeld());
    // The Held-back sheet is the Room's route, not the Workshop's.
    expect(onHeld).toHaveBeenCalledTimes(1);
  });

  it("does not poll: one read each, however long the bench is left open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = await openBench();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });

    // Five reads and a probe per integration, made when the bench opened and
    // again only when this client does something that may have moved them.
    expect(countOf("GET", SESSIONS)).toBe(1);
    expect(countOf("GET", CREDENTIALS)).toBe(1);
    expect(countOf("GET", INTEGRATIONS)).toBe(1);
    expect(countOf("GET", ATTENTION)).toBe(1);
    expect(countOf("GET", statusPath("weather"))).toBe(1);
    expect(countOf("GET", statusPath("home-service"))).toBe(1);
    // The overview is the exception, and it is not this bench's: `useOverview`
    // polls every 30 s for the Room and the Workshop header alike, and two
    // minutes is four of those — one shared poller, not a second one.
    expect(countOf("GET", OVERVIEW)).toBe(5);
    expect(result.current.error).toBeNull();
  });

  // ── Is any of this still true? ─────────────────────────────────────────────

  it("stamps when the overview landed, and calls itself live for it", async () => {
    const { result } = await openBench();

    expect(result.current.readAt).toBeGreaterThan(0);
    expect(result.current.online).toBe(true);
  });

  it("knows nothing yet rather than something stale before the first answer", async () => {
    hold("GET", OVERVIEW);
    const { result } = renderSystem();
    await settle();

    // `unknown since --:--` is not a time, and `not read yet` is a different
    // sentence from `unknown` — so the never-read case has no stamp at all.
    expect(result.current.readAt).toBeNull();
    expect(result.current.online).toBe(false);
  });

  it("stops calling itself live once two polls have gone by with no answer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = await openBench();
    const landed = result.current.readAt;

    // The server takes the connection and never answers. There is no timeout
    // anywhere in this tree, so nothing fails, nothing pauses, and the last
    // answer sits in the cache looking exactly like a fresh one.
    hold("GET", OVERVIEW);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STALE_AFTER_MS + 1_000);
    });

    expect(result.current.error).toBeNull();
    expect(result.current.overview).toBeDefined();
    expect(result.current.readAt).toBe(landed);
    expect(result.current.online).toBe(false);
  });

  // Two polls and not one: a single missed answer is the ordinary shape of a
  // slow request, and a bench that went `unknown` every time one poll ran long
  // would cry wolf on a house that is fine.
  it("gives the house two polls of silence before it stops believing the grid", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = await openBench();

    hold("GET", OVERVIEW);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS + 1_000);
    });
    expect(result.current.online).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS);
    });
    expect(result.current.online).toBe(false);
  });

  it("is live again on the next answer, and dates it to that answer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = await openBench();
    const landed = result.current.readAt;

    hold("GET", OVERVIEW);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STALE_AFTER_MS + 1_000);
    });
    expect(result.current.online).toBe(false);

    unhold("GET", OVERVIEW);
    await releaseNext("GET", OVERVIEW);
    await waitFor(() => expect(result.current.online).toBe(true));
    expect(result.current.readAt).toBeGreaterThan(landed ?? 0);
  });

  it("stops calling itself live when there is no network to poll on", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = await openBench();
    expect(result.current.online).toBe(true);

    // react-query's default `networkMode: "online"` **pauses** rather than
    // failing: the data is kept and the error stays null, which is precisely
    // why `error === null` cannot be what the stamp speaks for.
    onlineManager.setOnline(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS);
    });

    expect(result.current.error).toBeNull();
    expect(result.current.overview).toBeDefined();
    expect(result.current.online).toBe(false);
  });

  it("claims nothing about a bench nobody is looking at", async () => {
    const { result, rerender } = await openBench();
    expect(result.current.online).toBe(true);

    rerender({ on: false });

    expect(result.current.online).toBe(false);
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

  it("complains on the spine when the overview is the read that failed", async () => {
    answer("GET", OVERVIEW, { status: 503, body: { detail: "Redis unavailable" } });
    const { result } = renderSystem();

    await waitFor(() => expect(result.current.error).toBe("Redis unavailable"));
    expect(result.current.sessions.error).toBeNull();
  });

  it("says nothing at all once the bench is closed", async () => {
    for (const url of [SESSIONS, OVERVIEW, CREDENTIALS, INTEGRATIONS, ATTENTION]) {
      answer("GET", url, { status: 503, body: { detail: "Redis unavailable" } });
    }
    answer("POST", DRAIN, { status: 503, body: { detail: "Redis unavailable" } });
    const { result, rerender } = renderSystem();
    await waitFor(() => expect(result.current.sessions.error).toBe("Redis unavailable"));
    await waitFor(() => expect(result.current.credentials.error).toBe("Redis unavailable"));
    await waitFor(() => expect(result.current.integrations.error).toBe("Redis unavailable"));
    await waitFor(() => expect(result.current.attention.error).toBe("Redis unavailable"));
    act(() => result.current.maintenance.drain());
    await waitFor(() => expect(result.current.maintenance.drainError).toBe("Redis unavailable"));

    rerender({ on: false });

    // A bench nobody is looking at has no complaints to make.
    expect(result.current.error).toBeNull();
    expect(result.current.sessions.error).toBeNull();
    expect(result.current.credentials.error).toBeNull();
    expect(result.current.integrations.error).toBeNull();
    expect(result.current.attention.error).toBeNull();
    expect(result.current.quiet.error).toBeNull();
    expect(result.current.pairing.error).toBeNull();
    expect(result.current.maintenance.drainError).toBeNull();
    expect(result.current.maintenance.runError).toBeNull();
  });

  it("keeps what the bench learned across a trip to another bench", async () => {
    const client = makeClient();
    const { result, rerender } = await openBench(client);

    rerender({ on: false });
    rerender({ on: true });
    await settle();

    // The hook lives in the panel, not in the bench: the cache is still warm and
    // nothing is read a second time for the walk back.
    expect(result.current.sessions.list).toHaveLength(2);
    expect(reads()).toEqual(OPENING_READS);
  });
});
