import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, timedOut } from "./api";
import {
  DEVICES_PATH,
  fetchPublicKey,
  forgetPush,
  onOpenPath,
  permission,
  PUSH_KEY_PATH,
  reachContext,
  REQUEST_TIMEOUT_MS,
  requestAndSubscribe,
  syncSubscription,
} from "./push";
import { bufToB64url } from "./webauthn";

// jsdom has none of the push APIs. `navigator.serviceWorker` is defined per test
// and deleted afterwards, as in sw.test.ts — `vi.stubGlobal` would have to
// replace the whole `navigator`. `PushManager`, `Notification`, `matchMedia` and
// `fetch` are window globals, which `vi.stubGlobal` stands in for.

/** What the browser and the house were asked to do, in the order they were asked. */
const log: string[] = [];

/** A P-256 public key's shape: 65 bytes, the first 0x04. Only the bytes matter here. */
function point(fill: number): Uint8Array {
  const bytes = new Uint8Array(65).fill(fill);
  bytes[0] = 4;
  return bytes;
}

const HOUSE_BYTES = point(7);
const HOUSE_KEY = bufToB64url(HOUSE_BYTES.slice().buffer);
const OLD_BYTES = point(9);

const HELD = "https://fcm.googleapis.com/fcm/send/fake-held";
const FRESH = "https://fcm.googleapis.com/fcm/send/fake-fresh";

const sameBytes = (held: ArrayBuffer, want: Uint8Array) =>
  new Uint8Array(held).length === want.length &&
  new Uint8Array(held).every((byte, i) => byte === want[i]);

class FakeSubscription {
  readonly endpoint: string;
  readonly options: { userVisibleOnly: boolean; applicationServerKey: ArrayBuffer | null };
  /** The manager holding it, which unsubscribing leaves empty. */
  manager: FakePushManager | null = null;
  /** Whether unsubscribing fails. */
  refuses = false;
  readonly unsubscribe = vi.fn(() => {
    log.push(`unsubscribe ${this.endpoint}`);
    if (this.refuses) return Promise.reject(new Error("unsubscribe failed"));
    if (this.manager?.subscription === this) this.manager.subscription = null;
    return Promise.resolve(true);
  });

  constructor(endpoint: string, key: Uint8Array | null) {
    this.endpoint = endpoint;
    this.options = { userVisibleOnly: true, applicationServerKey: key && key.slice().buffer };
  }

  toJSON() {
    return {
      endpoint: this.endpoint,
      expirationTime: null,
      keys: { p256dh: "fake-p256dh", auth: "fake-auth" },
    };
  }
}

/**
 * A PushManager as strict as a browser's: subscribing under one key while a
 * subscription under another, or under none it reports, is held is an
 * InvalidStateError, and subscribing under the held one hands that subscription
 * back.
 */
class FakePushManager {
  subscription: FakeSubscription | null = null;
  readonly getSubscription = vi.fn(() => Promise.resolve(this.subscription));
  readonly subscribe = vi.fn((options: PushSubscriptionOptionsInit) => {
    log.push("subscribe");
    const key = new Uint8Array(options.applicationServerKey as ArrayBuffer);
    const current = this.subscription;
    if (current) {
      const heldKey = current.options.applicationServerKey;
      if (!heldKey || !sameBytes(heldKey, key)) {
        const message = "A subscription with a different applicationServerKey already exists.";
        return Promise.reject(new DOMException(message, "InvalidStateError"));
      }
      return Promise.resolve(current);
    }
    return Promise.resolve(this.hold(new FakeSubscription(FRESH, key)));
  });

  constructor(held: FakeSubscription | null) {
    if (held) this.hold(held);
  }

  hold(subscription: FakeSubscription): FakeSubscription {
    subscription.manager = this;
    this.subscription = subscription;
    return subscription;
  }
}

/**
 * A browser's service worker container. `ready: false` is what `ready` is with
 * no worker registered — dev, always — a promise that never settles. `ready` is
 * a getter that counts its reads, so a test can show it was never even looked at.
 */
