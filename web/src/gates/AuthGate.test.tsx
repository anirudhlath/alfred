import { ConnectionProvider, markTrue } from "@/shell/ConnectionProvider";
/** Which sockets `connect()` was asked of, in order. */
const { socketConnects } = vi.hoisted(() => ({ socketConnects: [] as string[] }));
vi.mock("@/lib/chat-socket", () => ({
  ChatSocket: class {
    onstatus = () => {};
    connect() {
      socketConnects.push("chat");
    }
    close() {}
    listen() {
      return () => {};
    }
  },
}));
vi.mock("@/lib/telemetry-socket", () => ({
  TelemetrySocket: class {
    onstatus = () => {};
    connect() {
      socketConnects.push("telemetry");
    }
    close() {}
    subscribe() {}
    listen() {
      return () => {};
    }
  },
}));
import { onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, post } from "@/lib/api";
import { authEvents } from "@/lib/auth-events";
import { DEVICE_KEY } from "@/lib/auth";
import { hhmm } from "@/lib/format";
import type { AuthStatus } from "@/lib/types";
import { QUERY_DEFAULTS } from "@/shell/QueryProvider";
import { NETWORK_GATE_DETAIL, NETWORK_GATE_DETAIL_SIGNED_IN } from "@/test/fixtures";
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
/**
 * When set, the status read 500s: the credential store under the route failed,
 * and Starlette answers for it in plain text.
 */
let statusDown = false;
/** When set, the status read never reaches the house: `fetch` rejects, as it does offline. */
let statusUnreachable = false;
/** When set, the status read is in flight and stays there; see `holdRead`. */
let statusHeld = false;
/** When set, something other than the house answers the status read, with this. */
let statusReply: (() => Response) | null = null;
/**
 * When set, the house's network gate refuses this phone: registration answers
 * 403, in the house's words — with the guidance it adds for a signed-in session.
 */
let offNetwork = false;
/** When set, the house's session gate finds no session: the sessions read answers 401. */
let sessionLapsed = false;
/** Reads the test is holding in flight, each waiting for `answerHeld`. */
const held: Array<() => void> = [];
/** Disconnected after each test; see `watchFor`. */
const observers: MutationObserver[] = [];

/**
 * A read that stays in flight until the test answers it, or its signal aborts
 * it — and then it rejects with the signal's reason, as a browser's fetch does.
 */
function holdRead(signal: AbortSignal | null | undefined): Promise<Response> {
  return new Promise<Response>((resolve, reject) => {
    signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
    held.push(() => resolve(new Response(JSON.stringify(status), { status: 200 })));
  });
}

/** Lets every held read land, with the status as it stands now. */
function answerHeld(): void {
  for (const answer of held.splice(0)) answer();
}

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/status") {
        if (statusUnreachable) throw new TypeError("Failed to fetch");
        if (statusHeld) return holdRead(init?.signal);
        if (statusReply) return statusReply();
        if (statusDown) return new Response("Internal Server Error", { status: 500 });
        return new Response(JSON.stringify(status), { status: 200 });
      }
      if (offNetwork && url.startsWith("/api/auth/register/")) {
        const detail = status.authenticated ? NETWORK_GATE_DETAIL_SIGNED_IN : NETWORK_GATE_DETAIL;
        return new Response(JSON.stringify({ detail }), { status: 403 });
      }
      if (sessionLapsed && url === "/api/auth/sessions") {
        return new Response('{"detail":"Authentication required"}', { status: 401 });
      }
      if (url === "/api/integrations") return new Response("[]", { status: 200 });
      if (url === "/api/admin/attention") return new Response('{"domains":[]}', { status: 200 });
      return new Response("{}", { status: 200 });
    }),
  );
}

/** How many times the status has been asked for, answered or not. */
const statusReads = (): number =>
  vi.mocked(fetch).mock.calls.filter(([input]) => String(input) === "/api/auth/status").length;

/**
 * A client on the app's own policy, for the cases without a network: what they
 * model is a read that fails, is retried once and gives up, and `retry: false`
 * gives up a step early. Only the backoff is the harness's, as in
 * `useTriggers.test.tsx`.
 */
const appClient = (): QueryClient =>
  new QueryClient({
    defaultOptions: { ...QUERY_DEFAULTS, queries: { ...QUERY_DEFAULTS.queries, retryDelay: 0 } },
  });

