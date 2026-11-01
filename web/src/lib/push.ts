import { ACTION_PATH } from "@/door/useActionRoute";
import { api, ApiError } from "./api";
import { b64urlToBuf, sessionChannel } from "./webauthn";

/** The house's VAPID public key: `push_public_key` in core/channels/web_server.py. No session. */
export const PUSH_KEY_PATH = "/api/push/public-key";
/** Where a subscription is registered (`POST`) and forgotten (`DELETE`). Session only. */
export const DEVICES_PATH = "/api/devices/register";
/** How long a sync waits for a worker to be ready before it gives up. */
export const READY_TIMEOUT_MS = 10_000;
/**
 * How long the key read and the registration may each take. `api()` sets no
 * deadline, and a socket the house accepts and never answers would hold
 * Enable's `asking`, or the launch sync's `checking`, until the browser gave up.
 */
export const REQUEST_TIMEOUT_MS = 10_000;
/**
 * How long a best-effort DELETE may take. Its answer changes nothing, and a
 * stalled connection would otherwise hold sign-out until the browser gives up —
 * about a minute on iOS.
 */
export const FORGET_TIMEOUT_MS = 5_000;

/**
 * Where this page stands for Web Push. "tab": not on the Home Screen, and iOS
 * pushes only to an installed app, so the way on is to install. "unsupported":
 * installed, but nothing here can subscribe — a browser missing one of the
 * three APIs, or dev, which registers no worker (`installServiceWorker`,
 * ./sw.ts). "capable": everything a subscription needs is here.
 */
export type ReachContext = "tab" | "unsupported" | "capable";

/**
 * Standalone is `sessionChannel()`'s test, so push and sign-in never disagree
 * about which client this is. It comes first: a tab in dev is still a tab.
 * `enabled` is a parameter for the reason it is one in `installServiceWorker`.
 */
export function reachContext(enabled: boolean = import.meta.env.PROD): ReachContext {
  if (sessionChannel() !== "pwa") return "tab";
  const supported =
    "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  return enabled && supported ? "capable" : "unsupported";
}

/** The page's notification permission; "default" where the browser has no Notification. */
export function permission(): NotificationPermission {
  return "Notification" in window ? Notification.permission : "default";
}

/**
 * `api`, given up after `REQUEST_TIMEOUT_MS`. It rejects with the deadline's own
 * TimeoutError whatever fetch rejected with, as `fetchAuthStatus` does: an engine
 * without abort reasons rejects with a plain AbortError, and the retry policy
 * (`QUERY_DEFAULTS`, through `timedOut`) knows a deadline only by its name. A
 * request that has already waited this long is not worth a second wait.
 */
async function bounded<T>(path: string, init?: RequestInit): Promise<T> {
  const deadline = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  try {
    return await api<T>(path, { ...init, signal: deadline });
  } catch (error) {
    throw deadline.aborted ? deadline.reason : error;
  }
}

/**
 * The house's public key, or null when Web Push is not configured there (its
 * 404). Rejects after `REQUEST_TIMEOUT_MS` with a TimeoutError.
 */
export async function fetchPublicKey(): Promise<string | null> {
  try {
    return (await bounded<{ public_key: string }>(PUSH_KEY_PATH)).public_key;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

/**
 * The active worker's registration. `ready` never settles on a page with no
 * worker registered, so the wait has a deadline: a sync must fail, where the UI
 * can offer it again, rather than hang for the life of the page. It is named a
 * TimeoutError, as `AbortSignal.timeout`'s are, so the retry policy does not
 * wait it out a second time.
 */
async function readyRegistration(): Promise<ServiceWorkerRegistration> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => reject(new DOMException("no service worker", "TimeoutError")),
      READY_TIMEOUT_MS,
    );
  });
  try {
    return await Promise.race([navigator.serviceWorker.ready, deadline]);
  } finally {
    clearTimeout(timer);
  }
}

function sameBytes(held: ArrayBuffer, want: Uint8Array): boolean {
  const bytes = new Uint8Array(held);
  return bytes.length === want.length && bytes.every((byte, i) => byte === want[i]);
}

/**
 * Drop the house's row for `endpoint`, giving up after `FORGET_TIMEOUT_MS`.
 * Best effort: whoever calls this unsubscribes next, whatever it answered. A row
 * left behind stays until a push service refuses it with 404 or 410, and the
 * adapter prunes it, or until the cap evicts it. After a key change a push
 * service may answer 403 instead, which prunes nothing.
 */
async function forgetRow(endpoint: string): Promise<void> {
  try {
    await api(DEVICES_PATH, {
      method: "DELETE",
      body: JSON.stringify({ endpoint }),
      signal: AbortSignal.timeout(FORGET_TIMEOUT_MS),
    });
  } catch {
    // A refusal, an outage or the deadline: nothing to tell anyone, see above.
  }
}

/**
 * Sign-out's two rules (docs/web-push.md, "From the PWA"). `generation` moves when
 * a `forgetPush` starts and again when it ends, and `forgetting` counts those
 * under way: a sync registers only if neither says one has started since it did.
 * `syncing` holds the syncs under way, which `forgetPush` waits for.
 */
let generation = 0;
let forgetting = 0;
const syncing = new Set<Promise<void>>();

