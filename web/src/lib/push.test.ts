import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  DEVICES_PATH,
  fetchPublicKey,
  forgetPush,
  onOpenPath,
  permission,
  PUSH_KEY_PATH,
  reachContext,
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

class FakeSubscription {
  readonly endpoint: string;
  readonly options: { userVisibleOnly: boolean; applicationServerKey: ArrayBuffer | null };
  /** Whether unsubscribing fails. */
  refuses = false;
  readonly unsubscribe = vi.fn(() => {
    log.push(`unsubscribe ${this.endpoint}`);
    return this.refuses ? Promise.reject(new Error("unsubscribe failed")) : Promise.resolve(true);
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

class FakePushManager {
  subscription: FakeSubscription | null;
  readonly getSubscription = vi.fn(() => Promise.resolve(this.subscription));
  readonly subscribe = vi.fn((options: PushSubscriptionOptionsInit) => {
    log.push("subscribe");
    const key = new Uint8Array(options.applicationServerKey as ArrayBuffer);
    this.subscription = new FakeSubscription(FRESH, key);
    return Promise.resolve(this.subscription);
  });

  constructor(held: FakeSubscription | null) {
    this.subscription = held;
  }
}

/**
 * A browser's service worker container. `ready: false` is what `ready` is with
 * no worker registered — dev, always — a promise that never settles.
 */
function registry(
  options: { held?: FakeSubscription | null; ready?: boolean; registered?: boolean } = {},
) {
  const { held = null, ready = true, registered = true } = options;
  const pushManager = new FakePushManager(held);
  const registration = { pushManager };
  const container = Object.assign(new EventTarget(), {
    ready: ready ? Promise.resolve(registration) : new Promise<never>(() => {}),
    getRegistration: vi.fn(() => Promise.resolve(registered ? registration : undefined)),
  });
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: container });
  return { pushManager, container };
}

const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

/** The house: its key (null when Web Push is not configured), and how a DELETE ends. */
function stubHouse(options: { key?: string | null; onDelete?: () => Promise<Response> } = {}) {
  const { key = HOUSE_KEY, onDelete = async () => reply(200, { status: "ok" }) } = options;
  const mock = vi.fn<typeof fetch>(async (input, init) => {
    const method = init?.method ?? "GET";
    log.push(`${method} ${String(input)}`);
    if (String(input) === PUSH_KEY_PATH) {
      return key === null
        ? reply(404, { detail: "Web Push not configured" })
        : reply(200, { public_key: key });
    }
    if (method === "DELETE") return onDelete();
    return reply(200, { status: "ok" });
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

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
});

describe("syncSubscription", () => {
  it("subscribes with the house key and registers", async () => {
    const { pushManager } = registry();
    const fetchMock = stubHouse();
    await syncSubscription(HOUSE_KEY);
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
    await expect(syncing).rejects.toThrow("no service worker");
    expect(fetchMock).not.toHaveBeenCalled();
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
    registry({ held, ready: false });
    const offline = () => Promise.reject(new TypeError("Failed to fetch"));
    const fetchMock = stubHouse({ onDelete: offline });
    await expect(forgetPush()).resolves.toBeUndefined();
    expect(requests(fetchMock)).toEqual([
      { method: "DELETE", path: DEVICES_PATH, body: { endpoint: HELD } },
    ]);
    expect(log).toEqual([`DELETE ${DEVICES_PATH}`, `unsubscribe ${HELD}`]);
  });

  it("returns at once with no worker registered, never waiting for one", async () => {
    const { container } = registry({ registered: false, ready: false });
    const fetchMock = stubHouse();
    await expect(forgetPush()).resolves.toBeUndefined();
    expect(container.getRegistration).toHaveBeenCalledTimes(1);
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
    const fn = vi.fn();
    const off = onOpenPath(fn);
    expect(() => off()).not.toThrow();
  });
});
