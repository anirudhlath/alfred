import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { badgeSupported, clearBadge, onBadgeAdded } from "./badge";

// jsdom has no Badging API, no service worker and no IndexedDB. The first two
// are defined on `navigator` per test and deleted afterwards, as in push.test.ts;
// `indexedDB` is a window global, which `vi.stubGlobal` stands in for.

/** A browser that can badge, whose `clearAppBadge` does as `clear` says. */
function badging(clear: () => Promise<void> = () => Promise.resolve()) {
  const clearAppBadge = vi.fn(clear);
  Object.defineProperty(navigator, "setAppBadge", { configurable: true, value: vi.fn() });
  Object.defineProperty(navigator, "clearAppBadge", { configurable: true, value: clearAppBadge });
  return clearAppBadge;
}

/**
 * A store that opens, and whose write ends as `ending` says. Scripted, because
 * nothing can make a real store refuse a number. `throws` is what `transaction()`
 * does on a database with no such store.
 */
function scripted(ending: "oncomplete" | "onerror" | "onabort" | "throws") {
  const close = vi.fn();
  const put = vi.fn(() => setTimeout(() => writing[ending]?.(), 0));
  const writing: Partial<Record<typeof ending, () => void>> & { objectStore(): unknown } = {
    objectStore: () => ({ put }),
  };
  const opening: { result: unknown; onsuccess?: () => void } = {
    result: {
      close,
      transaction() {
        if (ending === "throws") throw new DOMException("x", "NotFoundError");
        return writing;
      },
    },
  };
  vi.stubGlobal("indexedDB", {
    open() {
      setTimeout(() => opening.onsuccess?.(), 0);
      return opening;
    },
  });
  return { close, put };
}

beforeEach(() => {
  vi.stubGlobal("indexedDB", new IDBFactory());
});

afterEach(() => {
  Reflect.deleteProperty(navigator, "setAppBadge");
  Reflect.deleteProperty(navigator, "clearAppBadge");
  Reflect.deleteProperty(navigator, "serviceWorker");
});

describe("badgeSupported", () => {
  it("is whether the browser has setAppBadge", () => {
    expect(badgeSupported()).toBe(false);
    badging();
    expect(badgeSupported()).toBe(true);
  });
});

describe("clearBadge", () => {
  it("does nothing where setAppBadge is absent", async () => {
    const open = vi.spyOn(indexedDB, "open");
    await clearBadge();
    expect(open).not.toHaveBeenCalled();
  });

  it("clears the icon when the store hangs", () => {
    const clearAppBadge = badging();
    vi.stubGlobal("indexedDB", { open: () => ({}) });
    // Not awaited: it has no time limit of its own, so here it never settles.
    void clearBadge();
    expect(clearAppBadge).toHaveBeenCalledTimes(1);
  });

  it("never rejects", async () => {
    vi.stubGlobal("indexedDB", {
      open() {
        throw new DOMException("x", "UnknownError");
      },
    });
    const clearAppBadge = badging(() => Promise.reject(new Error("refused")));
    await expect(clearBadge()).resolves.toBeUndefined();
    expect(clearAppBadge).toHaveBeenCalledTimes(1);

    // Nor where the call itself throws, rather than answering with a rejection.
    badging(() => {
      throw new Error("thrown");
    });
    await expect(clearBadge()).resolves.toBeUndefined();
  });

  it("writes zero to the store when the icon refuses", async () => {
    badging(() => Promise.reject(new Error("refused")));
    const { put } = scripted("oncomplete");
    await clearBadge();
    expect(put).toHaveBeenCalledWith(0, "pushes");
  });

  it.each(["oncomplete", "onerror", "onabort", "throws"] as const)(
    "settles and closes the database when the write ends with %s",
    async (ending) => {
      badging();
      const { close } = scripted(ending);
      await clearBadge();
      expect(close).toHaveBeenCalled();
    },
  );

  it("settles when the store will not open", async () => {
    badging();
    vi.stubGlobal("indexedDB", {
      open() {
        const opening: { error: DOMException; onerror?: () => void } = {
          error: new DOMException("x", "UnknownError"),
        };
        setTimeout(() => opening.onerror?.(), 0);
        return opening;
      },
    });
    await expect(clearBadge()).resolves.toBeUndefined();
  });
});

describe("onBadgeAdded", () => {
  it("hears BADGE_ADDED only", () => {
    const container = new EventTarget();
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: container });
    const fn = vi.fn();
    const off = onBadgeAdded(fn);
    const say = (data: unknown) => container.dispatchEvent(new MessageEvent("message", { data }));

    say({ type: "OPEN_PATH", path: "/" });
    say({ type: "X" });
    say(null);
    say(7);
    say("BADGE_ADDED");
    expect(fn).not.toHaveBeenCalled();

    say({ type: "BADGE_ADDED" });
    expect(fn).toHaveBeenCalledTimes(1);

    off();
    say({ type: "BADGE_ADDED" });
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("does nothing where serviceWorker is absent", () => {
    const listening = vi.spyOn(EventTarget.prototype, "addEventListener");
    const fn = vi.fn();
    const off = onBadgeAdded(fn);
    window.dispatchEvent(new MessageEvent("message", { data: { type: "BADGE_ADDED" } }));
    expect(listening).not.toHaveBeenCalled();
    expect(fn).not.toHaveBeenCalled();
    off();
  });
});
