import { ACTION_PATH } from "@/door/useActionRoute";
import { api, ApiError, post } from "./api";
import { b64urlToBuf, sessionChannel } from "./webauthn";

/** The house's VAPID public key: `push_public_key` in core/channels/web_server.py. No session. */
export const PUSH_KEY_PATH = "/api/push/public-key";
/** Where a subscription is registered (`POST`) and forgotten (`DELETE`). Session only. */
export const DEVICES_PATH = "/api/devices/register";
/** How long a sync waits for a worker to be ready before it gives up. */
export const READY_TIMEOUT_MS = 10_000;

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

/** The house's public key, or null when Web Push is not configured there (its 404). */
export async function fetchPublicKey(): Promise<string | null> {
  try {
    return (await api<{ public_key: string }>(PUSH_KEY_PATH)).public_key;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

/**
 * The active worker's registration. `ready` never settles on a page with no
 * worker registered, so the wait has a deadline: a sync must fail, where the UI
 * can offer it again, rather than hang for the life of the page.
 */
async function readyRegistration(): Promise<ServiceWorkerRegistration> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("no service worker")), READY_TIMEOUT_MS);
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
 * Drop the house's row for `endpoint`. Best effort: whoever calls this is
 * leaving the subscription behind either way, and a row left at the house names
 * an endpoint the browser drops next, which the push service answers 404 or 410
 * and the adapter prunes. The cap bounds it meanwhile.
 */
async function forgetRow(endpoint: string): Promise<void> {
  try {
    await api(DEVICES_PATH, { method: "DELETE", body: JSON.stringify({ endpoint }) });
  } catch {
    // Nothing to tell anyone: see above.
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
 * Rejects when no worker is ready in time, when the browser refuses, or when
 * the registration does — the house's 503 `Device store busy` included, which
 * the caller retries like any other failure.
 */
export async function syncSubscription(publicKey: string): Promise<void> {
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
  await post(DEVICES_PATH, { platform: "web_push", subscription: subscription.toJSON() });
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
 * It never waits for `ready`, which never settles without a registered worker
 * (always, in dev) and would hold sign-out forever: `getRegistration()` answers
 * at once. And it never rejects, so sign-out never stops here. The unsubscribe
 * runs whatever the DELETE answered, so the browser stops receiving even when
 * the house could not be told.
 */
export async function forgetPush(): Promise<void> {
  if (!("serviceWorker" in navigator)) return;
  try {
    const registration = await navigator.serviceWorker.getRegistration();
    const subscription = await registration?.pushManager.getSubscription();
    if (!subscription) return;
    await forgetRow(subscription.endpoint);
    await subscription.unsubscribe();
  } catch {
    // A subscription the browser kept is registered again by the next sync, and
    // one it dropped is pruned at the house: neither is worth failing sign-out.
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
