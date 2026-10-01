import { describe, expect, it } from "vitest";
import source from "../../public/sw.kill.js?raw";
import shipped from "../../public/sw.js?raw";
import { type LoadOptions, loadWorker, ORIGIN } from "./sw-harness";

/**
 * The cache the shipped sw.js writes, read out of its own `VERSION` as
 * `worker.cache` reads it. This worker declares no version, so it has no
 * `cache` of its own to ask, and a name restated here would go on passing
 * after a bump had moved the real one.
 */
const SHELL = loadWorker(shipped).cache;

/**
 * The case the switch exists for: it arrives as an update over the shipped
 * worker, and a page that worker was serving is open.
 */
const STALE: LoadOptions = { update: true, clients: [{ path: "/", controlled: true }] };

/**
 * The case it must leave alone: a first visit with these bytes already at
 * /sw.js. The one page is the load that registered this worker, which no
 * worker has ever served — and after the switch is thrown, EVERY load is this
 * one, because main.tsx registers /sw.js on every load.
 */
const FRESH: LoadOptions = {};

/** Both at once: a fresh load nothing controls, open beside a page the shipped worker was serving. */
const BESIDE: LoadOptions = {
  update: true,
  clients: [{ path: "/fresh" }, { path: "/", controlled: true }],
};

/**
 * A phone with Alfred installed: the shell the shipped worker cached, a cache
 * an older build left behind, and one that no build of Alfred names. Every
 * cache on the origin goes, not only the ones the switch would recognise.
 */
function killer(options: LoadOptions = STALE) {
  const worker = loadWorker(source, options);
  worker.store.set(SHELL, new Map([[`${ORIGIN}/index.html`, new Response("<!doctype html>")]]));
  worker.store.set("alfred-shell-v0", new Map());
  worker.store.set("workbox-precache-v2", new Map());
  return worker;
}

