/**
 * The count's names, as `sw.js` has them. That file is hand-written and not
 * bundled, so there is nothing to share them through, and
 * `src/test/sw-badge.test.ts` pins that the two agree.
 */
const BADGE_DB = "alfred-badge";
const BADGE_STORE = "count";
const BADGE_KEY = "pushes";

/** Whether this browser can put a number on the icon. The worker counts nothing where it can't. */
export function badgeSupported(): boolean {
  return "setAppBadge" in navigator;
}

/**
 * Put the worker's count back to zero, with the worker's own open and upgrade:
 * this may be the first to reach the database. Closed however it ends, since a
 * connection left open stands in the way of any later version of it. A write
 * that fails settles this like one that lands, as nobody is waiting to hear which.
 */
function zeroCount(): Promise<void> {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open(BADGE_DB, 1);
    opening.onupgradeneeded = () => opening.result.createObjectStore(BADGE_STORE);
    opening.onerror = () => reject(opening.error);
    opening.onsuccess = () => {
      const database = opening.result;
      const end = () => {
        database.close();
        resolve();
      };
      try {
        const writing = database.transaction(BADGE_STORE, "readwrite");
        writing.objectStore(BADGE_STORE).put(0, BADGE_KEY);
        writing.oncomplete = end;
        writing.onerror = end;
        writing.onabort = end;
      } catch {
        // What `transaction()` throws, a database with no such store for one.
        end();
      }
    };
  });
}

/** `clearAppBadge`, with a throw made a rejection, so that it stops nothing beside it. */
async function clearIcon(): Promise<void> {
  await navigator.clearAppBadge();
}

/**
 * Take the number off the icon: the worker's count, and the icon itself. Each
 * runs whatever becomes of the other, so a store that has gone still clears the
 * icon and an icon that refuses still zeroes the count. Returns at once where
 * the browser has no badge.
 *
 * It never rejects, and it has no time limit of its own. It settles when both
 * have, so on a hung store it may never settle: every caller leaves it unawaited.
 */
export async function clearBadge(): Promise<void> {
  if (!badgeSupported()) return;
  await Promise.allSettled([zeroCount(), clearIcon()]);
}

/**
 * Hear the worker's `{type: "BADGE_ADDED"}`, a push counted while a window was
 * open (sw.js's `countPush`). Returns the unsubscriber.
 */
export function onBadgeAdded(fn: () => void): () => void {
  if (!("serviceWorker" in navigator)) return () => {};
  const container = navigator.serviceWorker;
  const hear = (event: MessageEvent) => {
    const data: unknown = event.data;
    if (typeof data !== "object" || data === null) return;
    if ((data as { type?: unknown }).type === "BADGE_ADDED") fn();
  };
  container.addEventListener("message", hear);
  return () => container.removeEventListener("message", hear);
}
