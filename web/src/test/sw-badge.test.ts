import { IDBFactory } from "fake-indexeddb";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearBadge } from "@/lib/badge";
// The shipped bytes, as sw.test.ts reads them.
import source from "../../public/sw.js?raw";
// The same file and the kill switch, each as its exact bytes: see FIRED.
import shippedBytes from "../../public/sw.js?url&inline";
import killSwitchBytes from "../../public/sw.kill.js?url&inline";
import { type LoadOptions, loadWorker, ORIGIN } from "./sw-harness";

/**
 * Whether the kill switch is fired: sw.js a byte-for-byte copy of sw.kill.js,
 * told exactly as sw.test.ts tells it, and for the same reason. The switch
 * counts nothing, and these tests are about the worker the copy took out, so
 * they stand down and the one test that runs says why.
 */
const FIRED = shippedBytes === killSwitchBytes;

if (FIRED) {
  it("stands down: web/public/sw.js is the kill switch, fired — sw.kill.js byte for byte", async ({
    annotate,
  }) => {
    expect(shippedBytes).toBe(killSwitchBytes);
    // As in sw.test.ts: a warning on the run, where a skipped count says nothing of why.
    await annotate(
      "The kill switch is fired: web/public/sw.js is sw.kill.js byte for byte, so sw-badge.test.ts's tests of the real worker stand down until the firing commit is reverted.",
      "warning",
    );
  });
}

/** What `build_payload()` sends for a notification that names no action. */
const NOTICE = { title: "Bins go out tonight", body: "Friday.", urgency: "important", id: "n-7", action_id: null };

/** A worker that keeps a count on a store that works, with nothing racing it: see `clockHeld`. */
const counting = (options: LoadOptions = {}) =>
  loadWorker(source, { indexedDB: new IDBFactory(), appBadge: "sets", clockHeld: true, ...options });

/** The deadline on a push's count, restated here so that a change to it is a change to this. */
const BADGE_TIMEOUT_MS = 5000;

/** A store that takes the open and never answers it. */
const hung: Pick<IDBFactory, "open"> = { open: () => ({}) as IDBOpenDBRequest };

/** The count's database at the worker's own version, as `upgrade` leaves it. */
const opened = (factory: IDBFactory, upgrade: (database: IDBDatabase) => void = () => {}) =>
  new Promise<IDBDatabase>((resolve, reject) => {
    const opening = factory.open("alfred-badge", 1);
    opening.onupgradeneeded = () => upgrade(opening.result);
    opening.onerror = () => reject(opening.error);
    opening.onsuccess = () => resolve(opening.result);
  });

/**
 * A store already holding `value`. Put there through the factory rather than a
 * seam, so what the worker then reads is what a phone's disk would hand it.
 */
const holding = async (value: unknown) => {
  const factory = new IDBFactory();
  const database = await opened(factory, (upgrading) => upgrading.createObjectStore("count"));
  await new Promise<void>((resolve, reject) => {
    const writing = database.transaction("count", "readwrite");
    writing.objectStore("count").put(value, "pushes");
    writing.oncomplete = () => resolve();
    writing.onabort = () => reject(writing.error);
  });
  database.close();
  return factory;
};

/** Whether a later version of the database would have to wait behind a connection left open. */
const upgradeIsBlocked = (factory: IDBFactory) =>
  new Promise<boolean>((resolve, reject) => {
    const opening = factory.open("alfred-badge", 2);
    opening.onblocked = () => resolve(true);
    opening.onerror = () => reject(opening.error);
    opening.onsuccess = () => {
      opening.result.close();
      resolve(false);
    };
  });