/**
 * Every sync under way settled, or `REQUEST_TIMEOUT_MS` gone, whichever is first.
 * A register POST already sent carries that deadline itself, so it has always
 * been answered or given up by then; a sync stuck before its POST never sends it.
 */
async function syncsSettled(): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, REQUEST_TIMEOUT_MS);
  });
  try {
    await Promise.race([Promise.allSettled([...syncing]), deadline]);
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Register this browser's subscription with the house, subscribing first when
 * it holds none. Run at every launch, so a row the house pruned or lost comes
 * back without the owner doing anything.
 *
 * A subscription made under a key other than the house's — the house's was
 * regenerated (docs/web-push.md, "A rotated key heals itself") — can never be
 * pushed to again: its row is deleted, and it is unsubscribed and replaced. One
 * whose key the browser does not report is kept, since nothing says it is stale.
 *
 * A sync that has not sent its register POST by the time a sign-out starts, or
 * that starts during one, never sends it: it resolves without registering.
 *
 * Rejects when no worker is ready in time, when the browser refuses, or when
 * the registration does or runs out of time. The two deadlines reject with a
 * TimeoutError, which the query's retry policy never repeats; anything else —
 * the house's 503 `Device store busy` included — it retries once.
 */
export async function syncSubscription(publicKey: string): Promise<void> {
  const run = runSync(publicKey, generation);
  syncing.add(run);
  try {
    await run;
  } finally {
    syncing.delete(run);
  }
}

/** `syncSubscription`'s steps, for a sync that started at `generation` `started`. */
async function runSync(publicKey: string, started: number): Promise<void> {
  const { pushManager } = await readyRegistration();
  const key = new Uint8Array(b64urlToBuf(publicKey));
  let subscription = await pushManager.getSubscription();
  const heldKey = subscription?.options.applicationServerKey;
  if (subscription && heldKey && !sameBytes(heldKey, key)) {
    await forgetRow(subscription.endpoint);
    await subscription.unsubscribe();
    subscription = null;
  }
  subscription ??= await pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: key,
  });
  if (generation !== started || forgetting > 0) return;
  await bounded(DEVICES_PATH, {
    method: "POST",
    body: JSON.stringify({ platform: "web_push", subscription: subscription.toJSON() }),
  });
}

/**
 * Ask for permission, and subscribe if it is granted. Call it from the tap: iOS
 * prompts only inside a user gesture, and an await ahead of the prompt would
 * end it, so asking is the first thing done. Resolves to the answer. Rejects as
 * `syncSubscription` does, and with `ApiError(404, "Web Push not configured")`
 * when the house has no key.
 */
export async function requestAndSubscribe(): Promise<NotificationPermission> {
  const answer = await Notification.requestPermission();
  if (answer !== "granted") return answer;
  const key = await fetchPublicKey();
  if (key === null) throw new ApiError(404, "Web Push not configured");
  await syncSubscription(key);
  return answer;
}

/**
 * Forget this browser's subscription, at the house and then in the browser —
 * what signing out does. Run it before the session ends: the DELETE needs it.
 *
 * A sync under way is waited for first, for at most `REQUEST_TIMEOUT_MS`: one
 * that has not sent its register POST never will, and one that has is answered
 * before the DELETE goes, so the DELETE is always the last word. Whatever
 * subscription the browser holds then is the one forgotten.
 *
 * It never waits for `ready`, which never settles without a registered worker
 * (always, in dev) and would hold sign-out forever: `getRegistration()` answers
 * at once. And it never rejects, so sign-out never stops here. The DELETE gives
 * up after `FORGET_TIMEOUT_MS`, and the unsubscribe runs whatever it answered, so
 * the browser stops receiving even when the house could not be told.
 */
export async function forgetPush(): Promise<void> {
  generation += 1;
  forgetting += 1;
  try {
    if (!("serviceWorker" in navigator)) return;
    await syncsSettled();
    const registration = await navigator.serviceWorker.getRegistration();
    const subscription = await registration?.pushManager.getSubscription();
    if (!subscription) return;
    await forgetRow(subscription.endpoint);
    await subscription.unsubscribe();
  } catch {
    // A known gap, which "never rejects" accepts: when `getRegistration()` or
    // `getSubscription()` throws, neither the DELETE nor the unsubscribe ran, and
    // when the DELETE and the unsubscribe both fail, the browser keeps its
    // subscription and the house its row. Either way this browser goes on
    // receiving pushes while signed out.
  } finally {
    forgetting -= 1;
    generation += 1;
  }
}

/**
 * Hear the worker's `{type: "OPEN_PATH", path}`, a notification tapped while a
 * window was open (sw.js's `notificationclick`). Only a path the router routes
 * is passed on — `/`, or one `ACTION_PATH` matches — so no message, from a
 * worker of an older build or anything else, can steer the app elsewhere.
 * Returns the unsubscriber.
 */
export function onOpenPath(fn: (path: string) => void): () => void {
  if (!("serviceWorker" in navigator)) return () => {};
  const container = navigator.serviceWorker;
  const hear = (event: MessageEvent) => {
    const data: unknown = event.data;
    if (typeof data !== "object" || data === null) return;
    const { type, path } = data as { type?: unknown; path?: unknown };
    if (type !== "OPEN_PATH" || typeof path !== "string") return;
    if (path === "/" || ACTION_PATH.test(path)) fn(path);
  };
  container.addEventListener("message", hear);
  return () => container.removeEventListener("message", hear);
}