function registry(
  options: { held?: FakeSubscription | null; ready?: boolean; registered?: boolean } = {},
) {
  const { held = null, ready = true, registered = true } = options;
  const pushManager = new FakePushManager(held);
  const registration = { pushManager };
  const whenReady = ready ? Promise.resolve(registration) : new Promise<never>(() => {});
  let readyReads = 0;
  const container = Object.assign(new EventTarget(), {
    getRegistration: vi.fn(() => Promise.resolve(registered ? registration : undefined)),
  });
  Object.defineProperty(container, "ready", {
    get: () => {
      readyReads += 1;
      return whenReady;
    },
  });
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: container });
  return { pushManager, container, readyReads: () => readyReads };
}

const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

type Answer = (init?: RequestInit) => Promise<Response>;

/**
 * The house: its key (null when Web Push is not configured), and how the key
 * read, a DELETE and a POST end. `onKey`, when given, answers the key read.
 */
function stubHouse(
  options: { key?: string | null; onKey?: Answer; onDelete?: Answer; onPost?: Answer } = {},
) {
  const ok: Answer = async () => reply(200, { status: "ok" });
  const { key = HOUSE_KEY, onKey, onDelete = ok, onPost = ok } = options;
  const mock = vi.fn<typeof fetch>(async (input, init) => {
    const method = init?.method ?? "GET";
    log.push(`${method} ${String(input)}`);
    if (String(input) === PUSH_KEY_PATH) {
      if (onKey) return onKey(init);
      return key === null
        ? reply(404, { detail: "Web Push not configured" })
        : reply(200, { public_key: key });
    }
    return method === "DELETE" ? onDelete(init) : onPost(init);
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

/** A request that never answers and fails only when its signal aborts, as fetch does. */
const stalled: Answer = (init) =>
  new Promise((_, reject) => {
    init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
  });

/**
 * `AbortSignal.timeout` on the faked clock. jsdom's runs on a timer vitest's fake
 * timers do not reach, so a test advancing the clock would never see it fire.
 */
function fakeDeadlines(): void {
  vi.spyOn(AbortSignal, "timeout").mockImplementation((ms) => {
    const deadline = new AbortController();
    setTimeout(() => deadline.abort(new DOMException("signal timed out", "TimeoutError")), ms);
    return deadline.signal;
  });
}

/**
 * Holds the next `subscribe()` until the returned function is called, then answers
 * it as the fake would have: a sync caught between asking the browser and
 * registering with the house.
 */
function holdSubscribe(pushManager: FakePushManager): () => void {
  const subscribe = pushManager.subscribe.getMockImplementation()!;
  let release: () => void = () => {};
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  pushManager.subscribe.mockImplementationOnce((options) => released.then(() => subscribe(options)));
  return release;
}

/** A POST held in flight until the returned function answers it, which the log records. */
function holdPost(): { onPost: Answer; answer: () => void } {
  let answer: () => void = () => {};
  const onPost: Answer = () =>
    new Promise((resolve) => {
      answer = () => {
        log.push("POST answered");
        resolve(reply(200, { status: "ok" }));
      };
    });
  return { onPost, answer: () => answer() };
}

/** Lets every queued microtask and the timers already due run, with real timers. */
const drain = () => new Promise((resolve) => setTimeout(resolve, 0));

/** What each request asked for, in order. */
function requests(mock: ReturnType<typeof stubHouse>) {
  return mock.mock.calls.map(([input, init]) => ({
    method: init?.method ?? "GET",
    path: String(input),
    body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
  }));
}

/** The request that registers `subscription` with the house. */
function registering(subscription: FakeSubscription) {
  return {
    method: "POST",
    path: DEVICES_PATH,
    body: { platform: "web_push", subscription: subscription.toJSON() },
  };
}

function stubNotification(
  answer: NotificationPermission = "granted",
  current: NotificationPermission = "default",
) {
  const requestPermission = vi.fn(() => {
    log.push("requestPermission");
    return Promise.resolve(answer);
  });
  vi.stubGlobal("Notification", { permission: current, requestPermission });
  return requestPermission;
}

/** The browser says the app is on the Home Screen. */
function standalone(matches: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: matches && query === "(display-mode: standalone)",
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

/** A browser with every push API, so a test takes away only what it names. */
function capableBrowser(): void {
  registry();
  vi.stubGlobal("PushManager", class {});
  stubNotification();
}

afterEach(() => {
  Reflect.deleteProperty(navigator, "serviceWorker");
  log.length = 0;
  vi.useRealTimers();
});

describe("reachContext", () => {
  it("is a tab outside standalone", () => {
    capableBrowser();
    standalone(false);
    expect(reachContext(true)).toBe("tab");
  });

  it("is unsupported in standalone without PushManager", () => {
    registry();
    stubNotification();
    standalone(true);
    expect(reachContext(true)).toBe("unsupported");
    vi.stubGlobal("PushManager", class {});
    expect(reachContext(true)).toBe("capable");
  });

  it("is unsupported in standalone without a service worker or Notification", () => {
    vi.stubGlobal("PushManager", class {});
    standalone(true);
    stubNotification();
    expect(reachContext(true)).toBe("unsupported");
    vi.unstubAllGlobals();
    vi.stubGlobal("PushManager", class {});
    standalone(true);
    registry();
    expect(reachContext(true)).toBe("unsupported");
  });

  it("is unsupported in dev", () => {
    capableBrowser();
    standalone(true);
    expect(reachContext(false)).toBe("unsupported");
  });
});

describe("permission", () => {
  it("is the browser's answer, and default where there is no Notification", () => {
    expect(permission()).toBe("default");
    stubNotification("granted", "denied");
    expect(permission()).toBe("denied");
  });
});

describe("fetchPublicKey", () => {
  it("reads a 404 key as unconfigured", async () => {
    stubHouse({ key: null });
    await expect(fetchPublicKey()).resolves.toBeNull();
  });

  it("hands back the house key, and rethrows anything but a 404", async () => {
    stubHouse();
    await expect(fetchPublicKey()).resolves.toBe(HOUSE_KEY);
    vi.stubGlobal("fetch", vi.fn(async () => reply(502, { detail: "Bad Gateway" })));
    await expect(fetchPublicKey()).rejects.toMatchObject({ status: 502 });
  });

  it("gives up on a key read that never answers, with a deadline's name", async () => {
    vi.useFakeTimers();
    fakeDeadlines();
    stubHouse({ onKey: stalled });
    let settled = false;
    const reading = fetchPublicKey().catch((caught: unknown) => {
      settled = true;
      return caught;
    });
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 1);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const error = await reading;
    // By name, which is all `timedOut` — and so the retry policy — reads.
    expect(error).toMatchObject({ name: "TimeoutError" });
    expect(timedOut(error)).toBe(true);
  });
});

describe("syncSubscription", () => {
  it("subscribes with the house key and registers", async () => {
    const { pushManager, readyReads } = registry();
    const fetchMock = stubHouse();
    await syncSubscription(HOUSE_KEY);
    expect(readyReads()).toBe(1);
    expect(pushManager.subscribe).toHaveBeenCalledTimes(1);
    const [options] = pushManager.subscribe.mock.calls[0];
    expect(options.userVisibleOnly).toBe(true);
    expect(new Uint8Array(options.applicationServerKey as ArrayBuffer)).toEqual(HOUSE_BYTES);
    expect(requests(fetchMock)).toEqual([registering(pushManager.subscription!)]);
  });

  it("re-registers a subscription it already holds", async () => {
    const held = new FakeSubscription(HELD, HOUSE_BYTES);
    const { pushManager } = registry({ held });
    const fetchMock = stubHouse();
    await syncSubscription(HOUSE_KEY);
    expect(pushManager.subscribe).not.toHaveBeenCalled();
    expect(held.unsubscribe).not.toHaveBeenCalled();
    expect(requests(fetchMock)).toEqual([registering(held)]);
  });

  it("keeps a subscription that names no key, as it is", async () => {
    const held = new FakeSubscription(HELD, null);
    const { pushManager } = registry({ held });
    const fetchMock = stubHouse();
    await syncSubscription(HOUSE_KEY);
    expect(pushManager.subscribe).not.toHaveBeenCalled();
    expect(held.unsubscribe).not.toHaveBeenCalled();
    expect(requests(fetchMock)).toEqual([registering(held)]);
  });

  it("resubscribes when the house key changed", async () => {
    const held = new FakeSubscription(HELD, OLD_BYTES);
    const { pushManager } = registry({ held });
    const fetchMock = stubHouse();
    await syncSubscription(HOUSE_KEY);
    expect(log).toEqual([
      `DELETE ${DEVICES_PATH}`,
      `unsubscribe ${HELD}`,
      "subscribe",
      `POST ${DEVICES_PATH}`,
    ]);
    expect(pushManager.subscription!.endpoint).toBe(FRESH);
    expect(requests(fetchMock)).toEqual([
      { method: "DELETE", path: DEVICES_PATH, body: { endpoint: HELD } },
      registering(pushManager.subscription!),
    ]);
  });

  it("resubscribes even when the old row's DELETE fails", async () => {
    const held = new FakeSubscription(HELD, OLD_BYTES);
    registry({ held });
    stubHouse({ onDelete: async () => reply(503, { detail: "Device store busy" }) });
    await expect(syncSubscription(HOUSE_KEY)).resolves.toBeUndefined();
    expect(log).toEqual([
      `DELETE ${DEVICES_PATH}`,
      `unsubscribe ${HELD}`,
      "subscribe",
      `POST ${DEVICES_PATH}`,
    ]);
  });

  it("resubscribes once the old row's DELETE runs out of time", async () => {
    vi.useFakeTimers();
    fakeDeadlines();
    const held = new FakeSubscription(HELD, OLD_BYTES);
    registry({ held });
    stubHouse({ onDelete: stalled });
    const syncing = syncSubscription(HOUSE_KEY);
    await vi.advanceTimersByTimeAsync(4_999);
    expect(held.unsubscribe).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await expect(syncing).resolves.toBeUndefined();
    expect(log).toEqual([
      `DELETE ${DEVICES_PATH}`,
      `unsubscribe ${HELD}`,
      "subscribe",
      `POST ${DEVICES_PATH}`,
    ]);
  });

  it("hands the house's busy store back to the caller, to retry", async () => {
    registry();
    stubHouse({ onPost: async () => reply(503, { detail: "Device store busy" }) });
    const error = await syncSubscription(HOUSE_KEY).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 503,
      detail: "Device store busy",
      message: "Device store busy",
    });
  });

  it("gives up when no worker is ready", async () => {
    vi.useFakeTimers();
    registry({ ready: false });
    const fetchMock = stubHouse();
    let settled = false;
    const syncing = syncSubscription(HOUSE_KEY);
    syncing.catch(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(9_999);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const error = await syncing.catch((caught: unknown) => caught);
    // A deadline by name, so the retry policy does not wait it out twice.
    expect(error).toMatchObject({ name: "TimeoutError", message: "no service worker" });
    expect(timedOut(error)).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("gives up on a registration that never answers, with a deadline's name", async () => {
    vi.useFakeTimers();
    fakeDeadlines();
    registry();
    stubHouse({ onPost: stalled });
    let settled = false;
    const syncing = syncSubscription(HOUSE_KEY).catch((caught: unknown) => {
      settled = true;
      return caught;
    });
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 1);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const error = await syncing;
    expect(error).toMatchObject({ name: "TimeoutError" });
    expect(timedOut(error)).toBe(true);
    expect(log).toEqual(["subscribe", `POST ${DEVICES_PATH}`]);
  });
});

describe("requestAndSubscribe", () => {
  it("asks for permission before anything else", async () => {
    registry();
    const fetchMock = stubHouse();
    const requestPermission = stubNotification("granted");
    const asking = requestAndSubscribe();
    // Before the first await gives the tap away: iOS prompts only inside it.
    expect(requestPermission).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
    await expect(asking).resolves.toBe("granted");
    expect(log).toEqual([
      "requestPermission",
      `GET ${PUSH_KEY_PATH}`,
      "subscribe",
      `POST ${DEVICES_PATH}`,
    ]);
  });

  it("does not subscribe when permission is refused", async () => {
    const { pushManager } = registry();
    const fetchMock = stubHouse();
    stubNotification("denied");
    await expect(requestAndSubscribe()).resolves.toBe("denied");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(pushManager.subscribe).not.toHaveBeenCalled();
  });

  it("fails as unconfigured when the house has no key", async () => {
    const { pushManager } = registry();
    stubHouse({ key: null });
    stubNotification("granted");
    const error = await requestAndSubscribe().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, detail: "Web Push not configured" });
    expect(pushManager.subscribe).not.toHaveBeenCalled();
  });
});

describe("forgetPush", () => {
  it("forgets: DELETEs, then unsubscribes, and never rejects", async () => {
    const held = new FakeSubscription(HELD, HOUSE_BYTES);
    held.refuses = true;
    const { readyReads } = registry({ held, ready: false });
    const offline = () => Promise.reject(new TypeError("Failed to fetch"));
    const fetchMock = stubHouse({ onDelete: offline });
    await expect(forgetPush()).resolves.toBeUndefined();
    expect(requests(fetchMock)).toEqual([
      { method: "DELETE", path: DEVICES_PATH, body: { endpoint: HELD } },
    ]);
    expect(log).toEqual([`DELETE ${DEVICES_PATH}`, `unsubscribe ${HELD}`]);
    expect(readyReads()).toBe(0);
  });

  it("gives up on a DELETE that never answers, and still unsubscribes", async () => {
    vi.useFakeTimers();
    fakeDeadlines();
    const held = new FakeSubscription(HELD, HOUSE_BYTES);
    registry({ held });
    stubHouse({ onDelete: stalled });
    let settled = false;
    const forgetting = forgetPush().then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(4_999);
    expect(settled).toBe(false);
    expect(held.unsubscribe).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await forgetting;
    expect(held.unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("returns at once with no worker registered, never looking at ready", async () => {
    const { container, readyReads } = registry({ registered: false, ready: false });
    const fetchMock = stubHouse();
    await expect(forgetPush()).resolves.toBeUndefined();
    expect(container.getRegistration).toHaveBeenCalledTimes(1);
    expect(readyReads()).toBe(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns at once with no subscription, or no service worker at all", async () => {
    const fetchMock = stubHouse();
    await expect(forgetPush()).resolves.toBeUndefined();
    registry({ ready: false });
    await expect(forgetPush()).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("forgetPush and a sync under way", () => {
  it("keeps a sync that has not registered from registering, and unsubscribes what it made", async () => {
    const { pushManager } = registry();
    const fetchMock = stubHouse();
    const release = holdSubscribe(pushManager);
    const syncing = syncSubscription(HOUSE_KEY);
    await vi.waitFor(() => expect(pushManager.subscribe).toHaveBeenCalledTimes(1));

    const forgetting = forgetPush();
    release();
    await expect(syncing).resolves.toBeUndefined();
    await expect(forgetting).resolves.toBeUndefined();

    // No register at all: the house never learns of the subscription the sync made,
    // and the browser does not keep it.
    expect(requests(fetchMock)).toEqual([
      { method: "DELETE", path: DEVICES_PATH, body: { endpoint: FRESH } },
    ]);
    expect(log).toEqual(["subscribe", `DELETE ${DEVICES_PATH}`, `unsubscribe ${FRESH}`]);
    expect(pushManager.subscription).toBeNull();
  });

  it("sends its DELETE only once a register already in flight has been answered", async () => {
    const held = new FakeSubscription(HELD, HOUSE_BYTES);
    registry({ held });
    const post = holdPost();
    const fetchMock = stubHouse({ onPost: post.onPost });
    const syncing = syncSubscription(HOUSE_KEY);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const forgetting = forgetPush();
    await drain();
    expect(log).toEqual([`POST ${DEVICES_PATH}`]);

    post.answer();
    await syncing;
    await forgetting;
    expect(log).toEqual([
      `POST ${DEVICES_PATH}`,
      "POST answered",
      `DELETE ${DEVICES_PATH}`,
      `unsubscribe ${HELD}`,
    ]);
  });

  it("lets the next sync register once it has finished", async () => {
    const { pushManager } = registry();
    const fetchMock = stubHouse();
    const release = holdSubscribe(pushManager);
    const overlapping = syncSubscription(HOUSE_KEY);
    await vi.waitFor(() => expect(pushManager.subscribe).toHaveBeenCalledTimes(1));
    const forgetting = forgetPush();
    release();
    await overlapping;
    await forgetting;

    // The next sign-in's sync.
    await syncSubscription(HOUSE_KEY);
    expect(requests(fetchMock)).toEqual([
      { method: "DELETE", path: DEVICES_PATH, body: { endpoint: FRESH } },
      registering(pushManager.subscription!),
    ]);
  });

  it("keeps a sync that starts while it runs from registering, even once it has ended", async () => {
    const { pushManager, container } = registry();
    const fetchMock = stubHouse();
    // Sign-out held at `getRegistration()`, so a sync can start while it runs.
    let answerRegistration: () => void = () => {};
    container.getRegistration.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          answerRegistration = () => resolve({ pushManager });
        }),
    );
    const forgetting = forgetPush();
    await vi.waitFor(() => expect(container.getRegistration).toHaveBeenCalledTimes(1));

    // A retry starts mid-sign-out and is held at `subscribe()` until sign-out has
    // ended, then subscribes.
    const release = holdSubscribe(pushManager);
    const late = syncSubscription(HOUSE_KEY);
    await vi.waitFor(() => expect(pushManager.subscribe).toHaveBeenCalledTimes(1));
    answerRegistration();
    await forgetting;
    release();
    await expect(late).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("waits for a stuck sync no longer than a request may take", async () => {
    vi.useFakeTimers();
    fakeDeadlines();
    const { pushManager } = registry();
    const fetchMock = stubHouse();
    const release = holdSubscribe(pushManager);
    const stuck = syncSubscription(HOUSE_KEY);
    await vi.waitFor(() => expect(pushManager.subscribe).toHaveBeenCalledTimes(1));

    let settled = false;
    const forgetting = forgetPush().then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 1);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await forgetting;

    // The browser answers at last, long after sign-out: still no register.
    release();
    await stuck;
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("onOpenPath", () => {
  it("hears OPEN_PATH for routes it knows and nothing else", () => {
    const { container } = registry();
    const heard: string[] = [];
    const off = onOpenPath((path) => heard.push(path));
    const say = (data: unknown) => container.dispatchEvent(new MessageEvent("message", { data }));
    say({ type: "OPEN_PATH", path: "/actions/abc" });
    say({ type: "OPEN_PATH", path: "/" });
    say({ type: "OPEN_PATH", path: "https://203.0.113.9/" });
    say({ type: "OPEN_PATH", path: "/actions/a/b" });
    say({ type: "OPEN_PATH", path: 7 });
    say({ type: "X", path: "/" });
    say({ type: "X" });
    say(null);
    say("OPEN_PATH");
    off();
    say({ type: "OPEN_PATH", path: "/" });
    expect(heard).toEqual(["/actions/abc", "/"]);
  });

  it("listens to nothing where there is no service worker", () => {
    const listening = vi.spyOn(EventTarget.prototype, "addEventListener");
    const fn = vi.fn();
    const off = onOpenPath(fn);
    // Nothing stands in for the missing container: no listener is attached to any
    // target, so a tap's message on the window, say, is not heard.
    window.dispatchEvent(new MessageEvent("message", { data: { type: "OPEN_PATH", path: "/" } }));
    expect(listening).not.toHaveBeenCalled();
    expect(fn).not.toHaveBeenCalled();
    off();
  });
});
