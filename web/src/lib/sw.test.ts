import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyUpdate, installServiceWorker, onUpdateReady, SW_URL, type SwOptions } from "./sw";

// jsdom has no `navigator.serviceWorker`, so each test defines one and deletes it
// afterwards — `vi.stubGlobal` would have to replace the whole `navigator`. The
// fakes are EventTargets rather than maps of listeners: a map holds one listener
// per type, so a listener added twice or never removed would be invisible, and
// both are things these tests exist to catch.

/** A ServiceWorker: enough of one to change state and take a message. */
class FakeWorker extends EventTarget {
  state: ServiceWorkerState;
  readonly postMessage = vi.fn();

  constructor(state: ServiceWorkerState) {
    super();
    this.state = state;
  }

  /** Move to `next` and say so, as the browser does. */
  settle(next: ServiceWorkerState): void {
    this.state = next;
    this.dispatchEvent(new Event("statechange"));
  }
}

class FakeRegistration extends EventTarget {
  installing: FakeWorker | null = null;
  waiting: FakeWorker | null;
  readonly unregister = vi.fn(() => Promise.resolve(true));
  /** How many update checks the page has asked for. */
  updates = 0;
  /**
   * How the next update check ends. `update` is a plain method rather than a
   * `vi.fn` on purpose: a `vi.fn` attaches a handler to every promise it
   * returns, to record how it settled, and so would hide the one thing two tests
   * below exist for — a rejection nobody handled, which vitest fails the run on.
   */
  checked: () => Promise<void> = () => Promise.resolve();

  constructor(waiting: FakeWorker | null) {
    super();
    this.waiting = waiting;
  }

  update(): Promise<void> {
    this.updates += 1;
    return this.checked();
  }

  /** A changed sw.js has started installing. */
  found(incoming: FakeWorker): void {
    this.installing = incoming;
    this.dispatchEvent(new Event("updatefound"));
  }
}

class FakeContainer extends EventTarget {
  controller: object | null;
  readonly registration: FakeRegistration;
  readonly register = vi.fn(() => Promise.resolve(this.registration));
  readonly getRegistrations = vi.fn(() => Promise.resolve([this.registration]));

  constructor(registration: FakeRegistration, controller: object | null) {
    super();
    this.registration = registration;
    this.controller = controller;
  }

  /** Hand the page to `next`: an update taking over, or a first worker's claim. */
  control(next: object): void {
    this.controller = next;
    this.dispatchEvent(new Event("controllerchange"));
  }
}

function registry(options: { waiting?: FakeWorker; controller?: object | null } = {}) {
  const registration = new FakeRegistration(options.waiting ?? null);
  const container = new FakeContainer(registration, options.controller ?? null);
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: container });
  return { registration, container };
}

const cleanups: (() => void)[] = [];

/** Install for real, and leave the uninstall to afterEach. */
async function install(options: SwOptions = {}): Promise<() => void> {
  const stop = await installServiceWorker({ enabled: true, ...options });
  cleanups.push(stop);
  return stop;
}

/**
 * Everything onUpdateReady says from here on, starting with the answer as it
 * stands. Subscribed *after* the test's own install, so the first entry is what
 * that install found — never what some other test left behind.
 */
function watch(): boolean[] {
  const seen: boolean[] = [];
  cleanups.push(onUpdateReady((ready) => seen.push(ready)));
  return seen;
}

/** A turn of the event loop: long enough for a settled promise's handlers to run. */
function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/** What the deployed container answers today: mount_spa serves index.html for /sw.js. */
function mimeRefusal(): DOMException {
  return new DOMException(
    "The script has an unsupported MIME type ('text/html').",
    "SecurityError",
  );
}

beforeEach(() => {
  Object.defineProperty(globalThis, "caches", {
    configurable: true,
    value: {
      keys: vi.fn(() => Promise.resolve(["alfred-shell-v1"])),
      delete: vi.fn(() => Promise.resolve(true)),
    },
  });
});

afterEach(() => {
  for (const cleanup of cleanups.splice(0).reverse()) cleanup();
  Reflect.deleteProperty(navigator, "serviceWorker");
  Reflect.deleteProperty(globalThis, "caches");
});

