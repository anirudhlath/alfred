import { describe, expect, it } from "vitest";
import { fetchRequest, html, loadWorker, ORIGIN } from "./sw-harness";

// The harness is the only thing standing between "this worker's tests pass" and
// "this worker works", and eight tasks' tests are written against it. Where it
// models a real ServiceWorkerGlobalScope loosely it does not merely lose
// coverage — it can pass a worker that cannot install on any phone. These are
// the places that has to be true, pinned here so the next edit cannot quietly
// undo one.

describe("the fake scope's event semantics", () => {
  it("runs every listener registered for a type, not only the last", async () => {
    const worker = loadWorker(`
      self.addEventListener("install", () => { self.caches.open("alfred-first"); });
      self.addEventListener("install", () => { self.caches.open("alfred-second"); });
    `);
    await worker.install();
    expect([...worker.store.keys()]).toEqual(["alfred-first", "alfred-second"]);
  });

  it("waits on every promise an event was extended with, not only the last", async () => {
    // A real ExtendableEvent is not done until all of them settle. Keeping only
    // the last would let `waitUntil(slow); waitUntil(fast)` resolve early, and
    // task 5's activate is `purge().then(() => claim())` with more to come.
    const worker = loadWorker(`
      self.addEventListener("activate", (event) => {
        event.waitUntil(new Promise((resolve) => self.setTimeout(() => {
          self.caches.open("alfred-slow");
          resolve();
        }, 5)));
        event.waitUntil(Promise.resolve());
      });
    `);
    await worker.activate();
    expect([...worker.store.keys()]).toEqual(["alfred-slow"]);
  });

  it("surfaces a rejection from any of them, not only the last", async () => {
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil(Promise.reject(new Error("first")));
        event.waitUntil(Promise.resolve());
      });
    `);
    await expect(worker.install()).rejects.toThrow("first");
  });

  it("refuses a second respondWith for one fetch event", async () => {
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => {
        event.respondWith(Promise.resolve(new Response("one")));
        event.respondWith(Promise.resolve(new Response("two")));
      });
    `);
    await expect(worker.request({ path: "/" })).rejects.toThrow("respondWith called twice");
  });
});