function renderGate(
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
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
  statusUnreachable = false;
  statusHeld = false;
  statusReply = null;
  offNetwork = false;
  sessionLapsed = false;
  held.length = 0;
  socketConnects.length = 0;
  loginPasskeyMock.mockReset().mockResolvedValue(undefined);
  registerPasskeyMock.mockReset().mockResolvedValue(undefined);
  vi.stubGlobal("location", { hostname: "alfred.example.com", pathname: "/" });
  stubFetch();
  // The setup gate greets by the hour; pin it so "Good evening" holds on CI's clock too.
  vi.spyOn(Date.prototype, "getHours").mockReturnValue(21);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  // react-query's online flag is module state, and an `offline` event a test
  // sent would otherwise pause every read in the next one.
  onlineManager.setOnline(true);
  for (const observer of observers.splice(0)) observer.disconnect();
});

/**
 * What a browser does when the phone's network changes: the flag first, then
 * the event, which reaches react-query's real `onlineManager` as well as the
 * gate. `act` is synchronous on purpose: React renders the change before
 * react-query's own reconnect refetch has started, the order in which a gate
 * that trusted the read's last error would say "signed out" for a frame.
 */
function phoneGoes(state: "online" | "offline"): void {
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(state === "online");
  act(() => void window.dispatchEvent(new Event(state)));
}

/**
 * A launch with no network: the flag says so, the read cannot reach the house,
 * and no event has fired — so react-query's `onlineManager`, which only ever
 * hears events, believes it is online.
 */
function launchOffline(): void {
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
  statusUnreachable = true;
}

/**
 * Counts every heading with this title that reaches the DOM from now on,
 * however briefly: one committed and replaced before the next query would
 * never be seen by `queryByRole`.
 */
function watchFor(title: string): () => number {
  let seen = 0;
  const count = (records: MutationRecord[]) => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (!(node instanceof Element)) continue;
        const headings = node.matches("h1") ? [node] : [...node.querySelectorAll("h1")];
        seen += headings.filter((h) => h.textContent === title).length;
      }
    }
  };
  const observer = new MutationObserver(count);
  observer.observe(document.body, { childList: true, subtree: true });
  observers.push(observer);
  return () => {
    count(observer.takeRecords());
    return seen;
  };
}

/** Every frame of the sign-in gate from now on; see `watchFor`. */
const watchForSignIn = () => watchFor("Welcome back, sir.");

/**
 * The auth read's deadlines, in the test's hands: each read's
 * `AbortSignal.timeout` becomes a signal the test fires.
 */
function holdDeadlines(): AbortController[] {
  const deadlines: AbortController[] = [];
  vi.spyOn(AbortSignal, "timeout").mockImplementation(() => {
    const deadline = new AbortController();
    deadlines.push(deadline);
    return deadline.signal;
  });
  return deadlines;
}

const UNANSWERED = "The house is not answering.";

