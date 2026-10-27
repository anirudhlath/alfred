import { describe, expect, it, vi } from "vitest";
import { broken, fetchRequest, html, js, loadWorker, macrotasks, ORIGIN, stalled } from "./sw-harness";

// The harness is the only thing standing between "this worker's tests pass" and
// "this worker works", and eight tasks' tests are written against it. Where it
// models a real ServiceWorkerGlobalScope loosely it does not merely lose
// coverage — it can pass a worker that cannot install on any phone. These are
// the places that has to be true, pinned here so the next edit cannot quietly
// undo one.

/**
 * What `vi.waitFor` is given for a write or a clock the harness is about to
 * reach: polled on every macrotask, since the thing waited on moves on them,
 * and allowed as long as the test itself is (4 s of vitest's 5), so that this
 * budget is never what runs out first on a machine that is slow rather than
 * wrong. A failure still says what was expected and what was there.
 */
const REACHED = { interval: 1, timeout: 4000 } as const;

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

  it("can be told to hold a put, read and not landed, until the test lets it go", async () => {
    // A slow disk: the one wait in a write that nothing in a worker bounds, and
    // how a test stops an install part way through its writes. Everything the
    // install does after that put waits with it, as it would on a phone.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          await cache.put("/x", new Response("x"));
          await cache.put("/y", new Response("y"));
        })());
      });
    `);
    const release = worker.holdPut("/x");
    const installing = worker.install();
    // `puts` is recorded at the call, so it is the thing to wait on: the put has
    // been asked for, and what follows is what the hold has to stop.
    await vi.waitFor(() => expect(worker.puts).toEqual(["/x"]), REACHED);
    await macrotasks();
    expect(worker.puts).toEqual(["/x"]);
    expect(worker.keys("alfred-shell-v1")).toEqual([]);
    release();
    await installing;
    expect(worker.keys("alfred-shell-v1")).toEqual(["/x", "/y"]);
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

  it("can be told to refuse to list its caches, the other half of a store that is gone", async () => {
    // A store Safari has dropped refuses `keys()` as well as `open()`, and the
    // kill switch lists the caches before it deletes them. Without this, a
    // switch whose listing is uncaught — which then neither unregisters nor
    // reloads, and stays installed with nothing to bring a fresh copy — passes.
    const worker = loadWorker(`
      self.addEventListener("activate", (event) => {
        event.waitUntil(self.caches.keys());
      });
    `);
    worker.refuseKeys();
    await expect(worker.activate()).rejects.toThrow(/storage error/);
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

  it("can be told to hold the body of the next match, its head answered, until the test lets it go", async () => {
    // The read half of a slow disk: the match answers, so the worker has the
    // entry in hand, and reading it — a clone's read included — waits. Once:
    // the next match of the same key reads as any other, which is how a test
    // holds one reader's copy of an entry while another reader goes on.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          await cache.put("/x", new Response("x"));
          const held = await cache.match("/x");
          await cache.put("/status", new Response(String(held.status)));
          const reading = held.clone().text().then((text) => cache.put("/held", new Response(text)));
          await cache.put("/after", new Response(await (await cache.match("/x")).text()));
          await reading;
        })());
      });
    `);
    const release = worker.holdRead("/x");
    const installing = worker.install();
    // `/after` is written after the held read was started and does not wait on
    // it, so once it is in the unheld chain is done. `/held` is what the hold
    // keeps out, and the macrotasks are every chance a read that was not held
    // would have needed to land it.
    await vi.waitFor(() => expect(worker.keys("alfred-shell-v1")).toContain("/after"), REACHED);
    await macrotasks();
    expect(worker.keys("alfred-shell-v1")).toEqual(["/after", "/status", "/x"]);
    release();
    await installing;
    const entries = worker.store.get("alfred-shell-v1")!;
    await expect(entries.get(`${ORIGIN}/status`)!.clone().text()).resolves.toBe("200");
    await expect(entries.get(`${ORIGIN}/held`)!.clone().text()).resolves.toBe("x");
    await expect(entries.get(`${ORIGIN}/after`)!.clone().text()).resolves.toBe("x");
  });

  it("can be told to fail the body of the next match when it is read, once", async () => {
    // The entry found and the disk failing to hand it over: the match answers
    // and the read rejects. Without it, what a worker does with a cached body
    // that will not read is code no test can reach.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          await cache.put("/x", new Response("x"));
          const found = await cache.match("/x");
          const outcome = await found.clone().text().then(() => "read", (error) => error.name);
          await cache.put("/outcome", new Response(String(found.status) + " " + outcome));
          await cache.put("/after", new Response(await (await cache.match("/x")).text()));
        })());
      });
    `);
    worker.refuseRead("/x");
    await worker.install();
    const entries = worker.store.get("alfred-shell-v1")!;
    await expect(entries.get(`${ORIGIN}/outcome`)!.clone().text()).resolves.toBe("200 UnknownError");
    await expect(entries.get(`${ORIGIN}/after`)!.clone().text()).resolves.toBe("x");
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
    // ever — measured in Chromium with no DevTools attached to the worker, an
    // install left "installing" on exactly this until the browser's own limit
    // on the event failed it, at 300 s. A fake that stored the object unread
    // made that put look instant, and four puts in sw.js took a network body
    // with no deadline.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const cache = await self.caches.open("alfred-shell-v1");
          await cache.put("/x.js", await self.fetch("/x.js"));
        })());
      });
    `);
    worker.respond("/x.js", stalled("text/javascript"));
    // Not `resolves`/`rejects`: the assertion is that it does neither. The put
    // is waited on, and then the install is given every macrotask a put that
    // finished would have needed.
    const outcome = await Promise.race([
      worker.install().then(() => "settled"),
      (async () => {
        await vi.waitFor(() => expect(worker.puts).toEqual(["/x.js"]), REACHED);
        await macrotasks();
        return "pending";
      })(),
    ]);
    expect(outcome).toBe("pending");
    expect(worker.puts).toEqual(["/x.js"]);
    expect(worker.keys("alfred-shell-v1")).toEqual([]);
  });
});

