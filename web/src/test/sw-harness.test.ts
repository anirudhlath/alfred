import { describe, expect, it } from "vitest";
import { fetchRequest, html, js, loadWorker, ORIGIN, stalled } from "./sw-harness";

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

  it("waits on work a fetch event was extended with, including after an await", async () => {
    // A FetchEvent is an ExtendableEvent, and the handler that needs it arms its
    // `waitUntil` only once the network has answered — long after the listener
    // returned, while `respondWith`'s own promise is still holding the event
    // open. A harness that snapshotted the list when the listener returned would
    // wait on nothing and read every cache before it was written.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => {
        event.respondWith((async () => {
          const response = await self.fetch("/x");
          event.waitUntil((async () => {
            const cache = await self.caches.open("alfred-shell-v1");
            await cache.put("/late", new Response("late"));
          })());
          return response;
        })());
      });
    `);
    worker.respond("/x", html("<!doctype html>"));
    await worker.request({ path: "/x" });
    expect(worker.keys("alfred-shell-v1")).toEqual(["/late"]);
  });

  it("surfaces a rejection that escapes a fetch event's waitUntil", async () => {
    // A browser logs it and carries on serving, which is why a worker that lets
    // one escape looks fine on a phone until the day it matters. Here it is a
    // defect, so it is raised where a test can see it.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => {
        event.respondWith(Promise.resolve(new Response("served")));
        event.waitUntil(Promise.reject(new Error("background")));
      });
    `);
    await expect(worker.request({ path: "/x" })).rejects.toThrow("background");
  });

  it("reports the response's own rejection, not one from unrelated background work", async () => {
    // Both halves fail, and only one of them is what the test asked about. A
    // test that pins a navigation's error — "offline, and no shell cached" — is
    // about the answer the PAGE got; letting a background rejection replace it
    // means that test can be failed, or satisfied, by work that has nothing to
    // do with the page. The background one is still `dispatch().settled`'s to
    // report, for a test that wants it.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => {
        event.respondWith(Promise.reject(new Error("response")));
        event.waitUntil(Promise.reject(new Error("background")));
      });
    `);
    await expect(worker.request({ path: "/x" })).rejects.toThrow("response");
    await expect(worker.dispatch({ path: "/x" }).settled).rejects.toThrow("background");
  });

  it("still lets the background half finish when the response rejects", async () => {
    // Reporting the response's rejection must not mean abandoning the event:
    // work the handler handed to `waitUntil` before the failure is part of
    // what the event did, and a test asserting on it right after the
    // rejection must see it landed, not racing the next line.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          await cache.put("/late", new Response("late"));
        })());
        event.respondWith(Promise.reject(new Error("response")));
      });
    `);
    await expect(worker.request({ path: "/x" })).rejects.toThrow("response");
    expect(worker.keys("alfred-shell-v1")).toEqual(["/late"]);
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

  it("does not put until the promise it handed back has settled", async () => {
    // The same reason deletes are modelled this way: a real put goes to disk,
    // so a worker that does not await one has left the write outside its
    // handler's lifetime, and the browser may stop the worker before it lands.
    // A microtask would make the missing `await` invisible; a macrotask does
    // not.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          cache.put("/x", new Response("x"));
        })());
      });
    `);
    await worker.install();
    expect(worker.keys("alfred-shell-v1")).toEqual([]);
  });

  it("can be told to refuse a put, which is what a phone that is full does", async () => {
    // `QuotaExceededError`, and it is the axis a worker is least likely to be
    // written for: a put is a disk write on a device whose storage the user does
    // not manage. Without this seam, a handler that treats a failed WRITE as a
    // failed FETCH — and so answers an online phone out of its cache — passes
    // every test in the suite.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          await cache.put("/x", new Response("x"));
        })());
      });
    `);
    worker.refusePut("/x");
    await expect(worker.install()).rejects.toThrow(/storage error/);
    expect(worker.keys("alfred-shell-v1")).toEqual([]);
  });

  it("rejects a put of a 206, as the Cache API does", async () => {
    // `response.ok` is 200–299, so a worker that stores on `ok` alone reaches
    // this — and in a browser it is a TypeError from a line that reads as though
    // it cannot fail.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          await cache.put("/x", new Response("part", { status: 206 }));
        })());
      });
    `);
    await expect(worker.install()).rejects.toThrow(/206/);
  });

  it("can be told to refuse to open at all, which is a store that is gone", async () => {
    // Safari drops the whole CacheStorage under storage pressure and in some
    // private modes. Without this, every `caches.open()` in the worker is
    // assumed infallible, and a handler that opens outside its try answers a
    // healthy network with a network error.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil(self.caches.open("alfred-shell-v1"));
      });
    `);
    worker.refuseOpen();
    await expect(worker.install()).rejects.toThrow(/storage error/);
  });

  it("can be told to refuse a match, which is the read half of the same failure", async () => {
    // The disk, not the lookup. Without it, every `.catch` on a cache read is
    // unreachable code, and a worker whose offline path rejects with a
    // DOMException rather than falling back passes.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => {
        event.respondWith((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          return (await cache.match("/x")) || new Response("fell through");
        })());
      });
    `);
    worker.store.set("alfred-shell-v1", new Map([[`${ORIGIN}/x`, html("<!doctype html>")]]));
    worker.refuseMatch("/x");
    await expect(worker.request({ path: "/x" })).rejects.toThrow(/storage error/);
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

  it("reads the body it is handed to the end, and spends it, as a real put does", async () => {
    // Real Cache.put reads the response's body to completion and only then
    // writes, so the Response a worker hands it is spent afterwards — which is
    // why a handler that puts the page's own response instead of a clone breaks
    // the page. A fake that filed the object away unread got both halves wrong.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          const response = await self.fetch("/x.js");
          await cache.put("/x.js", response);
          await cache.put("/spent", new Response(String(response.bodyUsed)));
        })());
      });
    `);
    worker.respond("/x.js", js("console.log(1)"));
    await worker.install();
    const entries = worker.store.get("alfred-shell-v1")!;
    await expect(entries.get(`${ORIGIN}/spent`)!.clone().text()).resolves.toBe("true");
    // And what it stored is the bytes and the headers, readable as often as a
    // test likes.
    const stored = entries.get(`${ORIGIN}/x.js`)!;
    await expect(stored.clone().text()).resolves.toBe("console.log(1)");
    expect(stored.headers.get("content-type")).toBe("text/javascript");
  });

  it("stores a response with no body as one with no body, status and all", async () => {
    // A 204 is a legal thing to put — only a 206 is refused — and a Response
    // with that status cannot even be CONSTRUCTED with a body, empty or not.
    // Modelled, so that a worker whose guard let one through is caught storing
    // it rather than by a TypeError the fake invented.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          await cache.put("/empty.js", new Response(null, { status: 204 }));
        })());
      });
    `);
    await worker.install();
    const stored = worker.store.get("alfred-shell-v1")!.get(`${ORIGIN}/empty.js`)!;
    expect(stored.status).toBe(204);
    expect(stored.body).toBeNull();
  });

  it("never finishes a put whose body never finishes arriving", async () => {
    // The other half of reading the body: a put of a network response is a
    // BODY READ, with every hazard of one. fetch() resolves on the headers, so a
    // route that stalls mid-body hands the worker a 200 whose put waits for
    // ever — measured in Chromium, an install left "installing" for minutes on
    // exactly this. A fake that stored the object unread made that put look
    // instant, and four puts in sw.js took a network body with no deadline.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          await cache.put("/x.js", await self.fetch("/x.js"));
        })());
      });
    `);
    worker.respond("/x.js", stalled("text/javascript"));
    // Not `resolves`/`rejects`: the assertion is that it does neither.
    const outcome = await Promise.race([
      worker.install().then(() => "settled"),
      new Promise((resolve) => setTimeout(() => resolve("pending"), 20)),
    ]);
    expect(outcome).toBe("pending");
    expect(worker.puts).toEqual(["/x.js"]);
    expect(worker.keys("alfred-shell-v1")).toEqual([]);
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