describe("installServiceWorker", () => {
  it("registers the worker at the root, so its scope is the whole app", async () => {
    const { container } = registry();
    await install();
    expect(container.register).toHaveBeenCalledWith(SW_URL);
  });

  it("registers nothing in dev, and clears what a previous preview left", async () => {
    // A worker registered by `npm run preview` outsurvives the preview server,
    // and `npm run dev` on the same origin then serves through a stale cache
    // and fights HMR. Dev actively cleans up rather than merely abstaining.
    const { container, registration } = registry();
    await installServiceWorker({ enabled: false });
    expect(container.register).not.toHaveBeenCalled();
    expect(registration.unregister).toHaveBeenCalled();
    expect(caches.delete).toHaveBeenCalledWith("alfred-shell-v1");
  });

  it("does nothing at all where there is no service worker", async () => {
    await expect(installServiceWorker({ enabled: true })).resolves.toBeTypeOf("function");
  });

  it("fails loudly when the worker cannot be registered", async () => {
    // A /sw.js that comes back as HTML is a deploy that did not ship the file,
    // and on a phone with no worker yet this rejection is the only sign of it.
    const refused = mimeRefusal();
    const { container } = registry();
    container.register.mockRejectedValue(refused);
    await expect(installServiceWorker({ enabled: true })).rejects.toBe(refused);
  });

  it("asks for a changed worker every time the app comes back", async () => {
    const { registration } = registry();
    await install();
    // An installed PWA is brought back, not relaunched, so it can stay open for
    // days without the navigation at which the browser checks /sw.js itself.
    document.dispatchEvent(new Event("visibilitychange"));
    expect(registration.updates).toBe(1);
  });

  it("stops re-checking once uninstalled", async () => {
    const { registration } = registry();
    const stop = await install();
    stop();
    document.dispatchEvent(new Event("visibilitychange"));
    expect(registration.updates).toBe(0);
  });

  it("takes a check that fails offline as no news", async () => {
    // Expected on every return to the foreground without a signal. Unhandled,
    // it would be a rejection per foreground — and vitest fails the whole run
    // on one, which is the half of this test the spy cannot see (and why
    // `update` on the fake is not a `vi.fn`).
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { registration } = registry();
    registration.checked = () => Promise.reject(new TypeError("Failed to fetch"));
    await install();
    document.dispatchEvent(new Event("visibilitychange"));
    await flush();
    expect(registration.updates).toBe(1);
    expect(warn).not.toHaveBeenCalled();
  });

  it("reports a check that fails while the phone is online", async () => {
    // A phone that already has a worker gets no rejection from register() —
    // it answers at once without fetching — so a deploy that stopped shipping
    // /sw.js surfaces here, as this refusal, and nowhere else.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const refused = mimeRefusal();
    const { registration } = registry();
    registration.checked = () => Promise.reject(refused);
    await install();
    document.dispatchEvent(new Event("visibilitychange"));
    await flush();
    expect(warn).toHaveBeenCalledWith("service worker update check failed", refused);
  });
});

