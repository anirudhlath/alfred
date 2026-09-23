import { onVisible } from "./lifecycle";

/** The worker is at the root so its scope is the whole app. */
export const SW_URL = "/sw.js";

export interface SwOptions {
  /**
   * Defaults to `import.meta.env.PROD`. A parameter rather than a read, so a
   * test proves the production path without stubbing the environment.
   */
  enabled?: boolean;
  /** Injected: jsdom implements no navigation, so a test cannot let this run. */
  reload?: () => void;
}

type Listener = (ready: boolean) => void;

// Module-level, like ConnectionProvider's singletons: one per module load, which
// is one per app. Whoever subscribes or applies is far from main.tsx's single
// `installServiceWorker()` and holds no registration of its own.
const listeners = new Set<Listener>();

/**
 * The worker the live install has seen reach "installed" behind a controller
 * and not yet seen leave it. Every `installServiceWorker` starts it from
 * nothing, its uninstaller clears it, and a worker that moves on — taking over,
 * or made redundant by a newer one — is dropped the moment it does.
 */
let waiting: ServiceWorker | null = null;

/**
 * Tell every subscriber, if the answer changed. A worker can be tracked twice —
 * found installing by the install, then reported again by an `updatefound`
 * that was already on its way — and hearing it twice must not say it twice.
 */
function setWaiting(next: ServiceWorker | null): void {
  if (next === waiting) return;
  waiting = next;
  for (const fn of [...listeners]) fn(waiting !== null);
}

/**
 * Subscribe to "a changed sw.js has installed and is waiting for permission".
 * That is narrower than "a new build": sw.js is byte-identical across most
 * deploys, so most deploys install no worker and never announce here — an open
 * app takes them at its next navigation, which sw.js answers network-first.
 *
 * Called immediately with the state as it stands, like `subscribeOnline` in
 * `ConnectionProvider`: a subscriber that arrives after the worker installed
 * must not wait for the next one.
 */
export function onUpdateReady(fn: Listener): () => void {
  listeners.add(fn);
  fn(waiting !== null);
  return () => void listeners.delete(fn);
}

/**
 * Take the waiting worker. It answers by calling `skipWaiting()`, which fires
 * `controllerchange`, which reloads the page — see `installServiceWorker`.
 * Without this a changed sw.js waits until every page the old worker controls
 * has closed — a reload does not count, see sw.js's `message` listener — and an
 * installed PWA can stay open for days.
 */
export function applyUpdate(): void {
  waiting?.postMessage({ type: "SKIP_WAITING" });
}

/** Drop every worker and every cache this origin holds. What dev does instead of registering. */
export async function unregisterAll(): Promise<void> {
  if (!("serviceWorker" in navigator)) return;
  const registrations = await navigator.serviceWorker.getRegistrations();
  await Promise.all(registrations.map((registration) => registration.unregister()));
  if ("caches" in globalThis) {
    const names = await caches.keys();
    await Promise.all(names.map((name) => caches.delete(name)));
  }
}

/**
 * Install the app shell's worker and watch for its successor. Resolves to the
 * uninstaller, which detaches everything this install attached and forgets its
 * waiting worker.
 *
 * Rejects when `register()` does, and the caller must report it: a /sw.js that
 * comes back as HTML — `mount_spa` answers any unknown path with index.html — is
 * a deploy that did not ship the file, and on a phone with no worker yet this
 * rejection is the only sign of it. A phone that already has one gets no
 * rejection here (`register()` answers at once, without fetching), and hears it
 * from the foreground update check instead.
 *
 * In dev this *unregisters* instead: a worker left behind by `npm run preview`
 * outlives the preview server, and `npm run dev` on the same origin then serves
 * a stale shell and fights HMR.
 */
export async function installServiceWorker(options: SwOptions = {}): Promise<() => void> {
  const { enabled = import.meta.env.PROD, reload = () => window.location.reload() } = options;
  // Derived by this install, never inherited: whatever an earlier install saw
  // waiting belonged to a registration this one has not looked at yet.
  setWaiting(null);
  if (!("serviceWorker" in navigator)) return () => {};
  if (!enabled) {
    await unregisterAll();
    return () => {};
  }

  const container = navigator.serviceWorker;
  const detach: (() => void)[] = [];
  const listen = (target: EventTarget, type: string, fn: () => void) => {
    target.addEventListener(type, fn);
    detach.push(() => target.removeEventListener(type, fn));
  };

  // A page with no controller that gains one has been *claimed* — sw.js's
  // activate calls clients.claim(), so a phone's first visit sees this seconds
  // after it loaded, perhaps halfway through registering a passkey. That page
  // came from the network moments ago and has nothing newer to reload into.
  // Only a page that already had a controller is being taken over.
  let controlled = container.controller !== null;
  let reloading = false;
  listen(container, "controllerchange", () => {
    if (!controlled) {
      controlled = true;
      return;
    }
    // Once. Safari fires this again while the reload is already under way, and
    // a second reload is a reload loop — the classic way to brick a PWA.
    if (reloading) return;
    reloading = true;
    reload();
  });

  const registration = await container.register(SW_URL);

  const track = (incoming: ServiceWorker | null) => {
    if (!incoming) return;
    const settle = () => {
      if (incoming.state === "installed") {
        // A controller already present means this is an *update*. On a first
        // install there is nothing to reload into, and offering a reload would
        // be a control with no effect.
        if (container.controller) setWaiting(incoming);
      } else if (incoming === waiting) {
        // It has stopped waiting: activating, because it was told to, or
        // redundant, because a newer one replaced it. Only the one held is
        // dropped — the two state changes arrive as separate events, and the
        // newer worker's may already have been heard.
        setWaiting(null);
      }
    };
    settle();
    listen(incoming, "statechange", settle);
  };

  track(registration.waiting);
  // The browser checks /sw.js at every navigation, so on a launch it can have
  // found a changed one before `updatefound` below had a listener.
  track(registration.installing);
  listen(registration, "updatefound", () => track(registration.installing));

  // An installed PWA is brought back, not relaunched, so it can stay open for
  // days without the navigation at which the browser checks /sw.js itself. This
  // is how a changed sw.js — a policy change, or the kill switch — reaches an
  // app that stays open. A new build with the same sw.js installs nothing here.
  detach.push(
    onVisible(() => {
      registration.update().catch((error: unknown) => {
        // Offline, a failed check is expected on every foreground and says
        // nothing. Online it is worth a line: this is the one place a phone that
        // already has a worker learns /sw.js has stopped being JavaScript.
        if (navigator.onLine) console.warn("service worker update check failed", error);
      });
    }),
  );

  return () => {
    for (const off of detach.splice(0)) off();
    // Nothing is listening to it any more, so nothing would hear it leave.
    setWaiting(null);
  };
}