describe("the fake Clients", () => {
  /**
   * In activate: claims first when asked to, then navigates every page that
   * `matchAll(<matchAll>)` answers, and writes how each one went into a cache
   * of its own as `/<outcome><page>` — "navigated", or the name of what the
   * navigation rejected with — because a cache key is the one thing a worker
   * can leave behind that a test can read.
   */
  const navigating = ({ claims = false, matchAll = "" } = {}) => `
    self.addEventListener("activate", (event) => {
      event.waitUntil((async () => {
        ${claims ? "await self.clients.claim();" : ""}
        const probe = await self.caches.open("probe");
        for (const client of await self.clients.matchAll(${matchAll})) {
          const outcome = await client.navigate(client.url).then(() => "navigated", (error) => error.name);
          await probe.put("/" + outcome + new URL(client.url).pathname, new Response(""));
        }
      })());
    });
  `;

  it("hands the activating worker the pages the older one controlled, before the event", async () => {
    // The spec's Activate algorithm re-points every client using the
    // registration at the new worker — and fires `controllerchange` at it —
    // BEFORE it dispatches `activate`. So the pages the previous build was
    // serving are this worker's, and in matchAll(), with no claim() at all.
    // A fake that hid them until a claim described the opposite of a browser,
    // and it is the fake the kill switch's reload loop was written against.
    const worker = loadWorker(navigating(), { update: true, clients: [{ path: "/", controlled: true }] });
    await worker.activate();
    expect(worker.keys("probe")).toEqual(["/navigated/"]);
    expect(worker.navigated).toEqual([`${ORIGIN}/`]);
  });

  it("leaves those pages with the older worker while this one is only installing", async () => {
    // The re-pointing is activation's, not install's: an installing worker
    // controls nothing, and a page the older worker serves is still its page
    // until this one activates — for as long as it waits, on an update.
    const worker = loadWorker(
      `self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          const probe = await self.caches.open("probe");
          await probe.put("/listed/" + (await self.clients.matchAll()).length, new Response(""));
        })());
      });`,
      { update: true, clients: [{ path: "/", controlled: true }] },
    );
    await worker.install();
    expect(worker.keys("probe")).toEqual(["/listed/0"]);
  });

  it("shows a page that nothing controlled to no worker that has not claimed it", async () => {
    // The default page: the one that registered this worker on a first
    // install, which no worker has ever served. matchAll() lists only the
    // clients this worker CONTROLS, so it is not there.
    const worker = loadWorker(navigating());
    await worker.activate();
    expect(worker.keys("probe")).toEqual([]);
    expect(worker.navigated).toEqual([]);
  });

  it("shows it once the worker has claimed", async () => {
    const worker = loadWorker(navigating({ claims: true }));
    await worker.activate();
    expect(worker.navigated).toEqual([`${ORIGIN}/`]);
  });

  it("tells a page the older worker controlled from one beside it that it did not", async () => {
    const worker = loadWorker(navigating(), {
      update: true,
      clients: [{ path: "/fresh" }, { path: "/stale", controlled: true }],
    });
    await worker.activate();
    expect(worker.navigated).toEqual([`${ORIGIN}/stale`]);
  });

  it("records where navigate() sent a page, not where the page was", async () => {
    // Every other test here sends a page to its own URL. A fake that wrote down
    // the page's URL instead would pass a worker that sent every page to the
    // root for one that reloads each where it is.
    const worker = loadWorker(
      `self.addEventListener("activate", (event) => {
        event.waitUntil((async () => {
          for (const client of await self.clients.matchAll()) await client.navigate("/elsewhere");
        })());
      });`,
      { update: true, clients: [{ path: "/actions/7", controlled: true }] },
    );
    await worker.activate();
    expect(worker.navigated).toEqual([`${ORIGIN}/elsewhere`]);
  });

  it("lists an uncontrolled page when asked to, and still will not navigate it", async () => {
    // `includeUncontrolled` widens what matchAll() shows, not what the worker
    // may do: navigate() on a page this worker does not control rejects with a
    // TypeError, at the call.
    const worker = loadWorker(navigating({ matchAll: "{ includeUncontrolled: true }" }));
    await worker.activate();
    expect(worker.keys("probe")).toEqual(["/TypeError/"]);
    expect(worker.navigated).toEqual([]);
  });

  it("rejects navigate() for a controlled page that is closing or already reloading", async () => {
    // Controlled, and listed, and refused all the same. src/lib/sw.ts reloads
    // a page when its controller changes, and Chromium rejects navigate() for
    // a page whose own reload is under way — even as the worker's navigation
    // replaces it. A page that has closed rejects too.
    const worker = loadWorker(navigating(), { update: true, clients: [{ path: "/", controlled: true }] });
    worker.refuseNavigate("/");
    await worker.activate();
    expect(worker.keys("probe")).toEqual(["/TypeError/"]);
    expect(worker.navigated).toEqual([]);
  });

  it("records the page each refused navigate() was for, which `navigated` cannot show", async () => {
    // A refused navigation starts nothing, so `navigated` reads the same for a
    // worker that never asks about a page and one that asks and is told no —
    // and for a page this worker does not control, that refusal is a browser's
    // check, the only thing then between the kill switch and its loop. The
    // page, not where it was sent: each is sent elsewhere here, so they differ.
    const worker = loadWorker(
      `self.addEventListener("activate", (event) => {
        event.waitUntil((async () => {
          for (const client of await self.clients.matchAll({ includeUncontrolled: true })) {
            await client.navigate("/elsewhere").catch(() => {});
          }
        })());
      });`,
      {
        update: true,
        clients: [{ path: "/fresh" }, { path: "/gone", controlled: true }, { path: "/stale", controlled: true }],
      },
    );
    worker.refuseNavigate("/gone");
    await worker.activate();
    expect(worker.refusedNavigations).toEqual([`${ORIGIN}/fresh`, `${ORIGIN}/gone`]);
    expect(worker.navigated).toEqual([`${ORIGIN}/elsewhere`]);
  });

  it("refuses a page controlled by an older worker when there is no older worker", () => {
    // A first install has nothing before it to have controlled anything. A
    // fixture that said otherwise would describe a phone no test should pass on.
    expect(() => loadWorker("", { clients: [{ path: "/", controlled: true }] })).toThrow(/update: true/);
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

  it("finishes an unregister() when its promise settles, not when it is called", async () => {
    // A real one is a job the browser runs, and until it has run the
    // registration is still there to match a navigation against. A fake that
    // was done at the call could not tell a worker that waits for it from one
    // that navigates the moment it has asked.
    const worker = loadWorker(`self.addEventListener("message", () => { self.registration.unregister(); });`);
    await worker.message("go");
    expect(worker.unregisterCalls).toBe(1);
    expect(worker.unregistered).toBe(false);
    // One macrotask, and no more is needed: this timer is armed after the one
    // `unregister()` armed, on the same list, and that list runs its timers in
    // the order they were armed — so it fires second, whatever the load.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(worker.unregistered).toBe(true);
  });

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

  /**
   * Writes the registration's three slots — installing, waiting, active, each a
   * state or "null" — at install, at activate and on any message, as
   * `/<when>/<installing>/<waiting>/<active>/<every worker installed so far>`.
   * A message is a look taken once an event has settled. "fail" makes the next
   * install or activate reject.
   */
  const slots = `
    let failing = false;
    const installed = [];
    const state = (worker) => (worker === null ? "null" : String(worker && worker.state));
    const seen = async (when, fail) => {
      const { installing, waiting, active } = self.registration;
      const cache = await self.caches.open("slots");
      const all = installed.map(state).join(",");
      await cache.put("/" + [when, state(installing), state(waiting), state(active), all].join("/"), new Response(""));
      if (fail) throw new Error("refused");
    };
    const next = () => {
      const fail = failing;
      failing = false;
      return fail;
    };
    self.addEventListener("install", (event) => {
      installed.push(self.registration.installing);
      event.waitUntil(seen("install", next()));
    });
    self.addEventListener("activate", (event) => event.waitUntil(seen("activate", next())));
    self.addEventListener("message", (event) => {
      if (event.data === "fail") failing = true;
      else event.waitUntil(seen(event.data, false));
    });
  `;

  it("has nothing installing and nothing waiting before an install", async () => {
    // Empty slots are null, as a real registration's are, and not undefined:
    // `=== null` must read the same here as on a phone.
    const worker = loadWorker(slots);
    await worker.message("before");
    expect(worker.keys("slots")).toEqual(["/before/null/null/null/"]);
  });

  it("has the worker installing, and nothing waiting, while its install runs", async () => {
    // In a browser `installing` is never empty during install, so a worker
    // that asks it anything gets an answer. Left undefined, a fake passed
    // `if (self.registration.installing) self.skipWaiting()`, which takes over
    // on every update.
    const first = loadWorker(slots);
    await first.install();
    expect(first.keys("slots")).toEqual(["/install/installing/null/null/installing"]);
    const update = loadWorker(slots, { update: true });
    await update.install();
    expect(update.keys("slots")).toEqual(["/install/installing/null/activated/installing"]);
  });

  it("moves the worker to waiting once its install has succeeded", async () => {
    // Out of `installing` and into `waiting`, in state "installed", behind the
    // older worker that is still active: the worker the page's update prompt
    // is about, and the one SKIP_WAITING reaches.
    const worker = loadWorker(slots, { update: true });
    await worker.install();
    await worker.message("after");
    expect(worker.keys("slots")).toContain("/after/null/installed/activated/installed");
  });

  it("makes a worker whose install failed redundant, and leaves the one waiting before it", async () => {
    const worker = loadWorker(slots, { update: true });
    await worker.install();
    await worker.message("fail");
    await expect(worker.install()).rejects.toThrow("refused");
    await worker.message("after");
    expect(worker.keys("slots")).toContain("/after/null/installed/activated/installed,redundant");
  });

  it("makes a waiting worker redundant when a newer one installs over it", async () => {
    const worker = loadWorker(slots, { update: true });
    await worker.install();
    await worker.install();
    await worker.message("after");
    expect(worker.keys("slots")).toContain("/after/null/installed/activated/redundant,installed");
  });

  it("activates the waiting worker, and leaves nothing waiting", async () => {
    // "activating" while the event runs, "activated" once everything it
    // extended with has settled — the state a page reads off
    // `registration.active` once the handshake is over.
    const worker = loadWorker(slots, { update: true });
    await worker.install();
    await worker.activate();
    await worker.message("after");
    expect(worker.keys("slots")).toEqual([
      "/activate/null/null/activating/activating",
      "/after/null/null/activated/activated",
      "/install/installing/null/activated/installing",
    ]);
  });

  it("marks the worker activated even when the work activate extended with rejected", async () => {
    // A browser does not undo an activation: the worker is active, and
    // "activated", whatever its waitUntil did.
    const worker = loadWorker(slots, { update: true });
    await worker.install();
    await worker.message("fail");
    await expect(worker.activate()).rejects.toThrow("refused");
    await worker.message("after");
    expect(worker.keys("slots")).toContain("/after/null/null/activated/activated");
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

  it("answers a stalled route's headers and the first of its body, and never the end", async () => {
    // What a connection that goes quiet mid-body looks like from inside a
    // worker: a 200 in hand, `ok` true, one read answered and the next never.
    // The first piece is the point — a worker that read one piece and called
    // the body done could not be told apart from one that read to the end
    // while this sent nothing. The fixture's own hazard, which is why nothing
    // awaits one of these without a deadline: a test that did would hang
    // rather than fail.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => { event.respondWith(self.fetch(event.request)); });
    `);
    worker.respond("/", stalled(undefined, "<!doctype html>"));
    const response = await worker.request({ path: "/", mode: "navigate" });
    expect(response!.status).toBe(200);
    expect(response!.headers.get("content-type")).toContain("text/html");
    const reader = response!.body!.getReader();
    expect(new TextDecoder().decode((await reader.read()).value)).toBe("<!doctype html>");
    // Not `resolves`/`rejects`: the assertion is that it does neither.
    const outcome = await Promise.race([
      reader.read().then(() => "settled"),
      macrotasks().then(() => "pending"),
    ]);
    expect(outcome).toBe("pending");
  });

  it("sends some of a stalled body by default, and none when told to", async () => {
    // Two stalls, and a worker has to survive both: the silent one is what a
    // deadline armed only once the first piece lands never ends.
    const first = (response: Response) =>
      Promise.race([
        response.body!.getReader().read().then(({ value }) => new TextDecoder().decode(value)),
        macrotasks().then(() => "pending"),
      ]);
    await expect(first(stalled())).resolves.not.toBe("pending");
    await expect(first(stalled(undefined, ""))).resolves.toBe("pending");
  });
});

describe("the fake clock", () => {
  /** Arms two timers on the worker's own `self.setTimeout` and records the order they fire in. */
  const racer = (first: number, second: number) =>
    loadWorker(`
      self.addEventListener("install", (event) => {
        const fired = [];
        event.waitUntil(new Promise((resolve) => {
          self.setTimeout(() => fired.push("first"), ${first});
          self.setTimeout(() => fired.push("second"), ${second});
          self.setTimeout(async () => {
            const cache = await self.caches.open("order");
            await cache.put("/" + fired.join(","), new Response(""));
            resolve();
          }, 60000);
        }));
      });
    `);

  it("fires what falls due first, not what was armed first, and moves to it", async () => {
    // The order is the whole of what this clock is for. A deadline armed for
    // 10 000 ms against a body piece due at 9 000 must lose, and one against a
    // piece due at 10 080 must win; a clock that fired every timer on the next
    // macrotask, as this one once did, fired them in the order they were armed
    // and could not tell a stall from a slow link.
    const worker = racer(10000, 9000);
    await worker.install();
    expect(worker.keys("order")).toEqual(["/second,first"]);
    expect(worker.now).toBe(60000);
  });

  it("fires two that fall due together in the order they were armed", async () => {
    const worker = racer(5000, 5000);
    await worker.install();
    expect(worker.keys("order")).toEqual(["/first,second"]);
  });

  it("does not fire a timer that was cleared", async () => {
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil(new Promise((resolve) => {
          const doomed = self.setTimeout(() => self.caches.open("fired"), 10);
          self.clearTimeout(doomed);
          self.setTimeout(resolve, 20);
        }));
      });
    `);
    await worker.install();
    expect([...worker.store.keys()]).toEqual([]);
    expect(worker.deadlinesCleared).toBe(1);
  });
});

describe("the fake network", () => {
  /** Fetches `/x` at install and puts it, which reads the body to the end. */
  const reader = `
    self.addEventListener("install", (event) => {
      event.waitUntil((async () => {
        const cache = await self.caches.open("c");
        await cache.put("/x", await self.fetch("/x"));
      })());
    });
  `;

  it("trickles a body a piece at a time on the clock, from the moment it is fetched", async () => {
    // Fetched ten seconds after it was made, and nothing sent before that:
    // three pieces 4 s apart and then the end put the finished read at 22 s.
    // A body that started when it was made would have had a piece waiting.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil((async () => {
          await new Promise((resolve) => self.setTimeout(resolve, 10000));
          const cache = await self.caches.open("c");
          await cache.put("/x", await self.fetch("/x"));
        })());
      });
    `);
    worker.respond("/x", worker.trickle("text/plain", ["a", "b", "c"], { every: 4000 }));
    await worker.install();
    expect(worker.now).toBe(10000 + 3 * 4000);
    // Every piece, in order.
    await expect(worker.store.get("c")!.get(`${ORIGIN}/x`)!.clone().text()).resolves.toBe("abc");
  });

  it("stops a trickle after its last piece when told to stall", async () => {
    const worker = loadWorker(reader);
    worker.respond("/x", worker.trickle("text/plain", ["a", "b"], { every: 4000, stall: true }));
    // Both pieces are waited on, on the clock that sends them, and then the
    // install is given every macrotask a trickle that ended would have needed.
    const outcome = await Promise.race([
      worker.install().then(() => "settled"),
      (async () => {
        await vi.waitFor(() => expect(worker.now).toBe(8000), REACHED);
        await macrotasks();
        return "pending";
      })(),
    ]);
    expect(outcome).toBe("pending");
    expect(worker.now).toBe(8000);
  });

  it("answers a late route on the clock, as late as it was told", async () => {
    // The answer a deadline stopped waiting for, arriving anyway: the headers
    // twelve seconds after the request, and the body as the route sends it.
    const worker = loadWorker(reader);
    worker.respond("/x", js("x"));
    worker.late("/x", 12000);
    await worker.install();
    expect(worker.now).toBe(12000);
    await expect(worker.store.get("c")!.get(`${ORIGIN}/x`)!.clone().text()).resolves.toBe("x");
  });

  it("fails a late route that has no answer, as late as it was told", async () => {
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil(self.fetch("/x").catch((error) => self.caches.open(error.name)));
      });
    `);
    worker.late("/x", 12000);
    await worker.install();
    expect(worker.now).toBe(12000);
    expect([...worker.store.keys()]).toEqual(["TypeError"]);
  });

  it("ends a late request the worker aborts, and leaves nothing of it on the clock", async () => {
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        const controller = new self.AbortController();
        self.setTimeout(() => controller.abort(), 10000);
        event.waitUntil(
          self.fetch("/x", { signal: controller.signal }).catch((error) => self.caches.open(error.name)),
        );
      });
    `);
    worker.respond("/x", js("x"));
    worker.late("/x", 12000);
    await worker.install();
    expect(worker.now).toBe(10000);
    expect([...worker.store.keys()]).toEqual(["AbortError"]);
    // The answer due at twelve seconds went with the request: nothing is left to
    // wait on, so the clock is given the macrotasks it would have used to get there.
    await macrotasks();
    expect(worker.now).toBe(10000);
  });

  it("records a late body let go of, through the same connection as any other", async () => {
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil(self.fetch("/x").then((response) => response.body.cancel()));
      });
    `);
    worker.respond("/x", stalled());
    worker.late("/x", 12000);
    await worker.install();
    expect(worker.now).toBe(12000);
    expect(worker.released).toEqual(["/x"]);
  });

  it("breaks a body off after its first piece, and refuses a cancel of it after that", async () => {
    // A connection reset, which ENDS a read where a stall never does — and an
    // errored stream rejects a cancel as well, which is the rejection a worker
    // giving up on one has to handle.
    const reader = broken(undefined, "<!doctype html>").body!.getReader();
    expect(new TextDecoder().decode((await reader.read()).value)).toBe("<!doctype html>");
    await expect(reader.read()).rejects.toThrow("network error");
    await expect(reader.cancel()).rejects.toThrow("network error");
  });

  it("records a body the worker let go of, as a browser tears the request down", async () => {
    // Every branch cancelled — here the only one — is what releases a request.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil(self.fetch("/x").then((response) => response.body.cancel()));
      });
    `);
    worker.respond("/x", stalled());
    await worker.install();
    expect(worker.released).toEqual(["/x"]);
  });

  it("does not record one somebody else is still reading", async () => {
    // A cancel stops at a tee while another branch is live. This is the
    // property that separates a worker giving up on its COPY of a page's
    // response from one that takes the page's read down with it.
    // Not returned: a cancel of one branch settles only once its twin is
    // cancelled too, and here the twin is the one being kept.
    const worker = loadWorker(`
      self.addEventListener("install", (event) => {
        event.waitUntil(self.fetch("/x").then((response) => {
          response.clone().body.cancel();
        }));
      });
    `);
    worker.respond("/x", stalled());
    await worker.install();
    expect(worker.released).toEqual([]);
  });

  it("errors a body still arriving, every branch of it, when its request is aborted after the headers", async () => {
    // A real fetch's abort does not stop at the headers: the Fetch Standard's
    // abort steps error the response's body, and in Chromium that reaches every
    // branch of it — measured in task 7's fourth fix round, where aborting after
    // the headers made the other branch's read reject with AbortError. It is
    // the whole reason a worker reading its copy of the PAGE's response cancels
    // that copy and never aborts the request: the abort would end the page's
    // read with its own. A harness that let a signal go quiet once the headers
    // were in could not tell the two apart, and passed a worker whose request
    // deadline was left armed past them.
    const worker = loadWorker(`
      self.addEventListener("fetch", (event) => {
        const controller = new self.AbortController();
        event.respondWith(self.fetch(event.request, { signal: controller.signal }).then((response) => {
          const copy = response.clone();
          event.waitUntil(copy.text().catch((error) => self.caches.open("copy: " + error.name)));
          controller.abort();
          return response;
        }));
      });
    `);
    /** How a read ended: the name of what it rejected with, or "pending" if it has not. */
    const ending = (read: Promise<unknown>) =>
      Promise.race([
        read.then(
          () => "resolved",
          (error: Error) => error.name,
        ),
        macrotasks().then(() => "pending"),
      ]);
    worker.respond("/x", stalled());
    const { responded, settled } = worker.dispatch({ path: "/x" });
    // The page's branch…
    await expect(ending((await responded)!.text())).resolves.toBe("AbortError");
    // …and the worker's, whose read ending is what lets the event finish.
    await expect(ending(settled)).resolves.toBe("resolved");
    expect([...worker.store.keys()]).toEqual(["copy: AbortError"]);
  });
});