describe("the kill switch", () => {
  it("takes over at once, without waiting to be asked", async () => {
    // The opposite of sw.js, and deliberately: this worker exists precisely for
    // the case where the app cannot be driven, so there is nobody to ask.
    const worker = killer();
    await worker.install();
    expect(worker.skipWaitingCalls).toBe(1);
  });

  // Both cases, because both happen to every phone: the copy that replaces the
  // real worker, and the one each later load installs afresh — which is the
  // second chance at a cache the first copy could not delete.
  it.each([
    ["over the shipped worker", STALE],
    ["in a fresh registration", FRESH],
  ])("drops every cache on the origin %s", async (_, options) => {
    const worker = killer(options);
    await worker.install();
    await worker.activate();
    expect([...worker.store.keys()]).toEqual([]);
  });

  it.each([
    ["over the shipped worker", STALE],
    ["in a fresh registration", FRESH],
  ])("unregisters itself %s, leaving a plain web page", async (_, options) => {
    const worker = killer(options);
    await worker.install();
    await worker.activate();
    expect(worker.unregisterCalls).toBe(1);
  });

  // Both cases, and the fresh one is why. A registration no page is using —
  // the copy each later load installs — is cleared as soon as unregister()
  // resolves, and Chromium stops this worker there: with the unregistration
  // moved first, such a copy left every cache it found. Counted as each delete
  // lands, so one still under way when the unregistration is asked for is a
  // delete not done:
  it.each([
    ["over the shipped worker", STALE],
    ["in a fresh registration", FRESH],
  ])("has deleted every cache before it unregisters %s", async (_, options) => {
    const worker = killer(options);
    const unregistersBy: number[] = [];
    const drop = worker.store.delete.bind(worker.store);
    worker.store.delete = (name: string) => {
      unregistersBy.push(worker.unregisterCalls);
      return drop(name);
    };
    await worker.install();
    await worker.activate();
    // Spelled as a list rather than `.every()`, which an empty list satisfies.
    expect(unregistersBy).toEqual([0, 0, 0]);
  });

  it("reloads a page the old worker was serving, once", async () => {
    // That page's document may have come out of the cache just dropped, and
    // nothing else will replace it: it is an installed app, open for days.
    const worker = killer();
    await worker.install();
    await worker.activate();
    expect(worker.navigated).toEqual([`${ORIGIN}/`]);
  });

  it("never reloads the page that registered it", async () => {
    // The loop. Reload this page and it registers /sw.js again, which installs
    // this worker again in a new registration, which reloads it again: 659
    // fetches of /sw.js in 8 s in Chromium, against a version that claimed
    // the open pages before it listed them. A phone in that loop is bricked.
    const worker = killer(FRESH);
    await worker.install();
    await worker.activate();
    expect(worker.navigated).toEqual([]);
  });

  it("reloads only the stale page when a fresh one is open beside it", async () => {
    const worker = killer(BESIDE);
    await worker.install();
    await worker.activate();
    expect(worker.navigated).toEqual([`${ORIGIN}/`]);
  });

  // Not reloading such a page is not enough. navigate() refuses a page this
  // worker does not control, and that refusal is a browser's check — the only
  // thing left between this worker and the loop if its listing ever widened to
  // `includeUncontrolled`, which lists both of these. So it never asks.
  it.each([
    ["the page that registered it", FRESH],
    ["a fresh page open beside a stale one", BESIDE],
  ])("never even asks to reload %s", async (_, options) => {
    const worker = killer(options);
    await worker.install();
    await worker.activate();
    expect(worker.refusedNavigations).toEqual([]);
  });

  it("never claims a page", async () => {
    // Activation already handed it every page the old worker controlled, so a
    // claim() adds only the pages no worker controls — the fresh load above,
    // the loop's own engine — and leaves each one controlled by a worker on its
    // way out, whose registration a later register("/sw.js") revives. Measured
    // in Chromium with the claim after the listing and two pages open: the run
    // ended with this worker still installed. See sw.kill.js.
    const worker = killer(BESIDE);
    await worker.install();
    await worker.activate();
    expect(worker.claimCalls).toBe(0);
  });

  it("carries on past a page that will not navigate", async () => {
    // Expected, not exotic. Activation fires `controllerchange` at the stale
    // page and src/lib/sw.ts reloads it, and Chromium rejects navigate() for a
    // page whose own reload is under way — even as the worker's navigation
    // replaces it ("Cannot navigate to URL"). A page that has closed rejects
    // too. That must cost neither the other pages their reload nor the origin
    // its clean-up. And the page that is left is reloaded where it is — its own
    // URL, not the root.
    const worker = killer({
      update: true,
      clients: [
        { path: "/gone", controlled: true },
        { path: "/actions/7", controlled: true },
      ],
    });
    worker.refuseNavigate("/gone");
    await worker.install();
    await expect(worker.activate()).resolves.toBeUndefined();
    expect(worker.navigated).toEqual([`${ORIGIN}/actions/7`]);
    expect([...worker.store.keys()]).toEqual([]);
    expect(worker.unregisterCalls).toBe(1);
  });

  it("carries on past a cache that will not go", async () => {
    // One transient storage error must not keep the caches after it, the
    // unregistration or the reload: this is the last lever there is.
    const worker = killer();
    worker.refuseDelete("alfred-shell-v0");
    await worker.install();
    await expect(worker.activate()).resolves.toBeUndefined();
    expect([...worker.store.keys()]).toEqual(["alfred-shell-v0"]);
    expect(worker.unregisterCalls).toBe(1);
    expect(worker.navigated).toEqual([`${ORIGIN}/`]);
  });

  it("has finished unregistering before it reloads anything", async () => {
    // Finished, not merely asked. A navigation that starts while the
    // registration still exists is matched to it, and Chromium holds it until
    // this worker has activated — which waits for the navigation. Measured with
    // the reloads started before the unregistration had finished: a page with
    // nothing else to reload it was never reloaded, this worker stayed
    // "activating", and the app's page revived the registration with its own
    // register("/sw.js"). `unregistered` at the moment of each navigation:
    const worker = killer();
    const unregisteredBy: boolean[] = [];
    const record = worker.navigated.push.bind(worker.navigated);
    worker.navigated.push = (...urls: string[]) => {
      unregisteredBy.push(worker.unregistered);
      return record(...urls);
    };
    await worker.install();
    await worker.activate();
    // Spelled as a list rather than `.every()`, which an empty list satisfies.
    expect(unregisteredBy).toEqual([true]);
  });

  it("caches nothing, ever", async () => {
    const worker = killer();
    await worker.install();
    await worker.activate();
    await expect(worker.request({ path: "/", mode: "navigate" })).resolves.toBeUndefined();
    await expect(worker.request({ path: "/assets/index-CY0Sh5Q2.js" })).resolves.toBeUndefined();
    expect([...worker.store.keys()]).toEqual([]);
  });
});