/** Give react-query's batched notifications (a `setTimeout(0)`) their turn. */
const tick = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));

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
      throw new ApiError(403, NETWORK_GATE_DETAIL);
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
    expect(screen.queryByText(NETWORK_GATE_DETAIL)).toBeNull();
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

  it("fails closed, to a gate that claims no session, when the status read errors", async () => {
    // The house's own 500: its credential store failed, so it could not say
    // who you are. Mounting the room would show an empty house as the truth,
    // and the sign-in gate would say "signed out", which the house never said.
    // Nothing but the house's 200 reaches either; see "AuthGate when the house
    // does not answer" below.
    statusDown = true;
    renderGate();
    expect(await screen.findByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
    expect(screen.queryByText("the room")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Welcome back, sir." })).toBeNull();
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

  it("keeps the room, under neither event gate, when a background read of the status meets a 403", async () => {
    // No network gate guards the status read, so its 403 is not the house
    // refusing this network, nor news of the session: a failed refetch, which
    // keeps the last answer.
    const { client } = renderGate();
    await screen.findByText("the room");
    const denials = watchFor("Not from here.");
    const lapses = watchFor("Your session lapsed.");

    statusReply = () => new Response('{"detail":"Forbidden"}', { status: 403 });
    await act(() => client.invalidateQueries({ queryKey: ["auth-status"] }));
    await waitFor(() => expect(client.getQueryState(["auth-status"])?.status).toBe("error"));
    await tick();

    expect(screen.getByText("the room")).toBeInTheDocument();
    expect(denials()).toBe(0);
    expect(lapses()).toBe(0);
  });

  it("keeps the room, under neither event gate, when a background read of the status meets a 401", async () => {
    // The route answers "not authenticated" with a 200, never a 401, so its 401
    // is not the house saying the session lapsed, nor news of the network: a
    // failed refetch, as above. A lapse the house does see arrives as that 200,
    // or as another read's 401.
    const { client } = renderGate();
    await screen.findByText("the room");
    const denials = watchFor("Not from here.");
    const lapses = watchFor("Your session lapsed.");

    statusReply = () => new Response('{"detail":"Unauthorized"}', { status: 401 });
    await act(() => client.invalidateQueries({ queryKey: ["auth-status"] }));
    await waitFor(() => expect(client.getQueryState(["auth-status"])?.status).toBe("error"));
    await tick();

    expect(screen.getByText("the room")).toBeInTheDocument();
    expect(denials()).toBe(0);
    expect(lapses()).toBe(0);
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

  it("reopens both sockets once signed in: a 4001 is never retried on its own", async () => {
    const user = userEvent.setup();
    status = { registered: true, authenticated: false };
    renderGate();
    await screen.findByRole("heading", { name: "Welcome back, sir." });
    expect(socketConnects).toEqual(["chat", "telemetry"]); // the mount, and nothing since
    socketConnects.length = 0;

    status = { registered: true, authenticated: true };
    await user.click(screen.getByRole("button", { name: "Sign in with Face ID" }));

    await screen.findByText("the room");
    expect(socketConnects).toEqual(["chat", "telemetry"]);
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

    // The house answered at 21:15; the gate says so, since what is behind it dates from then.
    const at = new Date(2026, 8, 8, 21, 15);
    act(() => markTrue(at));
    act(() => authEvents.emit("denied"));

    expect(screen.getByRole("heading", { name: "Not from here." })).toBeInTheDocument();
    expect(screen.getByText("403 · off-network")).toBeInTheDocument();
    expect(
      screen.getByText(`Last true ${hhmm(at)} · everything shown behind this is last-known`),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign in with Face ID" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Back to the room" }));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Not from here." })).toBeNull(),
    );
    expect(screen.getByText("the room")).toBeInTheDocument();
  });

  it("raises the denied gate for a 403 from a network-gated route, as api() announces it", async () => {
    // The other half of the status read's exemption (its 403, in "AuthGate when
    // the house does not answer"): registration is network-gated, so its 403 is
    // the house's own refusal of this network.
    offNetwork = true;
    renderGate();
    await screen.findByText("the room");

    await act(() => post("/api/auth/register/begin", {}).catch(() => undefined));

    expect(await screen.findByRole("heading", { name: "Not from here." })).toBeInTheDocument();
  });

  it("raises the lapsed-session gate for a 401 from a session-gated read, as api() announces it", async () => {
    // The other half of the status read's exemption for its 401 (in "AuthGate
    // routing"): the house's session gate sends this one, so the session has
    // really lapsed — and the gate rises over the room, which stays beneath it.
    sessionLapsed = true;
    renderGate();
    await screen.findByText("the room");

    await act(() => api("/api/auth/sessions").catch(() => undefined));

    expect(
      await screen.findByRole("heading", { name: "Your session lapsed." }),
    ).toBeInTheDocument();
    expect(screen.getByText("the room")).toBeInTheDocument();
  });
});

describe("AuthGate with no network", () => {
  it("says the phone is offline, not signed out, on a launch with no network", async () => {
    launchOffline();
    const signIns = watchForSignIn();
    const { client } = renderGate(appClient());

    // From the first render: nothing has landed, and the phone says why.
    expect(screen.getByRole("heading", { name: "No way to the house." })).toBeInTheDocument();

    // Not paused: react-query heard no `offline` event, so the read runs,
    // fails, is retried once and gives up — and the gate must not move.
    await waitFor(() => expect(client.getQueryState(["auth-status"])?.status).toBe("error"));
    await tick();
    expect(screen.getByRole("heading", { name: "No way to the house." })).toBeInTheDocument();
    expect(signIns()).toBe(0);
    // The flag explains the read; it never stopped either attempt going out.
    expect(statusReads()).toBe(2);
  });

  it("shows the bare field, not the offline gate, while a read is merely slow", () => {
    // onLine true and pending means the request is in flight. Saying "no
    // connection" there would be a guess, and the gate's whole point is that it
    // does not guess.
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    statusHeld = true;
    const { container } = renderGate(appClient());
    expect(container.querySelector(".gate-field")).not.toBeNull();
    expect(screen.queryByText("No way to the house.")).toBeNull();
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("comes back without a sign-in frame: the field, then the room", async () => {
    launchOffline();
    const { client } = renderGate(appClient());
    await waitFor(() => expect(client.getQueryState(["auth-status"])?.status).toBe("error"));
    const signIns = watchForSignIn();
    const before = statusReads();

    statusUnreachable = false;
    phoneGoes("online");

    // The render that heard the event. The read's last error is still the
    // offline one, so it says nothing, and the read is asked again.
    expect(screen.queryByRole("heading")).toBeNull();
    expect(document.querySelector(".gate-field")).not.toBeNull();

    expect(await screen.findByText("the room")).toBeInTheDocument();
    expect(signIns()).toBe(0);
    // Asked once more, by the gate: react-query never heard the phone go
    // offline, so its own refetch-on-reconnect never fires.
    expect(statusReads() - before).toBe(1);
  });

  it("says the house is not answering, as an online launch does, once a read with the signal back fails", async () => {
    launchOffline();
    const { client } = renderGate(appClient());
    await waitFor(() => expect(client.getQueryState(["auth-status"])?.status).toBe("error"));
    const signIns = watchForSignIn();
    const before = statusReads();

    phoneGoes("online"); // and the house still does not answer

    // The field while it asks, as on any return: the offline failure is not news.
    expect(screen.queryByRole("heading")).toBeNull();
    expect(await screen.findByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
    expect(statusReads() - before).toBe(2); // asked again, and retried once
    expect(signIns()).toBe(0);
  });

  it("goes offline over a failed read and back again without a sign-in frame", async () => {
    // Online, but the house did not answer.
    statusUnreachable = true;
    const signInsAtAll = watchForSignIn();
    renderGate(appClient());
    expect(await screen.findByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
    expect(signInsAtAll()).toBe(0);

    phoneGoes("offline"); // an event react-query hears, this time
    expect(screen.getByRole("heading", { name: "No way to the house." })).toBeInTheDocument();
    const signIns = watchForSignIn();
    const before = statusReads();

    statusUnreachable = false;
    phoneGoes("online");

    expect(screen.queryByRole("heading")).toBeNull();
    expect(await screen.findByText("the room")).toBeInTheDocument();
    expect(signIns()).toBe(0);
    // react-query's reconnect refetch and the gate's are the same read.
    expect(statusReads() - before).toBe(1);
  });

  it("leaves the room alone when the phone goes offline", async () => {
    // The gate is for a read that has not landed. Once one has, the room is the
    // truth as last read, and its own offline note says the rest.
    renderGate(appClient());
    await screen.findByText("the room");

    phoneGoes("offline");

    expect(screen.getByText("the room")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "No way to the house." })).toBeNull();
  });

  it("keeps a signed-out answer when the phone goes offline", async () => {
    // The house was asked and said so. "I cannot ask the house who you are"
    // would be false here; the sign-in gate is the answer as last read.
    status = { registered: true, authenticated: false };
    renderGate(appClient());
    await screen.findByRole("heading", { name: "Welcome back, sir." });

    phoneGoes("offline");

    expect(screen.getByRole("heading", { name: "Welcome back, sir." })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "No way to the house." })).toBeNull();
  });

  it("lets a read that lands anyway straight through, whatever the phone says, and keeps it", async () => {
    // navigator.onLine is a guess, and it never stops the read going out.
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const { client } = renderGate(appClient());
    expect(screen.getByRole("heading", { name: "No way to the house." })).toBeInTheDocument();

    expect(await screen.findByText("the room")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "No way to the house." })).toBeNull();

    // Landed is landed: a later read that fails, with the flag still saying
    // offline, keeps the room, as a failed refetch does anywhere else.
    statusUnreachable = true;
    await act(() => client.invalidateQueries({ queryKey: ["auth-status"] }));
    await waitFor(() => expect(client.getQueryState(["auth-status"])?.status).toBe("error"));
    await tick();
    expect(screen.getByText("the room")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "No way to the house." })).toBeNull();
  });
});