describe.skipIf(FIRED)("the icon's count", () => {
  it("adds one per push", async () => {
    const worker = counting();
    await worker.push(NOTICE);
    await worker.push(NOTICE);
    expect(worker.badges).toEqual([1, 2]);
    expect(worker.shown).toHaveLength(2);
  });

  it("counts two pushes at once as two", async () => {
    // One read-write transaction each, so the second reads what the first wrote.
    // A read and a write apart would both read nothing, and both write 1.
    const worker = counting();
    await Promise.all([worker.push(NOTICE), worker.push(NOTICE)]);
    expect([...worker.badges].sort()).toEqual([1, 2]);
  });

  it("counts nothing where setAppBadge is absent", async () => {
    // Not even a database left behind on a browser that could never show it.
    const factory = new IDBFactory();
    const worker = loadWorker(source, { indexedDB: factory });
    await worker.push(NOTICE);
    expect(worker.shown).toHaveLength(1);
    expect(worker.posted).toEqual([]);
    await expect(factory.databases()).resolves.toEqual([]);
  });

  it("shows the notification when the store throws", async () => {
    const worker = counting({
      indexedDB: {
        open() {
          throw new DOMException("x", "UnknownError");
        },
      },
    });
    await worker.push(NOTICE);
    expect(worker.shown).toHaveLength(1);
    expect(worker.badges).toEqual([]);
    expect(worker.posted).toEqual([]);
  });

  it("shows the notification when the store errors", async () => {
    const worker = counting({
      indexedDB: {
        open() {
          const request = { error: new DOMException("x", "UnknownError") } as unknown as IDBOpenDBRequest;
          setTimeout(() => request.onerror?.(new Event("error")), 0);
          return request;
        },
      },
    });
    await worker.push(NOTICE);
    expect(worker.shown).toHaveLength(1);
    expect(worker.badges).toEqual([]);
    expect(worker.posted).toEqual([]);
  });

  it("shows the notification when the database has no store to count in", async () => {
    // A throw inside the open's own handler, where a rejection has to be made by
    // hand. The clock is held, so a worker that only had its deadline to fall
    // back on would hang here rather than pass.
    const factory = new IDBFactory();
    (await opened(factory)).close();
    const worker = counting({ indexedDB: factory });
    await worker.push(NOTICE);
    expect(worker.shown).toHaveLength(1);
    expect(worker.badges).toEqual([]);
    expect(worker.posted).toEqual([]);
    await expect(upgradeIsBlocked(factory)).resolves.toBe(false);
  });

  it("gives up on a write that does not land, and closes the database", async () => {
    // Scripted, because nothing can make a real store refuse a number. The put
    // succeeds and then the transaction fails, so the number was never written,
    // and nothing may be shown for it. The clock is held, so a worker that did
    // not hear the ending would hang here.
    for (const ending of ["onerror", "onabort"] as const) {
      const close = vi.fn();
      const reading: { onsuccess?: () => void } = {};
      const putting: { onsuccess?: () => void } = {};
      const writing: { objectStore(): unknown; onerror?: () => void; onabort?: () => void } = {
        objectStore: () => ({
          get() {
            setTimeout(() => reading.onsuccess?.(), 0);
            return reading;
          },
          put() {
            setTimeout(() => {
              putting.onsuccess?.();
              writing[ending]?.();
            }, 0);
            return putting;
          },
        }),
      };
      const opening: { result: unknown; onsuccess?: () => void } = {
        result: { close, transaction: () => writing },
      };
      const worker = counting({
        indexedDB: {
          open() {
            setTimeout(() => opening.onsuccess?.(), 0);
            return opening as unknown as IDBOpenDBRequest;
          },
        },
      });
      await worker.push(NOTICE);
      expect(worker.shown, ending).toHaveLength(1);
      expect(worker.badges, ending).toEqual([]);
      expect(worker.posted, ending).toEqual([]);
      expect(close, ending).toHaveBeenCalled();
    }
  });

  it("closes the database once it has counted", async () => {
    const factory = new IDBFactory();
    const worker = counting({ indexedDB: factory });
    await worker.push(NOTICE);
    expect(worker.badges).toEqual([1]);
    await expect(upgradeIsBlocked(factory)).resolves.toBe(false);
  });

  it("gives up on a store that hangs", async () => {
    const worker = counting({ indexedDB: hung, clockHeld: false });
    await worker.push(NOTICE);
    expect(worker.shown).toHaveLength(1);
    expect(worker.deadlines).toEqual([BADGE_TIMEOUT_MS]);
    expect(worker.posted).toEqual([]);
  });

  it("does not hold the notification behind a hung store", async () => {
    // Shown while the store is still hung and the deadline has not fired, so the
    // notification is waiting on nothing of the count's.
    const worker = counting({ indexedDB: hung });
    const pushed = worker.push(NOTICE);
    await vi.waitFor(() => expect(worker.shown).toHaveLength(1));
    worker.releaseClock();
    await pushed;
  });

  it("tells every open window after the badge is set", async () => {
    // After, because a page on screen answers this by clearing the badge: told
    // before, it would clear a number that then arrives and stays.
    const worker = counting({ clients: [{ path: "/" }, { path: "/actions/abc" }] });
    await worker.push(NOTICE);
    expect(worker.events).toEqual(["badge 1", `post ${ORIGIN}/`, `post ${ORIGIN}/actions/abc`]);
    expect(worker.posted.map(({ data }) => data)).toEqual([{ type: "BADGE_ADDED" }, { type: "BADGE_ADDED" }]);
  });

  it("clears its deadline", async () => {
    const worker = counting();
    await worker.push(NOTICE);
    expect(worker.deadlines).toHaveLength(1);
    expect(worker.deadlinesCleared).toBe(worker.deadlines.length);
  });

  it("counts a push it cannot read", async () => {
    // Both are in the shade as `Alfred`, so both are on the icon, or the number
    // under-counts what the owner can see.
    const worker = counting();
    await worker.push(undefined, { raw: "{not json" });
    await worker.push();
    expect(worker.badges).toEqual([1, 2]);
    expect(worker.shown.map(({ title }) => title)).toEqual(["Alfred", "Alfred"]);
  });

  it("treats junk in the store as zero", async () => {
    for (const junk of ["junk", -3, 1.5]) {
      const worker = counting({ indexedDB: await holding(junk) });
      await worker.push(NOTICE);
      expect(worker.badges, String(junk)).toEqual([1]);
    }
  });

  it("tells the windows when setAppBadge rejects", async () => {
    // The count is written by then, so the page has something to clear.
    const worker = counting({ appBadge: "rejects" });
    await worker.push(NOTICE);
    expect(worker.shown).toHaveLength(1);
    expect(worker.posted).toHaveLength(1);
    await worker.push(NOTICE);
    expect(worker.badges).toEqual([1, 2]);
  });

  it("gives up on a setAppBadge that hangs", async () => {
    const worker = counting({ appBadge: "hangs" });
    const pushed = worker.push(NOTICE);
    await vi.waitFor(() => expect(worker.badges).toEqual([1]));
    worker.releaseClock();
    await pushed;
    expect(worker.shown).toHaveLength(1);
    // Windows are told once it has answered, and it never did.
    expect(worker.posted).toEqual([]);
  });
});