describe("the fake registration", () => {
  /**
   * Writes what `self.registration.active` looked like at install and at
   * activate into a cache of its own, as `/<event>-<state>`, because a cache
   * key is the one thing a worker can leave behind that a test can read.
   */
  const probe = `
    const seen = async (event) => {
      const active = self.registration.active;
      const cache = await self.caches.open("probe");
      await cache.put("/" + event + "-" + (active === null ? "null" : active.state), new Response(""));
    };
    self.addEventListener("install", (event) => event.waitUntil(seen("install")));
    self.addEventListener("activate", (event) => event.waitUntil(seen("activate")));
  `;

  it("has no active worker on a first install, as a browser's does not", async () => {
    // null, not undefined: that is what a real registration holds, and a
    // worker comparing against one must not pass here and fail on a phone.
    const worker = loadWorker(probe);
    await worker.install();
    expect(worker.keys("probe")).toEqual(["/install-null"]);
  });

  it("has the older worker active while an update installs", async () => {
    // Decision 7's case, and the one a first install cannot reach: a worker
    // that skips waiting only when there is someone to take over from is
    // invisible to a suite that never installs over anything.
    const worker = loadWorker(probe, { update: true });
    await worker.install();
    expect(worker.keys("probe")).toEqual(["/install-activated"]);
  });

  it("makes the activating worker the active one, whatever was there before", async () => {
    // By the time the activate event fires, the registration's active worker
    // IS the one activating. A fake that still answered with the older worker
    // would describe a registration no browser has.
    const worker = loadWorker(probe, { update: true });
    await worker.install();
    await worker.activate();
    expect(worker.keys("probe")).toEqual(["/activate-activating", "/install-activated"]);
  });
});

