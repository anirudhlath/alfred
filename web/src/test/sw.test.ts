import { describe, expect, it } from "vitest";
// The shipped bytes, not a TypeScript copy of them — same reason
// index-html.test.ts reads the shipped index.html.
import source from "../../public/sw.js?raw";
import { css, html, js, loadWorker, ORIGIN, type Harness } from "./sw-harness";

/** What `vite build` really emits: one module script, one stylesheet. */
const SHELL_HTML = `<!doctype html><html lang="en"><head>
<link rel="manifest" href="/manifest.json" />
<link rel="apple-touch-icon" href="/apple-touch-icon.png" />
<script type="module" crossorigin src="/assets/index-CY0Sh5Q2.js"></script>
<link rel="stylesheet" crossorigin href="/assets/index-nhBuGG1b.css">
</head><body><div id="root"></div></body></html>`;

const SHELL_ASSETS = ["/assets/index-CY0Sh5Q2.js", "/assets/index-nhBuGG1b.css"];

function shell(): Harness {
  const worker = loadWorker(source);
  worker.respond("/index.html", html(SHELL_HTML));
  worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
  worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
  return worker;
}

/** A 404 from the /assets StaticFiles mount: JSON, not the SPA's HTML fallback. */
const notFound = () =>
  new Response('{"detail":"Not Found"}', {
    status: 404,
    headers: { "content-type": "application/json" },
  });