/**
 * The pin between worker and page. sw.js is hand-written and not bundled, so
 * lib/badge.ts repeats its database, store and key by name, and its open and
 * upgrade by hand. A name that drifts on either side fails here: the page's clear
 * lands somewhere the worker never reads, or the worker cannot read what the page
 * made.
 */
describe.skipIf(FIRED)("the page and the worker share a count", () => {
  /** The page, on the worker's store, in a browser that can badge. */
  function pageOn(factory: IDBFactory): void {
    vi.stubGlobal("indexedDB", factory);
    for (const name of ["setAppBadge", "clearAppBadge"]) {
      const value = vi.fn(() => Promise.resolve());
      Object.defineProperty(navigator, name, { configurable: true, value });
    }
  }

  afterEach(() => {
    Reflect.deleteProperty(navigator, "setAppBadge");
    Reflect.deleteProperty(navigator, "clearAppBadge");
  });

  it("the page's clear zeroes the worker's count", async () => {
    const factory = new IDBFactory();
    pageOn(factory);
    const worker = counting({ indexedDB: factory });
    await worker.push(NOTICE);
    await worker.push(NOTICE);
    await clearBadge();
    await worker.push(NOTICE);
    expect(worker.badges).toEqual([1, 2, 1]);
  });

  it("the worker reads a database the page created", async () => {
    const factory = new IDBFactory();
    pageOn(factory);
    await clearBadge();
    const worker = counting({ indexedDB: factory });
    await worker.push(NOTICE);
    expect(worker.badges).toEqual([1]);
  });
});