describe("AuthGate when the house does not answer", () => {
  // What the house can say to this read (core/identity/auth_routes.py
  // `auth_status`): 200 with both booleans, or 500 when its credential store
  // fails. The route has no dependencies, and AuthCookieMiddleware turns a
  // failed session lookup into "not authenticated", not an error. So no 4xx is
  // ever the house's answer here, and nothing but its 200 says who you are.
  // Every other outcome is the same news, whoever brings it: no answer about
  // the session.

  it("says so when the read cannot reach the house, never signed out", async () => {
    statusUnreachable = true;
    const signIns = watchForSignIn();
    const { container } = renderGate(appClient());
    // The first read and its one retry: nothing true to say yet.
    expect(container.querySelector(".gate-field")).not.toBeNull();
    expect(screen.queryByRole("heading")).toBeNull();

    expect(await screen.findByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
    expect(screen.getByText("alfred.example.com · unknown since --:--")).toBeInTheDocument();
    expect(statusReads()).toBe(2);
    expect(signIns()).toBe(0);
  });

  it("gives up at the deadline on a read that never comes back, without a retry", async () => {
    const deadlines = holdDeadlines();
    statusHeld = true;
    const signIns = watchForSignIn();
    const { container } = renderGate(appClient());
    await waitFor(() => expect(deadlines).toHaveLength(1));
    expect(container.querySelector(".gate-field")).not.toBeNull();
    expect(screen.queryByRole("heading")).toBeNull();

    act(() => deadlines[0].abort(new DOMException("signal timed out", "TimeoutError")));

    expect(await screen.findByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
    // Out of time once is out of time: a retry would only double the wait.
    expect(statusReads()).toBe(1);
    expect(signIns()).toBe(0);
  });

  it("hands the read react-query's signal, so a cancel reaches the request too", async () => {
    statusHeld = true;
    const { unmount } = renderGate(appClient());
    await waitFor(() => expect(statusReads()).toBe(1));
    const [, init] =
      vi.mocked(fetch).mock.calls.find(([input]) => String(input) === "/api/auth/status") ?? [];
    expect(init?.signal?.aborted).toBe(false);

    // With nothing left watching it, react-query cancels a read that took its signal.
    unmount();

    expect(init?.signal?.aborted).toBe(true);
  });

  it("says so when a proxy answers for the house with a 502", async () => {
    statusReply = () =>
      new Response(
        "<html><head><title>502 Bad Gateway</title></head><body><center><h1>502 Bad Gateway</h1></center></body></html>",
        { status: 502, statusText: "Bad Gateway", headers: { "Content-Type": "text/html" } },
      );
    const signIns = watchForSignIn();
    renderGate(appClient());

    expect(await screen.findByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
    expect(statusReads()).toBe(2); // a 5xx is retried once, as on every read
    expect(signIns()).toBe(0);
  });

  it("says so when a 200 is not the house's status: a captive portal's page", async () => {
    statusReply = () =>
      new Response("<!doctype html><title>Sign in to this Wi-Fi</title>", {
        status: 200,
        headers: { "Content-Type": "text/html" },
      });
    const signIns = watchForSignIn();
    renderGate(appClient());

    expect(await screen.findByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
    expect(statusReads()).toBe(2);
    expect(signIns()).toBe(0);
  });

  it("does not read a 200 of someone else's JSON as a house with no passkey", async () => {
    // Taken for the status, `{"ok":true}` has no `registered`, and the setup
    // gate would offer to make this phone the house's only key.
    statusReply = () => new Response('{"ok":true}', { status: 200 });
    const setups = watchFor("Good evening. I am Alfred.");
    const signIns = watchForSignIn();
    renderGate(appClient());

    expect(await screen.findByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
    expect(setups()).toBe(0);
    expect(signIns()).toBe(0);
  });

  it("shows the unanswered gate alone for a 403, which this route never sends", async () => {
    // Never the house's on this route (see above): no network gate guards it,
    // so the 403 came from something in front of the house — here a Cloudflare
    // challenge, which a fetch cannot pass. The denied gate's "not from here"
    // would be a claim nobody made, so `api()` does not announce this one; a
    // network-gated route's 403 still raises it (see "AuthGate events").
    statusReply = () =>
      new Response("<!DOCTYPE html><title>Just a moment...</title>", {
        status: 403,
        headers: { "Content-Type": "text/html", "cf-mitigated": "challenge" },
      });
    const denials = watchFor("Not from here.");
    const signIns = watchForSignIn();
    renderGate(appClient());

    expect(await screen.findByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
    await tick();
    expect(denials()).toBe(0);
    expect(statusReads()).toBe(1); // a 4xx is never retried
    expect(signIns()).toBe(0);
  });

  it("shows the unanswered gate alone for a 401, and claims no session", async () => {
    // Never the house's on this route either (see above): it answers "not
    // authenticated" with a 200. `api()` announces this 401 neither as a lapsed
    // session nor as a refused network, and with nothing read there was no
    // session to lapse.
    statusReply = () => new Response('{"detail":"Unauthorized"}', { status: 401 });
    const lapses = watchFor("Your session lapsed.");
    const denials = watchFor("Not from here.");
    const signIns = watchForSignIn();
    renderGate(appClient());

    expect(await screen.findByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
    await tick();
    expect(lapses()).toBe(0);
    expect(denials()).toBe(0);
    expect(statusReads()).toBe(1);
    expect(signIns()).toBe(0);
  });

  it("tries again on the button, holding it while it asks, and lets the answer through", async () => {
    const user = userEvent.setup();
    statusUnreachable = true;
    const signIns = watchForSignIn();
    renderGate(appClient());
    const tryAgain = await screen.findByRole("button", { name: "Try again" });
    expect(tryAgain).not.toHaveAttribute("aria-disabled");
    const before = statusReads();

    statusUnreachable = false;
    statusHeld = true;
    await user.click(tryAgain);

    // One read, in flight: the gate stays up, and its button says it is asking.
    await waitFor(() => expect(tryAgain).toHaveAttribute("aria-disabled", "true"));
    expect(tryAgain).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
    await user.click(tryAgain);
    expect(statusReads() - before).toBe(1);

    act(() => answerHeld());

    expect(await screen.findByText("the room")).toBeInTheDocument();
    expect(signIns()).toBe(0);
  });

  it("gives the button back when the house still does not answer", async () => {
    const user = userEvent.setup();
    statusUnreachable = true;
    renderGate(appClient());
    const tryAgain = await screen.findByRole("button", { name: "Try again" });
    const before = statusReads();

    await user.click(tryAgain);

    await waitFor(() => expect(statusReads() - before).toBe(2)); // asked, and retried once
    await waitFor(() => expect(tryAgain).not.toHaveAttribute("aria-disabled"));
    expect(screen.getByRole("heading", { name: UNANSWERED })).toBeInTheDocument();
  });

  it("shows the sign-in gate once the house answers that you are signed out", async () => {
    // The one way to it now: the house's own answer.
    const user = userEvent.setup();
    statusUnreachable = true;
    renderGate(appClient());
    const tryAgain = await screen.findByRole("button", { name: "Try again" });

    statusUnreachable = false;
    status = { registered: true, authenticated: false };
    await user.click(tryAgain);

    expect(await screen.findByRole("heading", { name: "Welcome back, sir." })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: UNANSWERED })).toBeNull();
  });

  it("asks again when the app comes back to the foreground, once", async () => {
    statusUnreachable = true;
    renderGate(appClient());
    await screen.findByRole("heading", { name: UNANSWERED });
    const before = statusReads();
    statusUnreachable = false;

    // As a browser sends it: it bubbles to the window, where react-query's focus
    // refetch hears it too. With nothing read, the two are one request.
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    act(() => void document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));

    expect(await screen.findByText("the room")).toBeInTheDocument();
    expect(statusReads() - before).toBe(1);
  });

  it("asks again on a return from the back-forward cache, which react-query does not hear", async () => {
    statusUnreachable = true;
    renderGate(appClient());
    await screen.findByRole("heading", { name: UNANSWERED });
    const before = statusReads();
    statusUnreachable = false;

    act(() => void window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));

    expect(await screen.findByText("the room")).toBeInTheDocument();
    expect(statusReads() - before).toBe(1);
  });

  it("does not ask on a foreground while the offline gate is up", async () => {
    // With no connection a request can only fail the way the read already did
    // (deviation 5); the offline gate asks again once there is a signal.
    launchOffline();
    const { client } = renderGate(appClient());
    await waitFor(() => expect(client.getQueryState(["auth-status"])?.status).toBe("error"));
    const before = statusReads();

    act(() => void window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
    await tick();

    expect(screen.getByRole("heading", { name: "No way to the house." })).toBeInTheDocument();
    expect(statusReads() - before).toBe(0);
  });

  it("does not ask on a foreground once the house has answered", async () => {
    renderGate(appClient());
    await screen.findByText("the room");
    const before = statusReads();

    act(() => void window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
    await tick();

    expect(statusReads() - before).toBe(0);
  });
});