describe("onUpdateReady", () => {
  it("announces a worker that is already waiting", async () => {
    registry({ waiting: new FakeWorker("installed"), controller: {} });
    await install();
    expect(watch()).toEqual([true]);
  });

  it("announces one that finishes installing while the app is open", async () => {
    const { registration } = registry({ controller: {} });
    await install();
    const seen = watch();
    const incoming = new FakeWorker("installing");
    registration.found(incoming);
    incoming.settle("installed");
    expect(seen).toEqual([false, true]);
  });

  it("announces one that was already installing when the app registered", async () => {
    // The browser checks /sw.js at every navigation, so on a launch it can have
    // found a changed one before this page's `updatefound` listener existed.
    const { registration } = registry({ controller: {} });
    const incoming = new FakeWorker("installing");
    registration.installing = incoming;
    await install();
    const seen = watch();
    incoming.settle("installed");
    expect(seen).toEqual([false, true]);
  });

  it("says nothing on a first install", async () => {
    // No controller means this is the first worker this phone has ever had.
    // There is nothing to reload *into*, and offering a reload would be a
    // control with no effect.
    const { registration } = registry({ controller: null });
    await install();
    const seen = watch();
    const incoming = new FakeWorker("installing");
    registration.found(incoming);
    incoming.settle("installed");
    incoming.settle("activating");
    incoming.settle("activated");
    expect(seen).toEqual([false]);
  });

  it("withdraws the announcement once the worker stops waiting", async () => {
    const waiting = new FakeWorker("installed");
    registry({ waiting, controller: {} });
    await install();
    const seen = watch();
    waiting.settle("activating");
    expect(seen).toEqual([true, false]);
  });

  it("moves to the newer worker when one supersedes the waiting one", async () => {
    // Another changed sw.js arrived while the first still waited: the browser
    // makes the old one redundant and the new one waits in its place.
    const older = new FakeWorker("installed");
    const { registration } = registry({ waiting: older, controller: {} });
    await install();
    const seen = watch();
    const newer = new FakeWorker("installing");
    registration.found(newer);
    older.settle("redundant");
    newer.settle("installed");
    expect(seen).toEqual([true, false, true]);
    applyUpdate();
    expect(newer.postMessage).toHaveBeenCalledOnce();
    expect(older.postMessage).not.toHaveBeenCalled();
  });

  it("keeps the newer worker if the older one's retirement is heard last", async () => {
    // The two state changes are separate events. Whichever arrives second, the
    // one that stops waiting is forgotten only if it is still the one held.
    const older = new FakeWorker("installed");
    const { registration } = registry({ waiting: older, controller: {} });
    await install();
    const seen = watch();
    const newer = new FakeWorker("installing");
    registration.found(newer);
    newer.settle("installed");
    older.settle("redundant");
    expect(seen.at(-1)).toBe(true);
    applyUpdate();
    expect(newer.postMessage).toHaveBeenCalledOnce();
    expect(older.postMessage).not.toHaveBeenCalled();
  });

  it("forgets a worker an earlier install saw waiting", async () => {
    // State is derived by each install from its own registration, never
    // inherited — the earlier install is deliberately not stopped first.
    const earlier = new FakeWorker("installed");
    registry({ waiting: earlier, controller: {} });
    await install();
    const seen = watch();
    registry({ controller: {} });
    await install();
    applyUpdate();
    expect(seen).toEqual([true, false]);
    expect(earlier.postMessage).not.toHaveBeenCalled();
  });

  it("forgets the waiting worker once uninstalled, and hears nothing after", async () => {
    const waiting = new FakeWorker("installed");
    const { registration } = registry({ waiting, controller: {} });
    const stop = await install();
    const seen = watch();
    const pending = new FakeWorker("installing");
    registration.found(pending);
    stop();
    pending.settle("installed");
    registration.found(new FakeWorker("installed"));
    applyUpdate();
    expect(seen).toEqual([true, false]);
    expect(waiting.postMessage).not.toHaveBeenCalled();
  });

  it("says nothing when the answer has not changed", async () => {
    registry({ controller: {} });
    const stop = await install();
    const seen = watch();
    stop();
    await install();
    expect(seen).toEqual([false]);
  });

  it("stops telling a subscriber that has left", async () => {
    const { registration } = registry({ controller: {} });
    await install();
    const seen: boolean[] = [];
    const leave = onUpdateReady((ready) => seen.push(ready));
    leave();
    const incoming = new FakeWorker("installing");
    registration.found(incoming);
    incoming.settle("installed");
    expect(seen).toEqual([false]);
  });
});

describe("applyUpdate", () => {
  it("asks the waiting worker to take over", async () => {
    const waiting = new FakeWorker("installed");
    registry({ waiting, controller: {} });
    await install();
    applyUpdate();
    expect(waiting.postMessage).toHaveBeenCalledWith({ type: "SKIP_WAITING" });
  });

  it("does nothing when nothing is waiting", async () => {
    const { registration } = registry({ controller: {} });
    await install();
    const incoming = new FakeWorker("installing");
    registration.found(incoming);
    expect(watch()).toEqual([false]);
    expect(() => applyUpdate()).not.toThrow();
    expect(incoming.postMessage).not.toHaveBeenCalled();
  });

  it("asks once the worker has stopped waiting, not again", async () => {
    const waiting = new FakeWorker("installed");
    registry({ waiting, controller: {} });
    await install();
    applyUpdate();
    waiting.settle("activating");
    applyUpdate();
    expect(waiting.postMessage).toHaveBeenCalledOnce();
  });

  it("reloads once when the new worker takes control", async () => {
    const reload = vi.fn();
    const { container } = registry({ waiting: new FakeWorker("installed"), controller: {} });
    await install({ reload });
    container.control({});
    container.control({});
    // Twice, because Safari fires it again after the reload starts. A second
    // reload is a reload loop, which is the classic way to brick a PWA.
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("does not reload when the first worker claims the page", async () => {
    // sw.js's activate calls clients.claim(), so a phone's first visit sees
    // `controllerchange` seconds after it loaded — while someone may be halfway
    // through registering a passkey. The page came from the network just now;
    // there is nothing newer to reload into. A later takeover still reloads.
    const reload = vi.fn();
    const { container } = registry({ controller: null });
    await install({ reload });
    container.control({});
    expect(reload).not.toHaveBeenCalled();
    container.control({});
    expect(reload).toHaveBeenCalledOnce();
  });

  it("reloads nothing once uninstalled", async () => {
    const reload = vi.fn();
    const { container } = registry({ controller: {} });
    const stop = await install({ reload });
    stop();
    container.control({});
    expect(reload).not.toHaveBeenCalled();
  });
});