describe("install", () => {
  it("caches the shell and every asset index.html names", async () => {
    const worker = shell();
    await worker.install();
    expect(worker.keys()).toEqual(["/__shell-manifest", ...SHELL_ASSETS, "/index.html"]);
  });

  it("records what it cached, so activate can prune against it", async () => {
    const worker = shell();
    await worker.install();
    const listed = await worker.store
      .get(worker.cache)!
      .get(`${ORIGIN}/__shell-manifest`)!
      .json();
    expect(listed).toEqual(["/index.html", ...SHELL_ASSETS]);
  });

  it("stores the bytes it fetched, and not some other bytes", async () => {
    const worker = shell();
    await worker.install();
    // The key set alone says nothing about what is under each key: a worker
    // that cached an empty body, or the SPA fallback, or the stylesheet under
    // the chunk's URL, passes every assertion above.
    const entries = worker.store.get(worker.cache)!;
    const body = (path: string) => entries.get(`${ORIGIN}${path}`)!.clone().text();
    await expect(body("/index.html")).resolves.toBe(SHELL_HTML);
    await expect(body("/assets/index-CY0Sh5Q2.js")).resolves.toBe("console.log(1)");
    await expect(body("/assets/index-nhBuGG1b.css")).resolves.toBe("body{color:red}");
  });

  it("fetches the HTML first, because it is the manifest, then what it names", async () => {
    const worker = shell();
    await worker.install();
    // This pins what is fetched and in what order, not whether the assets go
    // out one at a time: fetching them with Promise.all would record the same
    // list, and would be no worse.
    expect(worker.fetched).toEqual(["/index.html", ...SHELL_ASSETS]);
  });

  it("asks past the HTTP cache for every one of them", async () => {
    const worker = shell();
    await worker.install();
    // Without `cache: "reload"`, a worker installing seconds after a deploy can
    // precache the bytes that deploy just replaced, and serve them until the
    // next one. Every install fetch, not merely the HTML — spelled out rather
    // than compared against `fetched`, which two empty lists would also satisfy.
    expect(worker.reloaded).toEqual(["/index.html", ...SHELL_ASSETS]);
  });

  it("precaches nothing that is not a same-origin /assets/ path", async () => {
    const worker = loadWorker(source);
    worker.respond(
      "/index.html",
      html(`<!doctype html><html lang="en"><head>
<link rel="manifest" href="/manifest.json" />
<link rel="apple-touch-icon" href="/apple-touch-icon.png" />
<script src="/api/admin/overview"></script>
<script src="https://cdn.example.invalid/assets/tracker.js"></script>
<script type="module" crossorigin src="/assets/index-CY0Sh5Q2.js"></script>
<link rel="stylesheet" crossorigin href="/assets/index-nhBuGG1b.css">
</head><body><div id="root"></div></body></html>`),
    );
    // Routes for all three refusals, deliberately: without them the worker
    // would "pass" by failing to fetch rather than by declining to.
    worker.respond("/manifest.json", js("{}"));
    worker.respond("/apple-touch-icon.png", js("png"));
    worker.respond("/api/admin/overview", js('{"dnd":false}'));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
    await worker.install();
    expect(worker.fetched).toEqual(["/index.html", ...SHELL_ASSETS]);
    expect(worker.keys()).toEqual(["/__shell-manifest", ...SHELL_ASSETS, "/index.html"]);
  });

  it("fetches an asset the HTML names twice only once", async () => {
    const worker = loadWorker(source);
    // A build that splits writes a modulepreload hint beside the tag naming the
    // same URL. Two fetches and two puts of identical bytes is the cheap cost;
    // a manifest listing it twice is the one activate would prune against.
    worker.respond(
      "/index.html",
      html(`<!doctype html><html lang="en"><head>
<link rel="modulepreload" href="/assets/index-CY0Sh5Q2.js" />
<script type="module" crossorigin src="/assets/index-CY0Sh5Q2.js"></script>
<link rel="stylesheet" crossorigin href="/assets/index-nhBuGG1b.css">
</head><body><div id="root"></div></body></html>`),
    );
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
    await worker.install();
    expect(worker.fetched).toEqual(["/index.html", ...SHELL_ASSETS]);
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.json();
    expect(listed).toEqual(["/index.html", ...SHELL_ASSETS]);
  });

  it("fails the install rather than caching half a shell", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
    // The entry chunk has no route: the network is gone mid-install. A
    // half-precached shell is a shell that boots to a blank screen offline.
    await expect(worker.install()).rejects.toThrow();
    // Not "some of it": nothing. Everything is validated before anything is
    // written, so a failed install leaves the previous worker's cache alone.
    expect(worker.keys()).toEqual([]);
  });

  it("writes nothing when it is the second asset that is missing", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    // The stylesheet is the one with no route. A fixture that fails on the
    // FIRST asset cannot tell "validate everything, then write" apart from
    // "write as you go" — both cache nothing. This one can: writing as it goes
    // would leave the shell and the entry chunk behind.
    await expect(worker.install()).rejects.toThrow();
    expect(worker.keys()).toEqual([]);
  });

  it("refuses an asset that 404s from the /assets mount", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    // What a removed hashed chunk really answers: /assets is a StaticFiles
    // mount, so it never reaches the SPA fallback. 404, application/json,
    // 22 bytes. `response.ok` is the half of isAsset that catches this.
    worker.respond("/assets/index-CY0Sh5Q2.js", notFound());
    worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
    await expect(worker.install()).rejects.toThrow();
    expect(worker.keys()).toEqual([]);
  });

  it("refuses an asset that comes back as the SPA fallback", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    // The other half of isAsset. It cannot fire while the /assets mount exists,
    // but a proxy or a captive portal can answer 200 with a page at any time,
    // and caching one under a .js URL is how a PWA bricks itself.
    worker.respond("/assets/index-CY0Sh5Q2.js", html(SHELL_HTML));
    worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
    await expect(worker.install()).rejects.toThrow();
    expect(worker.keys()).toEqual([]);
  });

  it("refuses a shell that is not HTML", async () => {
    const worker = loadWorker(source);
    // The same page, served with the wrong content type by something in front
    // of the app. Every asset it names IS routed, deliberately: without them
    // the install would fail for want of a network and this test would pass on
    // a worker with no isShell check at all.
    worker.respond("/index.html", js(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
    await expect(worker.install()).rejects.toThrow();
    expect(worker.keys()).toEqual([]);
    // And it stopped at the shell rather than going on to fetch them.
    expect(worker.fetched).toEqual(["/index.html"]);
  });

  it.each([
    [
      "the reverse proxy's default page",
      "<!doctype html><html><head><title>Congratulations!</title></head><body><h1>Congratulations!</h1></body></html>",
    ],
    [
      "the dev server's index.html",
      `<!doctype html><html lang="en"><head>
<script type="module" src="/@vite/client"></script>
</head><body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>`,
    ],
  ])("refuses %s, which is 200 text/html and names no asset", async (_name, page) => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(page));
    // Both are real: this origin sits behind a reverse proxy whose default page
    // answers while a proxy host is edited or a DNS record moves, and the dev
    // server names /@vite/client and /src/main.tsx and nothing hashed. Either
    // installs cleanly as the shell if nothing counts what it named — and
    // nothing re-installs afterwards, because `install` only re-runs when
    // sw.js's own bytes change. The recovery would be the kill switch.
    await expect(worker.install()).rejects.toThrow();
    expect(worker.keys()).toEqual([]);
  });
});