describe("the fake lifecycle calls", () => {
  it("hands back a promise from skipWaiting, as the real one does", async () => {
    // A worker that chains on it — `skipWaiting().then(…)` inside waitUntil is
    // the common shape — works in a browser; a fake returning undefined would
    // fail it with a TypeError that has nothing to do with the worker.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil(self.skipWaiting().then(() => self.caches.open("after")));
      });
    `);
    await worker.install();
    expect(worker.skipWaitingCalls).toBe(1);
    expect([...worker.store.keys()]).toEqual(["after"]);
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

  it("gives a navigation the destination a browser gives it", async () => {
    // A browser sets mode and destination together; a fixture that says one
    // without the other is not a request any page makes. The app's own
    // /assets/* requests are (cors, script|style|font), which is what the worker
    // tests send.
    expect(fetchRequest({ path: "/", mode: "navigate" }).destination).toBe("document");
    expect(fetchRequest({ path: "/assets/x.js" }).destination).toBe("");
    expect(fetchRequest({ path: "/assets/x.js", mode: "cors", destination: "script" })).toMatchObject(
      { mode: "cors", destination: "script" },
    );
  });

  it("records what self.fetch was handed, not merely which URL it named", async () => {
    // Forwarding the Request is what keeps the page's mode, credentials and
    // redirect mode; re-fetching `request.url` turns `redirect: "manual"` into
    // `"follow"`, which no assertion about a body can see.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => {
        event.respondWith(self.fetch(event.request));
      });
    `);
    worker.respond("/assets/x.js", html("<!doctype html>"));
    await worker.request({ path: "/assets/x.js", mode: "cors", destination: "script" });
    expect(worker.handed[0]).toMatchObject({ mode: "cors", destination: "script" });
    expect(worker.fetched).toEqual(["/assets/x.js"]);
  });

  it("records the init beside it, including the absence of one", async () => {
    // The half `handed` cannot show. A worker that forwards the Request AND
    // attaches a signal has handed over the same object as one that forwards it
    // untouched — but the non-empty init makes fetch() rebuild the Request, and
    // the rebuild demotes `mode: "navigate"` to `"same-origin"` and clears the
    // reload- and history-navigation flags. Nothing about the input records
    // that; the init is the only witness.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => {
        if (event.request.mode === "navigate") { event.respondWith(self.fetch(event.request)); return; }
        event.respondWith(self.fetch(event.request, { cache: "reload" }));
      });
    `);
    worker.respond("/", html("<!doctype html>"));
    worker.respond("/assets/x.js", js("1"));
    await worker.request({ path: "/", mode: "navigate" });
    await worker.request({ path: "/assets/x.js", mode: "cors", destination: "script" });
    expect(worker.inits).toEqual([undefined, { cache: "reload" }]);
  });

  it("records a put that was attempted and failed, not only one that landed", async () => {
    // `keys()` answers what is in the cache, which is the same for a worker
    // that declined a doomed write and one that attempted it and swallowed the
    // failure. Every attempt is recorded before anything can refuse it, which
    // is what lets a test tell those two apart.
    const worker = loadWorker(`
      self.addEventListener("message", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("c");
          await cache.put("/a.js", new Response("a")).catch(() => {});
          await cache.put("/b.js", new Response("b")).catch(() => {});
        })());
      });
    `);
    worker.refusePut("/b.js");
    await worker.message(null);
    expect(worker.puts).toEqual(["/a.js", "/b.js"]);
    expect(worker.keys("c")).toEqual(["/a.js"]);
  });

  it("dispatches without draining the background half, which `request` cannot", async () => {
    // `request()` awaits the response and then drains `waitUntil`, so a handler
    // that awaited its background work before answering passes every assertion
    // it can make. This is the seam that sees the difference: `responded`
    // settles when the page has its answer, `settled` when the event is over.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => {
        event.respondWith((async () => {
          const response = await self.fetch(event.request);
          event.waitUntil((async () => {
            const cache = await self.caches.open("c");
            await cache.put("/late.js", new Response("late"));
          })());
          return response;
        })());
      });
    `);
    worker.respond("/assets/x.js", js("1"));
    const { responded, settled } = worker.dispatch({ path: "/assets/x.js", mode: "cors" });
    await expect((await responded)!.text()).resolves.toBe("1");
    expect(worker.keys("c")).toEqual([]);
    await settled;
    expect(worker.keys("c")).toEqual(["/late.js"]);
  });

  it("answers a stalled route's headers and never its body", async () => {
    // What a connection that goes quiet mid-body looks like from inside a
    // worker: a 200 in hand, `ok` true, and a read that never settles. The
    // fixture's own hazard, which is why nothing awaits one of these without a
    // deadline — a test that did would hang rather than fail.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => { event.respondWith(self.fetch(event.request)); });
    `);
    worker.respond("/", stalled());
    const response = await worker.request({ path: "/", mode: "navigate" });
    expect(response!.status).toBe(200);
    expect(response!.headers.get("content-type")).toContain("text/html");
    const read = response!.text();
    // Not `resolves`/`rejects`: the assertion is that it does neither.
    const outcome = await Promise.race([
      read.then(() => "settled"),
      new Promise((resolve) => setTimeout(() => resolve("pending"), 20)),
    ]);
    expect(outcome).toBe("pending");
  });
});