describe("the fake CacheStorage", () => {
  it("answers every match with a fresh body, as a real Cache does", async () => {
    // Real Cache.match constructs a new Response per call; handing back the
    // stored object instead leaves it consumed the moment anything reads it.
    // Task 5's purge reads the shell manifest with .json(), so a harness that
    // did that would let one activate eat the record the NEXT one prunes
    // against — and every assertion about the manifest after an activate would
    // be reading a corpse rather than the cache.
    const worker = loadWorker(`
      self.addEventListener("activate", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          await cache.put("/x", new Response("once"));
          const first = await (await cache.match("/x")).text();
          const second = await (await cache.match("/x")).text();
          await cache.put("/read-twice", new Response(first + second));
        })());
      });
    `);
    await worker.activate();
    const entries = worker.store.get("alfred-shell-v1")!;
    await expect(entries.get(`${ORIGIN}/read-twice`)!.clone().text()).resolves.toBe("onceonce");
    // And the stored entry is still readable itself, which is the other half of
    // what clone() means: a match must not spend the cache's copy.
    await expect(entries.get(`${ORIGIN}/x`)!.clone().text()).resolves.toBe("once");
  });

  it("does not delete until the promise it handed back has settled", async () => {
    // A real delete goes to disk, so a missing `await` means the deletion is
    // outside waitUntil: the event finishes, the browser is free to kill the
    // worker, and the cache the purge was supposed to drop is still there.
    // Modelled as a macrotask so that is visible here and not only on a phone.
    const worker = loadWorker(`
      self.addEventListener("activate", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          await cache.put("/x", new Response("x"));
          self.caches.delete("alfred-gone");
          cache.delete("/x");
        })());
      });
    `);
    worker.store.set("alfred-gone", new Map());
    await worker.activate();
    expect([...worker.store.keys()]).toContain("alfred-gone");
    expect(worker.keys("alfred-shell-v1")).toContain("/x");
  });

  it("can be told to refuse a delete, which is the only way a worker's catch is reachable", async () => {
    // Nothing else in this fake can produce a rejecting delete, so without it a
    // worker with no per-delete `catch` — one transient failure and neither
    // purge finishes — passes the whole suite.
    const worker = loadWorker(`
      self.addEventListener("activate", (event) => {
        event.waitUntil(self.caches.delete("alfred-gone"));
      });
    `);
    worker.store.set("alfred-gone", new Map());
    worker.refuseDelete("alfred-gone");
    await expect(worker.activate()).rejects.toThrow(/storage error/);
    expect([...worker.store.keys()]).toEqual(["alfred-gone"]);
  });

  it("rejects a put of a response whose body has already been read", async () => {
    // Real Cache.put does. Without this, `await shell.text()` in place of
    // `await shell.clone().text()` is invisible here and fatal in a browser:
    // a worker that can never install, clearable only by the kill switch.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          const response = await self.fetch("/x");
          await response.text();
          await cache.put("/x", response);
        })());
      });
    `);
    worker.respond("/x", html("<!doctype html>"));
    await expect(worker.install()).rejects.toThrow(/body is already used/);
  });
});

describe("the fake Clients", () => {
  const claimer = (before: boolean) =>
    loadWorker(`
      self.addEventListener("activate", (event) => {
        event.waitUntil((async () => {
          ${before ? "await self.clients.claim();" : ""}
          for (const client of await self.clients.matchAll({ type: "window" })) {
            await client.navigate(client.url);
          }
        })());
      });
    `);

  it("shows no client to a worker that has not claimed one", async () => {
    // matchAll() sees only the clients this worker CONTROLS. A kill switch that
    // reloads the open pages before claiming them reloads nothing at all, and
    // a harness that always answers one client would call that a pass.
    const worker = claimer(false);
    await worker.activate();
    expect(worker.navigated).toEqual([]);
  });

  it("shows it once the worker has claimed", async () => {
    const worker = claimer(true);
    await worker.activate();
    expect(worker.navigated).toEqual([`${ORIGIN}/`]);
  });

  it("shows it without a claim when asked for uncontrolled clients", async () => {
    const worker = loadWorker(`
      self.addEventListener("activate", (event) => {
        event.waitUntil((async () => {
          for (const client of await self.clients.matchAll({ includeUncontrolled: true })) {
            await client.navigate(client.url);
          }
        })());
      });
    `);
    await worker.activate();
    expect(worker.navigated).toEqual([`${ORIGIN}/`]);
  });
});

describe("the seams the harness depends on", () => {
  it("refuses a global reached bare rather than letting it hit Node's", async () => {
    // `self.` is a convention only because this enforces it. Node's own fetch
    // is a live network call, and a worker that slipped past the fake scope
    // would be tested against the internet.
    const worker = loadWorker(`
      self.addEventListener("install", () => { fetch("/x"); });
    `);
    await expect(worker.install()).rejects.toThrow(/reached bare/);
  });

  it("does not answer a cross-origin URL from a same-origin route", async () => {
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil(self.fetch("https://cdn.example.invalid/assets/x.js"));
      });
    `);
    worker.respond("/assets/x.js", html("<!doctype html>"));
    await expect(worker.install()).rejects.toThrow(/no route for https:\/\/cdn\.example\.invalid/);
  });

  it("names the cache from the worker's own VERSION", async () => {
    // Not a second copy of the string: a VERSION bump is exactly what task 5's
    // purge exists for, and it must move the tests rather than make them
    // vacuous. The `alfred-` prefix stays restated — that one IS the invariant.
    expect(loadWorker('const VERSION = "v7";').cache).toBe("alfred-shell-v7");
  });

  it("refuses to guess a cache for a worker that declares no version", () => {
    // sw.kill.js owns no cache; it deletes all of them.
    expect(() => loadWorker("/* no version here */").cache).toThrow(/declares no VERSION/);
  });

  it("hands back a file-local function by the name the worker declares it under", () => {
    // The only way into a classic worker's internals, and the whole of what
    // makes `isRefused` testable on the half that matters: the requests it must
    // NOT refuse, which are indistinguishable from unhandled ones from outside.
    const worker = loadWorker('function isRefused(request) { return request.method !== "GET"; }', {
      expose: ["isRefused"],
    });
    expect(worker.inner.isRefused!(fetchRequest({ path: "/", method: "POST" }))).toBe(true);
    expect(worker.inner.isRefused!(fetchRequest({ path: "/" }))).toBe(false);
  });

  it("refuses a name that is not on the allowlist", () => {
    // `expose` splices an identifier into evaluated source, and the argument for
    // reaching in at all is specific to `isRefused` — `precache` and `purge`
    // both already have branches that drive them. The allowlist is where that
    // judgement lives rather than in a doc comment nothing enforces.
    expect(() =>
      loadWorker('const VERSION = "v1";', {
        expose: ["precache" as unknown as "isRefused"],
      }),
    ).toThrow(/not an exposable worker internal/);
  });

  it("evaluates the exposed worker's listeners exactly as an unexposed one's", async () => {
    // The tail is appended to the shipped bytes, so the source that registers
    // the listeners has to still be the source that ships. A `return` that
    // landed anywhere but last would cut the file short and take them with it.
    const worker = loadWorker(
      `function isRefused(request) { return Boolean(request); }
       self.addEventListener("install", () => { self.caches.open("alfred-still-here"); });`,
      { expose: ["isRefused"] },
    );
    await worker.install();
    expect([...worker.store.keys()]).toEqual(["alfred-still-here"]);
  });

  it("refuses a name the worker does not declare", () => {
    // Red the moment sw.js renames or deletes the function, which is the point:
    // a test reaching for a guard that is gone must not quietly reach nothing.
    expect(() => loadWorker('const VERSION = "v1";', { expose: ["isRefused"] })).toThrow(
      /isRefused is not defined/,
    );
  });

  it("resolves a scoped worker's bare path against its own origin", async () => {
    // Otherwise a scoped test that forgets `origin` sends a CROSS-origin request
    // and `resolves.toBeUndefined()` passes because the worker refused another
    // origin, not for the reason the test names.
    const worker = loadWorker(
      `self.addEventListener("fetch", (event) => {
         event.respondWith(Promise.resolve(new Response(event.request.url)));
       });`,
      { scope: "http://localhost:8081/" },
    );
    const response = await worker.request({ path: "/assets/x.js" });
    await expect(response!.text()).resolves.toBe("http://localhost:8081/assets/x.js");
  });

  it("carries a request's headers to the worker, case-insensitively", async () => {
    // Real `Headers`, not a plain object: a worker asks for "range" and a
    // browser sends "Range", and a fake that could not tell them apart would
    // pass a guard that never fires on a phone.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => {
        if (event.request.headers.get("range")) {
          event.respondWith(Promise.resolve(new Response("ranged")));
        }
      });
    `);
    const response = await worker.request({
      path: "/assets/x.js",
      headers: { Range: "bytes=0-1023" },
    });
    await expect(response!.text()).resolves.toBe("ranged");
  });
});
