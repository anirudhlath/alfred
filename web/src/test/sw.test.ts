import { describe, expect, it } from "vitest";
// The shipped bytes, not a TypeScript copy of them — same reason
// index-html.test.ts reads the shipped index.html.
import source from "../../public/sw.js?raw";
import {
  css,
  fetchRequest,
  font,
  html,
  js,
  loadWorker,
  ORIGIN,
  stalled,
  type Harness,
  type RequestSpec,
} from "./sw-harness";

/** What `vite build` really emits: one module script, one stylesheet. */
const SHELL_HTML = `<!doctype html><html lang="en"><head>
<link rel="manifest" href="/manifest.json" />
<link rel="apple-touch-icon" href="/apple-touch-icon.png" />
<script type="module" crossorigin src="/assets/index-CY0Sh5Q2.js"></script>
<link rel="stylesheet" crossorigin href="/assets/index-nhBuGG1b.css">
</head><body><div id="root"></div></body></html>`;

const SHELL_ASSETS = ["/assets/index-CY0Sh5Q2.js", "/assets/index-nhBuGG1b.css"];

/**
 * Shaped like the stylesheet `npm run build` really emits, checked against
 * `dist/assets/index-*.css` on 2026-09-16: root-absolute `/assets/` URLs, no
 * space before `format(…)`, the `.woff` fallback sharing a `src` with its
 * `.woff2`, and a subset small enough that Vite inlined it — the real file has
 * six `url(data:font/woff2;…)` and two `url(data:font/woff;…)`.
 */
const SHELL_CSS = `@font-face{font-family:DM Sans Variable;font-display:swap;src:url(/assets/dm-sans-latin-standard-normal-Bf69Tn_J.woff2)format("woff2-variations")}
@font-face{font-family:Geist Mono;font-weight:400;src:url(/assets/geist-mono-latin-400-normal-DTRLJnHl.woff2)format("woff2"),url(/assets/geist-mono-latin-400-normal-B2yvC1Cq.woff)format("woff")}
@font-face{font-family:Geist Mono;font-weight:500;src:url(data:font/woff2;base64,d09GMgABAAAA)format("woff2")}`;

/** The two the stylesheet names as files, in the order it names them. */
const SHELL_FONTS = [
  "/assets/dm-sans-latin-standard-normal-Bf69Tn_J.woff2",
  "/assets/geist-mono-latin-400-normal-DTRLJnHl.woff2",
];

/** One of the ten `.woff` fallbacks this build emits beside the `.woff2`. */
const WOFF_FALLBACK = "/assets/geist-mono-latin-400-normal-B2yvC1Cq.woff";

function shell(): Harness {
  const worker = loadWorker(source);
  worker.respond("/index.html", html(SHELL_HTML));
  worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
  worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
  return worker;
}

/**
 * The same shell with the stylesheet that names two woff2 files — everything a
 * real install writes. At module scope because the activate tests need a full
 * cache to prove the purge keeps one, not only the two assets `shell()` has.
 */
function withFonts(): Harness {
  const worker = loadWorker(source);
  worker.respond("/index.html", html(SHELL_HTML));
  worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
  worker.respond("/assets/index-nhBuGG1b.css", css(SHELL_CSS));
  for (const path of SHELL_FONTS) worker.respond(path, font());
  // The fallback is routed on purpose, exactly as the three refusals above
  // are: without a route a worker that took it would "pass" by failing to
  // fetch it rather than by declining to.
  worker.respond(WOFF_FALLBACK, font());
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
    // `.clone()`, here and everywhere a test reads a stored body directly. The
    // store holds the cache's own Response, so reading it leaves that copy
    // consumed — and `FakeCache.match` clones, which THROWS on a disturbed
    // body. A bare `.json()` followed by an activate is a synchronous error out
    // of `match()` that no browser produces.
    const listed = await worker.store
      .get(worker.cache)!
      .get(`${ORIGIN}/__shell-manifest`)!
      .clone()
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
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.clone().json();
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

  it("refuses a shell that is an HTML error page", async () => {
    const worker = loadWorker(source);
    // isShell's other half, which the content-type fixture above leaves
    // untested: a proxy's 502 is a real page of real text/html, and it names
    // assets the moment the proxy's error template carries any. `response.ok`
    // is the only thing that turns it away.
    worker.respond(
      "/index.html",
      new Response(SHELL_HTML, { status: 502, headers: { "content-type": "text/html" } }),
    );
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
    await expect(worker.install()).rejects.toThrow();
    expect(worker.keys()).toEqual([]);
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

  it("keeps the shell it had when a write fails part way through", async () => {
    // A second install writes into the cache the previous worker is still
    // serving from. Every body is in memory before the first write, so the one
    // way a write can still fail is storage — a phone that fills up part way
    // through — and with the shell written first, a failure after it left that
    // worker's offline launch a new shell naming a chunk the cache does not
    // hold: a white screen, from an install that did not even succeed. The
    // shell goes in last, as refresh() publishes it, so the one that stays is
    // the one whose assets are all there.
    const worker = shell();
    await worker.install();
    worker.respond("/index.html", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    worker.refusePut(NEXT_ASSETS[1]);
    await expect(worker.install()).rejects.toThrow(/storage error/);
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("two");
    expect(worker.keys()).toEqual(expect.arrayContaining(SHELL_ASSETS));
  });
});

describe("install, and the fonts", () => {
  const cached = ["/__shell-manifest", ...SHELL_ASSETS, ...SHELL_FONTS, "/index.html"].sort();

  it("follows the stylesheet to the woff2 files the HTML never names", async () => {
    const worker = withFonts();
    await worker.install();
    // The HTML names two things and neither is a typeface, so without this pass
    // a cold offline launch renders in -apple-system rather than DM Sans.
    expect(worker.keys()).toEqual(cached);
  });

  it("stores the font bytes under the font's own URL", async () => {
    const worker = withFonts();
    await worker.install();
    // The key set says nothing about what is under each key: a loop that put
    // the stylesheet's response under the font's URL passes the test above.
    const entries = worker.store.get(worker.cache)!;
    for (const path of SHELL_FONTS) {
      await expect(entries.get(`${ORIGIN}${path}`)!.clone().text()).resolves.toBe("woff2");
    }
  });

  it("asks past the HTTP cache for the fonts too", async () => {
    const worker = withFonts();
    await worker.install();
    // Same reason as the shell and the chunks: a worker installing seconds
    // after a deploy must not precache the bytes that deploy replaced.
    expect(worker.reloaded).toEqual(["/index.html", ...SHELL_ASSETS, ...SHELL_FONTS]);
  });

  it("records the fonts in the manifest, so activate does not prune them", async () => {
    const worker = withFonts();
    await worker.install();
    // A font cached but unlisted is a font the versioned purge deletes on the
    // very next activate — precached once, gone before it was ever served.
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.clone().json();
    expect(listed).toEqual(["/index.html", ...SHELL_ASSETS, ...SHELL_FONTS]);
  });

  it("skips the .woff fallbacks, which no worker-capable browser asks for", async () => {
    const worker = withFonts();
    await worker.install();
    // @fontsource ships one beside every woff2 — ten in this build, 79 kB of
    // the 215 kB of fonts. Every browser that can run a service worker has
    // supported woff2 for years, so taking them is a third of the install for
    // bytes nothing will ever ask for.
    expect(worker.fetched).not.toContain(WOFF_FALLBACK);
    expect(worker.keys()).not.toContain(WOFF_FALLBACK);
  });

  it("ignores url(data:…), which is already inside the stylesheet it cached", async () => {
    const worker = withFonts();
    await worker.install();
    // Two guards refuse it, which is why this dies only when fontsInCss is
    // rewritten wide open rather than when either one is edited: isAssetUrl
    // first (a data: URL's origin is not this one), and the .woff2 narrowing
    // second (its pathname is `font/woff2;base64,…`). That rewrite is exactly
    // what this exists to catch. Asserted on what was FETCHED, not on the keys
    // — a data: URL that slipped through has no route, so it would fail quietly
    // and cache nothing while still costing a request on every install. And on
    // the payload rather than the scheme, because `fetched` records pathnames
    // and this one does not start with "data:".
    expect(worker.fetched.filter((path) => path.includes("base64"))).toEqual([]);
  });

  it.each([
    [
      "a CDN's",
      "https://fonts.cdn.example.invalid/assets/inter-latin.woff2",
      "https://fonts.cdn.example.invalid/assets/inter-latin.woff2",
    ],
    ["one outside /assets/", "/fonts/legacy-Bf69Tn.woff2", "/fonts/legacy-Bf69Tn.woff2"],
    [
      "one that walks out of /assets/",
      "/assets/../api/admin/overview.woff2",
      "/api/admin/overview.woff2",
    ],
    // The next three exist because /assets/ is an ANCHOR, and nothing else in
    // this file said so. The first two are the whole of the guard: with
    // `includes` instead of `startsWith` the admin overview is fetched and — the
    // mount answers application/json, which isAsset accepts — stored in the
    // shell cache as a font. With the trailing slash dropped, any sibling
    // directory whose name merely begins "assets" is inside the cache.
    [
      "one under a path that only contains /assets/",
      "/api/admin/assets/overview.woff2",
      "/api/admin/assets/overview.woff2",
    ],
    [
      "one under a path that only starts with /assets",
      "/assetsphalt/x-Aa11bB22.woff2",
      "/assetsphalt/x-Aa11bB22.woff2",
    ],
    // %2f is a separator the URL parser does not treat as one: this pathname
    // really does stay under /assets/, and becomes a traversal the moment
    // uvicorn decodes it. Inert against StaticFiles, which 404s it — but the
    // refusal should not depend on a server this file cannot see.
    [
      "one hiding its separator as %2f",
      "/assets/..%2fapi/admin/overview.woff2",
      "/assets/..%2fapi/admin/overview.woff2",
    ],
    // Not isAssetUrl's business but the extension's: `includes(".woff2")` takes
    // this, and a proxy that pre-compresses writes exactly this name beside
    // every asset it serves.
    [
      "one whose name only contains .woff2",
      "/assets/dm-sans-latin-standard-normal-Bf69Tn_J.woff2.br",
      "/assets/dm-sans-latin-standard-normal-Bf69Tn_J.woff2.br",
    ],
  ])("refuses %s font, however the stylesheet names it", async (_name, named, routed) => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    worker.respond("/assets/index-nhBuGG1b.css", css(`@font-face{src:url(${named})format("woff2")}`));
    // Routed, again, so the refusal is a refusal. The third row is why the URL
    // is resolved before it is judged rather than after: `/assets/../api/…`
    // passes a `startsWith("/assets/")` written against the raw text, and the
    // browser would send it to /api.
    worker.respond(routed, font());
    await worker.install();
    expect(worker.fetched).toEqual(["/index.html", ...SHELL_ASSETS]);
    expect(worker.keys()).toEqual(["/__shell-manifest", ...SHELL_ASSETS, "/index.html"]);
  });

  it("takes a font however the stylesheet spaces and quotes it", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    // Vite's minified output is unquoted and unspaced, and @fontsource's own
    // source CSS is too — but `url("…")` is the CSS spec's form, and a
    // stylesheet that is hand-written, postcss-processed or built with
    // cssMinify off carries it. Dropping a font because of a quotation mark
    // would be silent: the app still renders, in the wrong typeface.
    worker.respond(
      "/assets/index-nhBuGG1b.css",
      css(`@font-face{src:url( "/assets/quoted-Aa11bB22.woff2" )}
@font-face{src:url( /assets/spaced-Cc33dD44.woff2 )}`),
    );
    worker.respond("/assets/quoted-Aa11bB22.woff2", font());
    worker.respond("/assets/spaced-Cc33dD44.woff2", font());
    await worker.install();
    expect(worker.keys()).toEqual(
      [
        "/__shell-manifest",
        ...SHELL_ASSETS,
        "/assets/quoted-Aa11bB22.woff2",
        "/assets/spaced-Cc33dD44.woff2",
        "/index.html",
      ].sort(),
    );
    // And it is recorded under the URL it is cached under. A manifest entry
    // that still carries the stylesheet's spacing is a font activate cannot
    // match against the cache key, so the purge deletes it on the next boot.
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.clone().json();
    expect(listed).toEqual([
      "/index.html",
      ...SHELL_ASSETS,
      "/assets/quoted-Aa11bB22.woff2",
      "/assets/spaced-Cc33dD44.woff2",
    ]);
  });

  it("takes a font however the stylesheet cases it", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    // CSS url() is an ASCII case-insensitive token and a filename is whatever
    // the filesystem says. A case-sensitive regex would be a second refusal
    // standing beside isAssetUrl, undocumented, and paid for in a typeface that
    // silently does not arrive. The path keeps its own case — only the test for
    // the extension ignores it — because the server's does.
    worker.respond(
      "/assets/index-nhBuGG1b.css",
      css(`@font-face{src:URL(/assets/upper-Aa11bB22.woff2)}
@font-face{src:url(/assets/shouty-Cc33dD44.WOFF2)}`),
    );
    worker.respond("/assets/upper-Aa11bB22.woff2", font());
    worker.respond("/assets/shouty-Cc33dD44.WOFF2", font());
    await worker.install();
    expect(worker.keys()).toEqual(
      [
        "/__shell-manifest",
        ...SHELL_ASSETS,
        "/assets/upper-Aa11bB22.woff2",
        "/assets/shouty-Cc33dD44.WOFF2",
        "/index.html",
      ].sort(),
    );
  });

  it("takes a url() out of a comment or a string, which is the price of a wide regex", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    // Pinned as a cost, not as a feature. This worker matches `url(` in text; it
    // does not tokenise CSS, and it is not going to — a CSS parser inside a
    // service worker is a far worse trade than one install request and some
    // cache bytes nothing asks for. The real stylesheet does carry a comment
    // (Tailwind's licence banner), so the shape is real even if today's content
    // is harmless. What bounds the cost is isAssetUrl, which is why the second
    // and third rules here are still refused.
    worker.respond(
      "/assets/index-nhBuGG1b.css",
      css(`/*! banner url(/assets/ghost-Aa11bB22.woff2) */
.a::after{content:"url(/assets/ghost2-Cc33dD44.woff2)"}
/* url(/api/admin/overview.woff2) and url(https://cdn.example.invalid/assets/x.woff2) */`),
    );
    worker.respond("/assets/ghost-Aa11bB22.woff2", font());
    worker.respond("/assets/ghost2-Cc33dD44.woff2", font());
    worker.respond("/api/admin/overview.woff2", font());
    worker.respond("https://cdn.example.invalid/assets/x.woff2", font());
    await worker.install();
    expect(worker.fetched).toEqual([
      "/index.html",
      ...SHELL_ASSETS,
      "/assets/ghost-Aa11bB22.woff2",
      "/assets/ghost2-Cc33dD44.woff2",
    ]);
  });

  it("fetches a font two rules name only once", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    // A shape @fontsource emits: one src: list naming the same file twice for
    // two format() hints. Two fetches and two puts of identical bytes is the
    // cheap half of the cost; a manifest entry listed twice is the one activate
    // prunes against, which is why the asset pass holds the same property.
    worker.respond(
      "/assets/index-nhBuGG1b.css",
      css(`@font-face{src:url(/assets/twice-Aa11bB22.woff2)format("woff2-variations"),url(/assets/twice-Aa11bB22.woff2)format("woff2")}`),
    );
    worker.respond("/assets/twice-Aa11bB22.woff2", font());
    await worker.install();
    expect(worker.fetched).toEqual([
      "/index.html",
      ...SHELL_ASSETS,
      "/assets/twice-Aa11bB22.woff2",
    ]);
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.clone().json();
    expect(listed).toEqual(["/index.html", ...SHELL_ASSETS, "/assets/twice-Aa11bB22.woff2"]);
  });

  it("fetches a font two stylesheets name only once", async () => {
    const worker = loadWorker(source);
    // A code-split build emits a stylesheet per chunk, and two that both pull
    // in the same @fontsource subset each carry its @font-face. fontsInCss
    // dedupes within one sheet; this is the pass across them.
    worker.respond(
      "/index.html",
      html(`<!doctype html><html lang="en"><head>
<script type="module" crossorigin src="/assets/index-CY0Sh5Q2.js"></script>
<link rel="stylesheet" crossorigin href="/assets/index-nhBuGG1b.css">
<link rel="stylesheet" crossorigin href="/assets/room-Dk3mQ9pR.css">
</head><body><div id="root"></div></body></html>`),
    );
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    const face = `@font-face{src:url(/assets/shared-Aa11bB22.woff2)format("woff2")}`;
    worker.respond("/assets/index-nhBuGG1b.css", css(face));
    worker.respond("/assets/room-Dk3mQ9pR.css", css(`${face}\n.x{color:red}`));
    worker.respond("/assets/shared-Aa11bB22.woff2", font());
    await worker.install();
    const sheets = ["/assets/index-nhBuGG1b.css", "/assets/room-Dk3mQ9pR.css"];
    expect(worker.fetched).toEqual([
      "/index.html",
      "/assets/index-CY0Sh5Q2.js",
      ...sheets,
      "/assets/shared-Aa11bB22.woff2",
    ]);
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.clone().json();
    expect(listed).toEqual([
      "/index.html",
      "/assets/index-CY0Sh5Q2.js",
      ...sheets,
      "/assets/shared-Aa11bB22.woff2",
    ]);
  });

  it("resolves a relative url() against the stylesheet, as a browser does", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    // @fontsource's own CSS writes url(./files/…), and Vite rewrites it to a
    // root-absolute path only while `base` is "/" — a sub-path deploy emits the
    // relative form. A browser resolves it against the stylesheet; resolving it
    // against the document instead yields /x.woff2, which isAssetUrl then
    // refuses, and the typeface is silently absent offline.
    worker.respond(
      "/assets/index-nhBuGG1b.css",
      css(`@font-face{src:url(./relative-Aa11bB22.woff2)format("woff2")}`),
    );
    worker.respond("/assets/relative-Aa11bB22.woff2", font());
    await worker.install();
    expect(worker.keys()).toEqual(
      [
        "/__shell-manifest",
        ...SHELL_ASSETS,
        "/assets/relative-Aa11bB22.woff2",
        "/index.html",
      ].sort(),
    );
  });

  it("does not fetch a font the HTML already named", async () => {
    const worker = loadWorker(source);
    // A <link rel="preload" as="font"> is a normal thing to add to index.html,
    // and the asset pass takes it as an asset. Without this the font pass takes
    // it a second time and the manifest carries it twice.
    worker.respond(
      "/index.html",
      html(`<!doctype html><html lang="en"><head>
<link rel="preload" as="font" href="/assets/dm-sans-latin-standard-normal-Bf69Tn_J.woff2" crossorigin />
<script type="module" crossorigin src="/assets/index-CY0Sh5Q2.js"></script>
<link rel="stylesheet" crossorigin href="/assets/index-nhBuGG1b.css">
</head><body><div id="root"></div></body></html>`),
    );
    worker.respond("/assets/dm-sans-latin-standard-normal-Bf69Tn_J.woff2", font());
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    worker.respond("/assets/index-nhBuGG1b.css", css(SHELL_CSS));
    // The stylesheet's other font is routed so that the preloaded one is the
    // only thing this test is asking about.
    worker.respond(SHELL_FONTS[1], font());
    await worker.install();
    expect(worker.fetched).toEqual([
      "/index.html",
      "/assets/dm-sans-latin-standard-normal-Bf69Tn_J.woff2",
      ...SHELL_ASSETS,
      SHELL_FONTS[1],
    ]);
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.clone().json();
    expect(listed).toEqual([
      "/index.html",
      "/assets/dm-sans-latin-standard-normal-Bf69Tn_J.woff2",
      ...SHELL_ASSETS,
      SHELL_FONTS[1],
    ]);
  });

  it("reads the stylesheet for fonts and not the entry chunk", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    // A bundle is not a list of anything. This build's chunk does contain the
    // text `url(` twice, but both are a zero-argument method of that name in the
    // WebSocket client and the regex captures neither — so the rule rests on
    // what a bundle IS, not on that: any library that injects styles at runtime
    // carries @font-face rules inside it, and reading 414 kB into a string to
    // find out costs the install and returns nothing. The planted rule below is
    // the shape that would be taken.
    worker.respond(
      "/assets/index-CY0Sh5Q2.js",
      js('const s="@font-face{src:url(/assets/ghost-D3adB33f.woff2)}";'),
    );
    worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
    worker.respond("/assets/ghost-D3adB33f.woff2", font());
    await worker.install();
    expect(worker.fetched).toEqual(["/index.html", ...SHELL_ASSETS]);
  });

  it("installs anyway when a font comes back as something that is not a font", async () => {
    const worker = withFonts();
    // A font that 404s from behind a proxy, or once the /assets mount is gone,
    // comes back as index.html with a 200. It must not be cached as a font —
    // and it must not fail an install over a typeface: an install aborted here
    // leaves the phone with no worker at all, which is the worse of the two.
    worker.respond(SHELL_FONTS[0], html("<!doctype html><html></html>"));
    await worker.install();
    expect(worker.keys()).not.toContain(SHELL_FONTS[0]);
    // The second font still landed, so the loop declined one rather than stopping.
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", ...SHELL_ASSETS, SHELL_FONTS[1], "/index.html"].sort(),
    );
    // And the manifest records what was CACHED, not what was wanted. Listing a
    // font that was declined is a name activate keeps a slot open for and the
    // asset branch would look up and miss — the record has to be fact, not
    // intent.
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.clone().json();
    expect(listed).toEqual(["/index.html", ...SHELL_ASSETS, SHELL_FONTS[1]]);
  });

  it("installs anyway when a font cannot be fetched at all", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    worker.respond("/assets/index-nhBuGG1b.css", css(SHELL_CSS));
    // The other failure: not a wrong answer but no answer. The first font has
    // no route — the network went away mid-install — and fetch() rejects with a
    // TypeError, which fails the whole install unless it is caught. Aborting
    // here would be worse than the missing typeface: it leaves the phone with
    // no worker at all, and install only re-runs when sw.js's bytes change.
    worker.respond(SHELL_FONTS[1], font());
    await worker.install();
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", ...SHELL_ASSETS, SHELL_FONTS[1], "/index.html"].sort(),
    );
  });
});

describe("install, and the network that never answers", () => {
  it("gives every request a deadline", async () => {
    const worker = shell();
    await worker.install();
    // One per fetch — the shell and both chunks — and one per body, which is
    // the half that is easy to miss: fetch() resolves on the HEADERS, so a
    // route that answers 200 and then goes quiet leaves `await text()` pending
    // for ever on a request that looks finished — and `Cache.put` is a read of
    // the body too, which is why the chunk the worker never reads itself gets
    // one. A `.catch()` handles a fetch that rejects and does nothing at all
    // for any of these hangs, so they are the only thing standing between a
    // captive portal and an install that waits until the browser kills it.
    expect(worker.deadlines).toEqual(Array<number>(6).fill(10000));
    // Three fetches and three bodies: the shell's HTML, and each asset read
    // whole before it is put (the stylesheet's font scrape then reads from
    // memory, with nothing left to wait on).
    expect(worker.fetched).toHaveLength(3);
    expect(worker.deadlines).toHaveLength(worker.fetched.length * 2);
    // And takes each one back down once its request is done. A deadline left
    // armed fires ten seconds later against nothing, holding the worker awake
    // to do it.
    expect(worker.deadlinesCleared).toBe(worker.deadlines.length);
  });

  it("gives up on a font that accepts the connection and answers nothing", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    worker.respond("/assets/index-nhBuGG1b.css", css(SHELL_CSS));
    // Not a rejection and not a wrong answer: a black hole. This is what a
    // captive portal does, and the outcome without a deadline is the one the
    // font loop's catch exists to prevent — waitUntil never settles, the
    // browser kills the install, and the phone has no worker at all.
    worker.hang(SHELL_FONTS[0]);
    worker.respond(SHELL_FONTS[1], font());
    await worker.install();
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", ...SHELL_ASSETS, SHELL_FONTS[1], "/index.html"].sort(),
    );
    // Five fetches — shell, two chunks, two fonts — and four bodies: the
    // shell's, both chunks', and the font that answered. The one that hung
    // never had a body to read.
    expect(worker.fetched).toHaveLength(5);
    expect(worker.deadlines).toEqual(Array<number>(9).fill(10000));
  });

  it("fails the install when it is an asset that never answers", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    // The asset loop has the same hole and the same fix, but the opposite
    // resolution: a shell missing its entry chunk must not install, so the
    // deadline arrives as a rejection and takes the install down cleanly
    // instead of leaving it hanging.
    worker.hang("/assets/index-CY0Sh5Q2.js");
    worker.respond("/assets/index-nhBuGG1b.css", css(SHELL_CSS));
    await expect(worker.install()).rejects.toThrow();
    expect(worker.keys()).toEqual([]);
  });

  it("fails the install when it is the shell itself that never answers", async () => {
    const worker = loadWorker(source);
    worker.hang("/index.html");
    await expect(worker.install()).rejects.toThrow();
    expect(worker.keys()).toEqual([]);
  });

  it("fails the install when the shell answers and its body never arrives", async () => {
    // The hang one layer in, and the one a deadline on the REQUEST does not
    // reach: fetch() resolves on the headers, so this install holds a 200
    // text/html whose `text()` never settles. Undeadlined it does not fail —
    // it waits, inside waitUntil, until the browser gives up on the install and
    // leaves the phone on whatever worker it had, which is the same outcome as
    // the black hole above through a door the request's own deadline cannot
    // see. A test for it hangs rather than failing, which is why the assertion
    // is on the rejection.
    const worker = loadWorker(source);
    worker.respond("/index.html", stalled());
    await expect(worker.install()).rejects.toThrow("deadline exceeded");
    expect(worker.keys()).toEqual([]);
  });

  it("fails the install when it is the stylesheet's body that never arrives", async () => {
    // The second read, and the one nothing else in this file would notice: the
    // stylesheet is fetched, validated and then read again to scrape @font-face
    // out of it. A shell whose CSS body never finishes is not installable — the
    // bytes to cache are not all there — so this fails rather than installing a
    // stylesheet that is half a file.
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    worker.respond("/assets/index-nhBuGG1b.css", stalled("text/css"));
    await expect(worker.install()).rejects.toThrow("deadline exceeded");
    expect(worker.keys()).toEqual([]);
  });

  it("fails the install when it is the entry chunk's body that never arrives", async () => {
    // The body nothing in this file reads — except `Cache.put`, which reads
    // every body it is handed to the end before it writes. So a put of a
    // network response is a body read, and without a deadline of its own this
    // one waits for ever: measured in Chromium, a shell naming a chunk whose
    // headers arrive and whose bytes stall left the install "installing" for
    // minutes. It fails here, before a single write — the shell included, since
    // a new shell naming a chunk the cache does not hold is a white screen
    // offline, in the cache the previous worker is still serving from.
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", stalled("text/javascript"));
    worker.respond("/assets/index-nhBuGG1b.css", css(SHELL_CSS));
    await expect(worker.install()).rejects.toThrow("deadline exceeded");
    expect(worker.keys()).toEqual([]);
    expect(worker.puts).toEqual([]);
  });

  it("installs without a font whose body never arrives", async () => {
    // The same hang on the loop that is allowed to lose things: a typeface
    // whose bytes stall is declined like one that never answers, and the
    // install carries on without it rather than waiting on its put for ever.
    const worker = withFonts();
    worker.respond(SHELL_FONTS[0], stalled("font/woff2"));
    await worker.install();
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", ...SHELL_ASSETS, SHELL_FONTS[1], "/index.html"].sort(),
    );
    expect(worker.puts).not.toContain(SHELL_FONTS[0]);
  });
});

describe("activate", () => {
  /** The absolute key a cache entry is really stored under. */
  const at = (path: string, origin = ORIGIN) => `${origin}${path}`;

  it("deletes every cache this worker does not own", async () => {
    const worker = shell();
    // Neither is empty: "reach every byte an earlier version wrote" is the
    // point of the version purge, and a previous version's cache is full of
    // them — a whole build's shell, assets and fonts under hashed names that
    // nothing will ever ask for again.
    worker.store.set(
      "alfred-shell-v0",
      new Map([[at("/index.html"), html("<!doctype html><html>the build before</html>")]]),
    );
    // Not prefixed `alfred-`, deliberately. Anything this origin holds that is
    // not the current cache is not ours to keep: a Workbox trial's precache, an
    // experiment, whatever the outgoing SPA left behind.
    worker.store.set(
      "workbox-precache-v2",
      new Map([[at("/assets/index-OLDER.js"), js("older")]]),
    );
    await worker.install();
    await worker.activate();
    // Read from the worker's own VERSION, so a bump moves this test rather than
    // making it vacuous.
    expect([...worker.store.keys()]).toEqual([worker.cache]);
  });

  it("deletes the assets of a build this worker did not precache", async () => {
    const worker = shell();
    await worker.install();
    // The previous deploy's entry chunk AND its stylesheet, left in the cache by
    // the previous worker — which had the same VERSION, because most deploys do
    // not bump it, so the cache is not dropped wholesale and this is the only
    // thing that reaches them. Nothing will ever ask for either: their URLs are
    // content-hashed. Two of them, not one, because a real deploy leaves at
    // least two and a purge that deletes one and stops passes every
    // single-entry fixture while the cache grows by a build on every activate.
    const entries = worker.store.get(worker.cache)!;
    entries.set(at("/assets/index-OLD.js"), js("old"));
    entries.set(at("/assets/index-OLD.css"), css("old"));
    // And a sidecar whose URL EXTENDS one the record names — what Vite emits
    // with sourcemaps on. A keep-set matched by prefix rather than by equality
    // keeps it for ever.
    entries.set(at("/assets/index-CY0Sh5Q2.js.map"), js("{}"));
    await worker.activate();
    expect(worker.keys()).not.toContain("/assets/index-OLD.js");
    expect(worker.keys()).not.toContain("/assets/index-OLD.css");
    expect(worker.keys()).not.toContain("/assets/index-CY0Sh5Q2.js.map");
    expect(worker.keys()).toContain("/assets/index-CY0Sh5Q2.js");
  });

  it("keeps everything when the record is not a list", async () => {
    const worker = shell();
    await worker.install();
    // A valid JSON body that is not an array. `[MANIFEST, ...record]` spreads a
    // string into single characters, none of which resolve to a real key, so
    // index.html, the entry chunk and the stylesheet all go — and nothing
    // rejects, so the first anyone hears of it is a blank screen on a phone
    // with no network. `precache()` always writes an array today; this is the
    // guard for the edit that changes the record's shape.
    worker.store
      .get(worker.cache)!
      .set(at("/__shell-manifest"), new Response(JSON.stringify("/index.html")));
    await worker.activate();
    expect(worker.keys()).toEqual(["/__shell-manifest", ...SHELL_ASSETS, "/index.html"]);
    expect(worker.claimCalls).toBe(1);
  });

  it("keeps everything when the record cannot be read at all", async () => {
    const worker = shell();
    worker.store.set("alfred-shell-v0", new Map());
    await worker.install();
    const entries = worker.store.get(worker.cache)!;
    entries.set(at("/assets/index-OLD.js"), js("old"));
    // Not JSON. `/__shell-manifest` is not a real endpoint — `mount_spa` answers
    // any unknown path with index.html and a 200 — so a page of HTML is what the
    // network would put under that key. Unguarded, `.json()` rejects, which
    // takes `purge()` down AFTER the version purge has deleted the old world and
    // BEFORE the entry purge runs, and the claim never happens: old cache gone,
    // stale entries kept, pages left on the previous worker.
    entries.set(at("/__shell-manifest"), html("<!doctype html><html></html>"));
    await expect(worker.activate()).resolves.toBeUndefined();
    // The version purge still finished.
    expect([...worker.store.keys()]).toEqual([worker.cache]);
    // Everything is kept, the stale entry included: a record it cannot read is
    // not a licence to guess which entries were ours.
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", "/assets/index-OLD.js", ...SHELL_ASSETS, "/index.html"].sort(),
    );
    expect(worker.claimCalls).toBe(1);
  });

  it("deletes the caches after one that refuses to go, and still prunes and claims", async () => {
    const worker = shell();
    worker.store.set("alfred-shell-v0", new Map());
    worker.store.set("workbox-precache-v2", new Map());
    // The one failure a real CacheStorage produces here. Uncaught, it aborts
    // every cache after it, skips the entry purge entirely and skips the claim —
    // one transient error and the recovery path does nothing at all.
    worker.refuseDelete("alfred-shell-v0");
    await worker.install();
    worker.store.get(worker.cache)!.set(at("/assets/index-OLD.js"), js("old"));
    await worker.activate();
    expect([...worker.store.keys()]).toEqual(["alfred-shell-v0", worker.cache]);
    expect(worker.keys()).not.toContain("/assets/index-OLD.js");
    expect(worker.claimCalls).toBe(1);
  });

  it("prunes the entries after one that refuses to go, and still claims", async () => {
    const worker = shell();
    await worker.install();
    const entries = worker.store.get(worker.cache)!;
    entries.set(at("/assets/index-OLD.js"), js("old"));
    entries.set(at("/assets/index-OLD.css"), css("old"));
    worker.refuseDelete("/assets/index-OLD.js");
    await worker.activate();
    // The one that refused is still there, which nothing can help. The one
    // behind it in the loop is not, and the pages are claimed.
    expect(worker.keys()).toContain("/assets/index-OLD.js");
    expect(worker.keys()).not.toContain("/assets/index-OLD.css");
    expect(worker.claimCalls).toBe(1);
  });

  it("keeps every byte this install wrote, fonts and all", async () => {
    const worker = withFonts();
    await worker.install();
    const wrote = worker.keys();
    worker.store.get(worker.cache)!.set(at("/assets/index-OLD.js"), js("old"));
    await worker.activate();
    // The other half, and the one that fails in the safe-looking direction: a
    // keep-set built from the wrong field, or a manifest read the wrong way,
    // empties the cache while every "it deleted the stale thing" assertion
    // still passes, and the next cold launch is a blank screen. The fonts are
    // in here because they are the entries install appends LAST, which is where
    // an off-by-one in the record shows up.
    expect(worker.keys()).toEqual(wrote);
  });

  it("keeps its own record of what it cached", async () => {
    const worker = shell();
    await worker.install();
    await worker.activate();
    // Pruning the manifest would leave the next activate with nothing to prune
    // against, and it would drop the whole cache.
    expect(worker.keys()).toContain("/__shell-manifest");
    // Intact, not merely present: a purge that rewrote the record with what it
    // happened to keep would pass the line above and delete the shell the first
    // time anything was missing from it.
    const listed = await worker.store.get(worker.cache)!.get(at("/__shell-manifest"))!.clone().json();
    expect(listed).toEqual(["/index.html", ...SHELL_ASSETS]);
  });

  it("leaves the activate after it something to prune against", async () => {
    const worker = withFonts();
    await worker.install();
    await worker.activate();
    const wrote = worker.keys();
    // A deploy that does not bump VERSION installs into the same cache, and the
    // activate after it prunes against the same record. The harness cannot load
    // two workers over one store, so this second activate stands in for the
    // next build's — same bytes, same cache, same record. If the first purge
    // consumed or dropped the record, this one keeps nothing.
    worker.store.get(worker.cache)!.set(at("/assets/index-OLD.js"), js("old"));
    await worker.activate();
    expect(worker.keys()).toEqual(wrote);
  });

  it("takes control of the pages already open", async () => {
    const worker = shell();
    await worker.install();
    await worker.activate();
    expect(worker.claimCalls).toBe(1);
  });

  it("claims the open pages only once the purge has finished", async () => {
    const worker = shell();
    worker.store.set("alfred-shell-v0", new Map());
    await worker.install();
    worker.store.get(worker.cache)!.set(at("/assets/index-OLD.js"), js("old"));
    // `claimCalls` at the moment of each deletion. A worker that claims first —
    // `waitUntil(purge()); self.clients.claim()`, or `Promise.all([purge(),
    // claim()])` — waits on everything a real ExtendableEvent waits on and
    // passes every other test in this block, while handing the open pages a
    // worker whose cache is still being emptied underneath them.
    const claimedBy: number[] = [];
    const watch = <K>(map: Map<K, unknown>) => {
      const real = map.delete.bind(map);
      map.delete = (key: K) => {
        claimedBy.push(worker.claimCalls);
        return real(key);
      };
    };
    watch(worker.store); // the version purge
    watch(worker.store.get(worker.cache)!); // the entry purge
    await worker.activate();
    // Two deletions, each of them before the claim. Spelled as a list rather
    // than `.every()`, which an empty list satisfies.
    expect(claimedBy).toEqual([0, 0]);
    expect(worker.claimCalls).toBe(1);
  });

  it("survives activating with an empty cache", async () => {
    // The kill switch has run, or a user cleared site data, or this is the
    // first activate after an install that failed. Nothing to prune against,
    // and dropping everything would be the wrong reading of that.
    const worker = shell();
    await expect(worker.activate()).resolves.toBeUndefined();
    expect([...worker.store.keys()]).toEqual([worker.cache]);
    // And it still claims. A purge that threw on the missing record would take
    // the claim down with it and leave the open pages on the old worker.
    expect(worker.claimCalls).toBe(1);
  });

  it("keeps a cache it has no record for rather than emptying it", async () => {
    const worker = shell();
    await worker.install();
    worker.store.get(worker.cache)!.delete(at("/__shell-manifest"));
    await worker.activate();
    // No path in this worker reaches this today — install writes the record or
    // writes nothing. It is the READING of a missing record, and the reading
    // has to be "I cannot tell what is mine, so I keep it": the alternative
    // turns one absent bookkeeping entry into a blank screen on the next cold
    // launch, which is the failure the whole phase exists to prevent.
    expect(worker.keys()).toEqual([...SHELL_ASSETS, "/index.html"].sort());
  });

  it("deletes an API response nothing should ever have cached", async () => {
    const worker = shell();
    await worker.install();
    // Decision 3's failure with a longer half-life: a stale `dnd: false` served
    // from cache, with no stamp and no way for the reader to know. Nothing in
    // this worker can write one — but the purge is the recovery path for a
    // build that could have, and for whatever a previous sw.js of this same
    // VERSION left, which is every deploy that does not bump it.
    worker.store.get(worker.cache)!.set(at("/api/admin/overview"), js('{"dnd":false}'));
    await worker.activate();
    expect(worker.keys()).not.toContain("/api/admin/overview");
  });

  it("keeps everything when the cached shell cannot be read", async () => {
    // The prune's keep-set is install's record UNION what the cached shell
    // names, and the second half is a cache read, which can fail on its own —
    // the disk, not the lookup. Read the record, fail to read the shell, prune
    // anyway, and every runtime entry goes on the strength of half an answer.
    // The reading is the same as for a record that will not parse: keep
    // everything, and let the next install rewrite both.
    const worker = shell();
    await worker.install();
    const orphan = "/assets/index-D4kq2mZa.js";
    worker.store.get(worker.cache)!.set(at(orphan), js("console.log(2)"));
    worker.refuseMatch("/index.html");
    await worker.activate();
    expect(worker.keys()).toContain(orphan);
  });

  it("deletes a cross-origin entry whose path matches one it keeps", async () => {
    const worker = shell();
    await worker.install();
    // Same pathname, different origin — a CDN copy of the entry chunk that a
    // previous build of this same VERSION stored. Nothing this worker writes
    // can look like this, which is exactly why the purge is the only thing that
    // would ever remove it; a keep-set compared on pathnames keeps it for ever.
    const poisoned = "https://cdn.example.invalid/assets/index-CY0Sh5Q2.js";
    worker.store.get(worker.cache)!.set(poisoned, js("cdn"));
    await worker.activate();
    // On the absolute keys, not `worker.keys()`, which maps to pathnames and
    // cannot tell the two apart.
    const urls = [...worker.store.get(worker.cache)!.keys()];
    expect(urls).not.toContain(poisoned);
    expect(urls).toContain(at("/assets/index-CY0Sh5Q2.js"));
  });
});

/**
 * Every path this worker must never answer, with the mode the real client sends
 * for that URL and why it exists. One table, driven twice: through `isRefused`
 * directly, where every row is a live assertion today, and through a dispatched
 * event, where a row can only fail once a branch exists that could have claimed
 * it. The navigate rows are that; the `cors` rows fall out of both branches
 * unhandled whatever the guard says, and the direct half is what pins them. No
 * row is given `navigate` it would not really arrive with.
 */
const NEVER_ANSWERED: readonly (readonly [path: string, mode: string, why: string])[] = [
  ["/api/admin/overview", "cors", "the Room's own read; src/lib/api.ts calls fetch()"],
  ["/api/auth/status", "cors", "the gate read every launch begins with"],
  ["/api/actions/pending", "cors", "the Door's poll"],
  ["/api/actions/a91f3c2e", "cors", "a deep link's own read"],
  [
    "/api/admin/streams/user_requests?count=50",
    "cors",
    "a Workshop stream read — a polled REST GET with a query string, exactly as src/App.test.tsx mocks it; there is no SSE anywhere in this repo, and `chat` is not in STREAM_CATALOG either",
  ],
  ["/health", "navigate", "the iOS client's healthcheck; this client never fetches it, so only a human gets here"],
  ["/ws", "navigate", "a WebSocket upgrade is not a fetch, so likewise"],
  ["/ws/telemetry", "navigate", "likewise"],
  ["/docs", "navigate", "FastAPI's Swagger UI — 200 text/html, and outside /api/"],
  ["/docs/oauth2-redirect", "navigate", "HTML from the same source"],
  ["/redoc", "navigate", "likewise"],
  ["/openapi.json", "navigate", "not HTML, and not this app either"],
];

/**
 * `sw.js`'s own refusal, called directly.
 *
 * The worker is a classic script and exports nothing, so a policy function
 * inside it can otherwise only be tested through whatever branch consults it.
 * For this one that is not good enough twice over. There is no branch at all
 * until the navigation and asset handlers land — so the dispatched half below
 * can only fail from that point on. And the paths it must NOT refuse stay
 * invisible from outside even afterwards: "refused" and "not handled" both come
 * back as `undefined`, and a guard that has quietly widened looks exactly like
 * one that has not. `loadWorker(source, { expose: ["isRefused"] })` is the seam,
 * and it goes red if the file renames the function — the same reach
 * `worker.cache` makes into VERSION.
 */
const isRefused = loadWorker(source, { expose: ["isRefused"] }).inner.isRefused!;

const refuses = (spec: RequestSpec): boolean => isRefused(fetchRequest(spec));

describe("what the worker refuses to touch", () => {
  it.each(NEVER_ANSWERED)("refuses %s (%s) — %s", (path, mode) => {
    // True means the fetch handler never calls respondWith for it, so no branch
    // downstream can store it, serve it, or see it at all — stronger than
    // "network-only". Decision 3: a cached API response is this client stating
    // a server fact it has not just read, with a half-life. A stale `alive`, a
    // stale `dnd: false`, no stamp, and no way for the reader to know.
    expect(refuses({ path, mode })).toBe(true);
  });

  it.each([
    ["/", "the Room itself"],
    ["/actions/a91f3c2e", "the deep link a notification lands on"],
    ["/ws-telemetry", "a path that looks like a socket and is not one"],
    ["/workshop", "an app path that shares letters with the socket and no prefix"],
    ["/apifoo", "an api-prefixed path that is not under /api/"],
    ["/assets/vendor/api/x.js", "a hashed asset with /api/ inside its path"],
    ["/healthz", "a health-prefixed path that is not /health"],
    ["/docsx", "a docs-prefixed path FastAPI does not serve"],
    ["/docs/foo", "a path under /docs/ that is the app, not Swagger"],
    ["/assets/index-CY0Sh5Q2.js", "the entry chunk"],
    ["/manifest.json", "a public file no branch answers, which is a different thing"],
    ["/__shell-manifest", "this worker's own record"],
  ])("does not refuse %s — %s", (path) => {
    // Each is a path some plausible widening swallows. Measured against the live
    // app on :8081: /apifoo, /healthz, /docsx, /docs/foo and /workshop are all
    // 200 text/html — the app itself — so widening /api, /health or /docs to a
    // bare prefix hands a phone with no network a browser error page instead of
    // the app. /ws-telemetry is the exception and is here for the anchor rather
    // than the cost: spa.py's ("api/", "ws") prefixes make every /ws* path a
    // 404, so refusing it would cost nothing visible. It is still the fixture
    // that carries the `startsWith("/ws")` mutant, which is what it is for.
    expect(refuses({ path })).toBe(false);
  });

  it.each([
    ["/%68ealth", "/health"],
    ["/healt%68", "/health"],
    ["/%61pi/admin/overview", "/api/admin/overview"],
    ["/ap%69/admin/overview", "/api/admin/overview"],
    ["/api%2fadmin/overview", "/api/admin/overview"],
    ["/%77s", "/ws"],
    ["/ws%2ftelemetry", "/ws/telemetry"],
    ["/%64ocs", "/docs"],
  ])("refuses %s, which uvicorn routes as %s", (path) => {
    // The single worst hole this guard has had. uvicorn unquote()s the whole
    // path before Starlette routes it, so one percent-escape used to defeat the
    // entire list. Every one of these was measured against the live app: the
    // health rows return the real health JSON, the api rows the real 401, the ws
    // rows the sockets' 404, and /%64ocs Swagger's own HTML.
    expect(refuses({ path })).toBe(true);
  });

  it.each(["/%zz", "/%", "/%FF"])("refuses %s, which it cannot decode at all", (path) => {
    // decodeURIComponent throws on all three; Python's unquote leaves the first
    // two as literal text and replaces the third. That disagreement is the whole
    // reason — a path this worker and the server read differently must not be
    // answered out of a cache, and the thing it would be guessing with is the
    // only offline copy of the app the phone has. The server answers /%zz with
    // the shell, so the cost is one malformed URL that boots online only.
    expect(refuses({ path })).toBe(true);
  });

  it.each([
    ["/%2e%2e%2fapi/admin/overview", "decodes to /../api/admin/overview"],
    ["/assets/..%2fapi/admin/overview", "decodes to /assets/../api/…"],
    ["/x%2f..%2f..%2fhealth", "decodes to /x/../../health"],
    ["/%2e%2fapi/admin/overview", "decodes to /./api/admin/overview"],
  ])("refuses %s, which %s", (path) => {
    // What replaced the %2f special case. `new URL` normalises /x/../api/y and
    // even /%2e%2e/api/y down to /api/y before this runs — asserted below — but
    // it does NOT normalise an encoded separator, so the decoded form is where
    // the traversal is still visible.
    //
    // None of these reaches the API today, measured: rows 1 and 3 are 200
    // text/html (Starlette does not normalise dot segments, so the unroutable
    // path falls through to mount_spa) and row 2 is a 404 from the /assets
    // mount. Refusing them is not about today's routing — it is that the raw
    // pathname of row 2 still begins "/assets/", the prefix the asset branch
    // reads, so a server that ever did normalise would have it cache a
    // 401 as a hashed asset. The last row is the lone "." held to the same rule
    // and is what makes that arm of the `some` bite; without it a mutant that
    // drops it kills nothing.
    expect(refuses({ path })).toBe(true);
  });

  it("reads the pathname the URL parser has already normalised", () => {
    // Why there is no rule for a plain dot segment: there is nothing left to
    // catch by the time anything here looks.
    expect(new URL("/assets/../api/admin/overview", ORIGIN).pathname).toBe("/api/admin/overview");
    expect(new URL("/%2e%2e/api/admin/overview", ORIGIN).pathname).toBe("/api/admin/overview");
    expect(refuses({ path: "/assets/../api/admin/overview" })).toBe(true);
    expect(refuses({ path: "/%2e%2e/api/admin/overview" })).toBe(true);
  });

  it("judges the pathname and not the query", () => {
    // A %2F in a query says nothing about which path the server routes to, and
    // this worker drops the query from every cache key anyway. Refusing on it
    // would turn an ordinary navigation into a browser error page offline.
    expect(refuses({ path: "/?next=%2Fassets%2Fx" })).toBe(false);
    // And an encoded separator in the path that decodes to something the server
    // does NOT route specially is likewise ordinary: /%2Fhealth decodes to
    // //health, which the live app answers with the shell, measured.
    expect(refuses({ path: "/%2Fhealth" })).toBe(false);
  });

  it.each(["POST", "PUT", "PATCH", "DELETE", "HEAD"])("refuses a %s", (method) => {
    // Never a shell request, and the Cache API cannot store one anyway. `/` is
    // the path a GET is most certainly not refused for, so this is the method
    // and nothing else — the second assertion is what says so.
    expect(refuses({ path: "/", method, mode: "navigate" })).toBe(true);
    expect(refuses({ path: "/", mode: "navigate" })).toBe(false);
  });

  it("refuses a GET that asks for part of a body", () => {
    // A Range asks for bytes 0-1023; a cache-first branch answers with the whole
    // file and a 200, which is a different answer to a different question and is
    // the canonical way a service worker stalls a media element in Safari.
    // Nothing in this client sends one — there is no <audio> or <video> anywhere
    // in src/, and a spoken reply arrives over /ws as base64 and is decoded
    // through an AudioContext (src/lib/audio.ts). Named for the reason /ws is:
    // so the branch that would break under one is never written.
    expect(
      refuses({ path: "/assets/index-CY0Sh5Q2.js", headers: { Range: "bytes=0-1023" } }),
    ).toBe(true);
    // The same request without the header is the one the asset branch serves
    // from cache.
    expect(refuses({ path: "/assets/index-CY0Sh5Q2.js" })).toBe(false);
  });

  it("refuses a blob: URL, whose origin is this origin", () => {
    const url = `blob:${ORIGIN}/9b1c2e3a-4d5f-6071-8293-a4b5c6d7e8f9`;
    // The reason the guard cannot be the origin comparison alone: a blob: URL's
    // origin is the origin INSIDE it, so it compares equal to ours, while its
    // "pathname" is a whole URL and nothing this worker could fetch or key on.
    expect(new URL(url).origin).toBe(ORIGIN);
    expect(refuses({ path: url })).toBe(true);
  });

  it("refuses a data: URL", () => {
    // Origin "null" here, so the origin comparison catches this one — but it is
    // the same question as the blob: above, and answered in the same clause.
    expect(refuses({ path: "data:text/javascript,alert(1)" })).toBe(true);
  });

  it("refuses another origin", () => {
    expect(
      refuses({ path: "/assets/index-CY0Sh5Q2.js", origin: "https://cdn.example.invalid" }),
    ).toBe(true);
  });

  it("refuses a protocol-relative URL, which is another origin wearing a path", () => {
    // `//host/path` looks like a path and is not one: it resolves against the
    // scheme alone, so this is cdn.example.invalid's file, not ours.
    expect(refuses({ path: "//cdn.example.invalid/assets/index-CY0Sh5Q2.js" })).toBe(true);
  });

  it("refuses a host hiding behind credentials", () => {
    const url = "https://alfred.example.com@evil.invalid/assets/index-CY0Sh5Q2.js";
    // Everything before the @ is a username, so this is evil.invalid. Reading it
    // correctly is why the comparison is on `url.origin` and never on the href.
    expect(new URL(url).host).toBe("evil.invalid");
    expect(refuses({ path: url })).toBe(true);
  });

  it("does not refuse credentials on our own host, which are still us", () => {
    // The other half of the same measurement: credentials are not part of an
    // origin, so this IS this origin and is treated as it. Nothing stores them
    // either — the asset branch keys the cache on the pathname, not the href.
    expect(refuses({ path: "https://user:pass@alfred.example.com/assets/index-CY0Sh5Q2.js" })).toBe(
      false,
    );
  });

  it("refuses a host that merely begins with this origin", () => {
    // What `startsWith` on the origin would swallow: a host can be registered
    // with ours as a prefix, and a prefix test reads it as us. Equality is the
    // only reading of "same origin" there is — and this is the fixture that
    // says so, found by a mutant that survived every other origin row here.
    const url = `${ORIGIN}.evil.invalid/assets/index-CY0Sh5Q2.js`;
    expect(new URL(url).host).toBe("alfred.example.com.evil.invalid");
    expect(refuses({ path: url })).toBe(true);
  });

  it("judges the decoded pathname alone, which loses nothing", () => {
    // Why the raw pathname is not also tested against the lists, though it was
    // at first: every entry in both lists is escape-free, and decoding rewrites
    // nothing but escapes, so a raw pathname the lists match decodes to one
    // they still match. A raw term could refuse nothing the decoded one does
    // not — no fixture can distinguish them, and none should be written to try.
    // The mutant that deleted it killed no test, which is what said so.
    expect(decodeURIComponent("/api/admin/overview")).toBe("/api/admin/overview");
    expect(decodeURIComponent("/api/%2e%2e").startsWith("/api/")).toBe(true);
    expect(refuses({ path: "/api/admin/overview" })).toBe(true);
  });

  it("refuses the two socket paths and nothing that merely resembles them", () => {
    // Both directions, said once, because each is invisible on its own: a guard
    // that matches neither socket looks exactly like one that matches both from
    // any test that only asks about app paths, and a guard that swallows an app
    // path looks exactly like a correct one from any test that only asks about
    // sockets. /ws and /ws/telemetry are the whole of what core/ serves
    // (core/channels/web_server.py:454, core/channels/telemetry_ws.py:43).
    expect(["/ws", "/ws/telemetry"].map((path) => refuses({ path }))).toEqual([true, true]);
    expect(["/ws-telemetry", "/wsx", "/workshop"].map((path) => refuses({ path }))).toEqual([
      false,
      false,
      false,
    ]);
    // And the reading that produced the wrong fixture first time round, measured
    // instead of assumed: `/workshop` does not begin "/ws" — w, o, r — so no
    // prefix guard on the socket reaches it, and it is `/ws-telemetry` that
    // carries that mutant.
    expect("/workshop".startsWith("/ws")).toBe(false);
    expect("/ws-telemetry".startsWith("/ws")).toBe(true);
  });

  it("does not refuse its own origin when that origin is http://localhost", () => {
    // A worker is a secure context, which is https everywhere EXCEPT localhost —
    // and `npm run preview` serves a production build there, as does the
    // container's own port. A scheme guard written to the literal "https:"
    // refuses every request on that origin, silently, and the only symptom is
    // that nothing is ever cached. The scope is what says which scheme is ours.
    const local = "http://localhost:8081";
    const worker = loadWorker(source, { expose: ["isRefused"], scope: `${local}/` });
    const refusesAt = (path: string): boolean =>
      worker.inner.isRefused!(fetchRequest({ path, origin: local }));
    expect(refusesAt("/assets/index-CY0Sh5Q2.js")).toBe(false);
    // And the list still bites there, which is what says the scheme is the only
    // thing that moved.
    expect(refusesAt("/api/admin/overview")).toBe(true);
    // The https origin is another origin from there, as it is to a browser.
    expect(refusesAt(`${ORIGIN}/assets/index-CY0Sh5Q2.js`)).toBe(true);
  });
});

describe("the fetch handler, and what it declines to answer", () => {
  // `undefined` means respondWith was never called. A row of the table can only
  // FAIL here once a branch exists that could have claimed it: the navigate rows
  // are claimed by the navigation branch, so those have been live since it
  // landed, and the `cors` rows fall out of both branches unhandled whatever
  // the guard says. The direct block above is what pins those, and is
  // also the only place the must-NOT-refuse half can be said at all.
  it.each(NEVER_ANSWERED)("leaves %s (%s) to the network — %s", async (path, mode) => {
    const worker = shell();
    await worker.install();
    await expect(worker.request({ path, mode })).resolves.toBeUndefined();
  });

  it("caches nothing when an API call goes past it", async () => {
    const worker = shell();
    await worker.install();
    const before = worker.keys();
    await worker.request({ path: "/api/admin/overview", mode: "cors" });
    expect(worker.keys()).toEqual(before);
  });

  it("does not write Swagger UI into the shell cache", async () => {
    // The reproduction, end to end: /docs is 200 text/html and outside /api/, so
    // a navigation branch that saw it would pass isShell() and overwrite the
    // cached shell — and every offline launch afterwards would render Swagger
    // until the next online navigation. The cache assertion is the one that
    // matters; the undefined is how it is achieved.
    const worker = shell();
    await worker.install();
    worker.respond("/docs", html("<!doctype html><title>Alfred Web Channel - Swagger UI</title>"));
    await expect(worker.request({ path: "/docs", mode: "navigate" })).resolves.toBeUndefined();
    const cached = worker.store.get(worker.cache)!.get(`${ORIGIN}/index.html`)!;
    await expect(cached.clone().text()).resolves.toContain('<div id="root">');
  });

  it("leaves a navigation-mode request to an API path alone too", async () => {
    // /api/* is judged before any branch, deliberately: a hand-typed API URL in
    // the address bar arrives with mode "navigate", and the navigation branch
    // would otherwise cache whatever came back as the app shell.
    const worker = shell();
    await worker.install();
    await expect(
      worker.request({ path: "/api/admin/overview", mode: "navigate" }),
    ).resolves.toBeUndefined();
  });

  it("leaves every non-GET alone", async () => {
    const worker = shell();
    await worker.install();
    for (const method of ["POST", "PUT", "PATCH", "DELETE", "HEAD"]) {
      await expect(
        worker.request({ path: "/", method, mode: "navigate" }),
      ).resolves.toBeUndefined();
    }
  });

  it("leaves a ranged GET for a precached asset alone", async () => {
    // Cached, so the asset branch would otherwise answer it out of the cache
    // with a whole body and a 200.
    const worker = shell();
    await worker.install();
    expect(worker.keys()).toContain("/assets/index-CY0Sh5Q2.js");
    await expect(
      worker.request({
        path: "/assets/index-CY0Sh5Q2.js",
        headers: { Range: "bytes=0-1023" },
      }),
    ).resolves.toBeUndefined();
  });

  it("leaves another origin alone", async () => {
    const worker = shell();
    await worker.install();
    await expect(
      worker.request({ path: "/assets/index-CY0Sh5Q2.js", origin: "https://example.invalid" }),
    ).resolves.toBeUndefined();
  });

  it("leaves a percent-escaped API path alone, in navigation mode", async () => {
    // The live hole, dispatched rather than called: /%61pi/admin/overview
    // returns the real API's 401 and a navigation branch would have cached it.
    const worker = shell();
    await worker.install();
    await expect(
      worker.request({ path: "/%61pi/admin/overview", mode: "navigate" }),
    ).resolves.toBeUndefined();
  });

  it("leaves the unhashed public files alone", async () => {
    // Not a refusal: these fall out of the branches unanswered, which the block
    // above says in the one way that can tell the two apart. All four are read
    // while the phone is online (at install time, and by the browser chrome),
    // all four are revalidated by SpaCacheMiddleware's third tier, and a stale
    // manifest is the one thing a reinstall cannot fix.
    const worker = shell();
    await worker.install();
    for (const path of [
      "/manifest.json",
      "/manifest-light.json",
      "/icon.svg",
      "/apple-touch-icon.png",
    ]) {
      await expect(worker.request({ path })).resolves.toBeUndefined();
    }
  });
});

/** What a cold offline launch must render: the shell, not whatever else was 200. */
const ROOT_DIV = '<div id="root">';

/** The cached shell, read out of the store as the object the worker put there. */
const shellInCache = (worker: Harness): Response =>
  worker.store.get(worker.cache)!.get(`${ORIGIN}/index.html`)!;

/**
 * A deploy's `index.html`: the tags `vite build` really writes — `crossorigin`
 * and all — naming whatever hashes that deploy has, with a marker in the body so
 * one deploy can be told from another. A shell fixture that names nothing is a
 * page the worker now refuses on purpose, which is what this exists to avoid
 * writing by accident.
 */
const deployedHtml = (marker: string, assets: readonly string[] = SHELL_ASSETS): string =>
  `<!doctype html><html lang="en"><head>` +
  assets
    .map((url) =>
      url.endsWith(".css")
        ? `<link rel="stylesheet" crossorigin href="${url}">`
        : `<script type="module" crossorigin src="${url}"></script>`,
    )
    .join("") +
  `</head><body><div id="root"></div><!--${marker}--></body></html>`;

const deployed = (marker: string, assets: readonly string[] = SHELL_ASSETS): Response =>
  html(deployedHtml(marker, assets));

/** The hashes the NEXT deploy writes. Nothing here has them cached. */
const NEXT_ASSETS = ["/assets/index-D4kq2mZa.js", "/assets/index-Cv8pLm1x.css"];

/**
 * A real name from this build that these fixtures' stylesheet does not name, so
 * install never sees it — the shape of a font the install declined, or one a
 * later build splits out.
 */
const LATE_FONT = "/assets/geist-mono-latin-500-normal-YINYabwD.woff2";

/**
 * The request a browser really makes for one of these. Measured in a browser
 * against the real `dist`: every `/assets/*` request is `mode: "cors"` — the
 * entry chunk `destination: "script"`, the stylesheet `"style"`, all eight woff2
 * `"font"` — because `index.html` marks the module script and the stylesheet
 * `crossorigin`. The harness default is `"no-cors"`, which no `/assets/*`
 * request in this app has ever been, and a suite that never sends the real mode
 * cannot see a dispatch keyed on the wrong one: `mode !== "no-cors"` in place of
 * `mode === "navigate"` routes every chunk, stylesheet and typeface through
 * `navigate()` and serves the HTML shell as a JavaScript module offline.
 */
const fromPage = (path: string, destination = "script"): RequestSpec => ({
  path,
  mode: "cors",
  destination,
});

describe("navigations", () => {
  it("serves the live page when there is a network", async () => {
    // Decision 8, and the whole of it: an online phone renders the deploy that
    // is live right now, never the cache. That is what makes a poisoned cache
    // something only an offline phone can see.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("live"));
    const response = await worker.request({ path: "/", mode: "navigate" });
    await expect(response!.text()).resolves.toContain("live");
  });

  it("refreshes the cached shell from that answer", async () => {
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("live"));
    const response = await worker.request({ path: "/", mode: "navigate" });
    // The page is read FIRST and the stored copy second, and that order is the
    // point. A worker that put the response itself instead of a clone passes
    // the other way round and fails in a browser, where `cache.put` spends the
    // body the page is about to read: the navigation dies with the cache
    // correctly written. Read this way, the store is left holding a spent
    // object and `.clone()` on it throws.
    await expect(response!.text()).resolves.toContain("live");
    await expect(shellInCache(worker).clone().text()).resolves.toContain("live");
  });

  it("does not overwrite the shell with an error page that carries the app's own HTML", async () => {
    // The status half of the guard, and the fixture has to name hashed assets or
    // the empty-manifest refusal below is what stops it and this proves nothing.
    // It is the realistic shape too: nginx serving a stale cached copy of the
    // app with `proxy_cache_use_stale` answers 502 with exactly this body.
    // Stored, it would be the app's offline face until the next good launch.
    const worker = shell();
    await worker.install();
    worker.respond(
      "/",
      new Response(deployedHtml("bad gateway"), {
        status: 502,
        headers: { "content-type": "text/html; charset=utf-8" },
      }),
    );
    const response = await worker.request({ path: "/", mode: "navigate" });
    expect(response!.status).toBe(502);
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("bad gateway");
  });

  it("does not cache a page that is not HTML", async () => {
    // The content-type half, and the only thing `isShell` buys over a bare
    // `response.ok`. The body is the app's own HTML under the wrong type —
    // a misconfigured proxy, a host answering with a template it will not admit
    // is a page — because a fixture that names no asset is refused one guard
    // later and would leave this one untested. A browser will not execute it as
    // a document either, so caching it as the shell caches something no launch
    // can render.
    const worker = shell();
    await worker.install();
    worker.respond("/", js(deployedHtml("wrong type")));
    await worker.request({ path: "/", mode: "navigate" });
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("wrong type");
  });

  it("does not publish a page that names no hashed asset", async () => {
    // The refusal `precache()` makes, mirrored on the only other path that ever
    // writes a shell. A 200 `text/html` from this origin naming nothing hashed
    // is not this app: the reverse proxy's own default page while a host is
    // being edited is exactly that. install declines to start from one; without
    // the mirror, the first navigation writes one straight over a good shell and
    // every offline launch afterwards renders it.
    const worker = shell();
    await worker.install();
    worker.respond("/", html("<!doctype html><title>proxy default page</title>"));
    const response = await worker.request({ path: "/", mode: "navigate" });
    // Served, because an online phone gets what the network said…
    await expect(response!.text()).resolves.toContain("proxy default");
    // …and not published, because the offline copy is not the network's to set.
    await expect(shellInCache(worker).clone().text()).resolves.toContain(ROOT_DIV);
  });

  it("publishes a new deploy's shell only once the assets it names are cached", async () => {
    // `sw.js` is byte-identical across most deploys — VERSION is bumped by hand
    // — so a deploy runs no install and no activate, and this is the only thing
    // that ever updates the offline copy of the app.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    await worker.request({ path: "/", mode: "navigate" });
    await expect(shellInCache(worker).clone().text()).resolves.toContain("two");
    expect(worker.keys()).toEqual(expect.arrayContaining(NEXT_ASSETS));
  });

  it("keeps the shell it has when the new deploy's assets cannot be fetched", async () => {
    // The white-screen bug, and the reason the two are published together. The
    // shell names hashes the cache does not hold, `asset()` propagates a network
    // failure by design, and the next cold launch with no network is a blank
    // page rather than the old app. Lose the network between the navigation and
    // the page's own asset requests — a tunnel, a backgrounded app, a rollout
    // mid-flight — and that is the state it stays in.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    // No routes for NEXT_ASSETS: the refresh's fetches reject.
    const response = await worker.request({ path: "/", mode: "navigate" });
    await expect(response!.text()).resolves.toContain("two");
    await expect(shellInCache(worker).clone().text()).resolves.toContain(ROOT_DIV);
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("two");
  });

  it("keeps the shell it has when a new deploy's asset comes back as something else", async () => {
    // The same rule against the other failure: the /assets mount gone and
    // mount_spa answering a hashed URL with index.html and a 200.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], html("<!doctype html>"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    await worker.request({ path: "/", mode: "navigate" });
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("two");
    expect(worker.keys()).not.toContain(NEXT_ASSETS[0]);
  });

  it("keeps the shell it has when a new deploy's asset cannot be stored", async () => {
    // The storage axis crossing the consistency rule. A refresh that does not
    // wait for each asset write before publishing the shell cannot know whether
    // the write happened: the phone is full, the chunk is not there, and the
    // shell naming it goes in anyway. That is the white screen again, reached
    // through the failure the page never sees.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    worker.refusePut(NEXT_ASSETS[0]);
    const response = await worker.request({ path: "/", mode: "navigate" });
    // The page still gets the live deploy…
    await expect(response!.text()).resolves.toContain("two");
    // …and the offline copy is still the one whose assets are all present.
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("two");
    expect(worker.keys()).not.toContain(NEXT_ASSETS[0]);
  });

  it("does not refetch an asset the refreshed shell names and the cache holds", async () => {
    // A deploy that changes one chunk must not re-fetch the other, and the
    // overlap with what the page is fetching for itself is already unavoidable.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", [SHELL_ASSETS[0], NEXT_ASSETS[1]]));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    const before = worker.fetched.length;
    await worker.request({ path: "/", mode: "navigate" });
    const during = worker.fetched.slice(before);
    expect(during).toContain(NEXT_ASSETS[1]);
    expect(during).not.toContain(SHELL_ASSETS[0]);
  });

  it("leaves the install's record alone when it refreshes the shell", async () => {
    // A refresh that also wrote the shell under `/__shell-manifest` would turn
    // `purge()` into a no-op: the record would stop being a list of pathnames,
    // `Array.isArray` would refuse it, and every activate afterwards would keep
    // everything — silently, with nothing rejecting.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("live"));
    await worker.request({ path: "/", mode: "navigate" });
    const record = await worker.store
      .get(worker.cache)!
      .get(`${ORIGIN}/__shell-manifest`)!
      .clone()
      .json();
    expect(record).toEqual(["/index.html", ...SHELL_ASSETS]);
  });

  it("serves the live page when the cache write is refused", async () => {
    // A phone that is full: `cache.put` rejects with QuotaExceededError, and an
    // installed PWA on iOS is exactly the thing that meets it. Handled as a
    // failed fetch — which is what one over-wide `catch` does — this answers a
    // working network out of the cache: decision 8 exactly inverted, and the one
    // rule that keeps a poisoned cache away from an online phone.
    const worker = shell();
    await worker.install();
    worker.refusePut("/index.html");
    worker.respond("/", deployed("live"));
    const response = await worker.request({ path: "/", mode: "navigate" });
    await expect(response!.text()).resolves.toContain("live");
    // And the shell it could not replace is still whole.
    await expect(shellInCache(worker).clone().text()).resolves.toContain(ROOT_DIV);
  });

  it("serves the live page when the write is refused and nothing is cached at all", async () => {
    // The same failure on a phone with no shell to fall back to, which is where
    // the inversion is loudest: a 200 from the network turned into `offline, and
    // no shell cached`.
    const worker = shell();
    worker.refusePut("/index.html");
    worker.respond("/", deployed("live"));
    const response = await worker.request({ path: "/", mode: "navigate" });
    await expect(response!.text()).resolves.toContain("live");
  });

  it("serves the live page when the cache cannot be opened at all", async () => {
    // Safari drops the whole store under storage pressure and in some private
    // modes. `caches.open()` outside the try meant a healthy network answered
    // with the browser's network-error page — a worker actively making a working
    // phone worse than no worker at all.
    const worker = shell();
    await worker.install();
    worker.refuseOpen();
    worker.respond("/", deployed("live"));
    const response = await worker.request({ path: "/", mode: "navigate" });
    await expect(response!.text()).resolves.toContain("live");
  });

  it("serves the cached shell when there is no network", async () => {
    const worker = shell();
    await worker.install();
    // No route for "/" — the harness rejects, exactly as a real fetch does.
    const response = await worker.request({ path: "/", mode: "navigate" });
    await expect(response!.text()).resolves.toContain(ROOT_DIV);
  });

  it("serves the cached shell when the network accepts and never answers", async () => {
    // A hang is indistinguishable from being offline, and the fallback for being
    // offline is the cached shell. Without a deadline this waits for ever, and
    // an installed PWA is where that is worst: a standalone window has no
    // browser chrome to escape a hang with. It is also the arm a narrower
    // `catch (e) { if (!(e instanceof TypeError)) throw e }` would drop — the
    // abort arrives as a DOMException, not a TypeError.
    const worker = shell();
    await worker.install();
    worker.hang("/");
    const response = await worker.request({ path: "/", mode: "navigate" });
    await expect(response!.text()).resolves.toContain(ROOT_DIV);
  });

  it("gives the navigation a deadline, and clears it when the page answers", async () => {
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("live"));
    const before = worker.deadlines.length;
    await worker.request({ path: "/", mode: "navigate" });
    expect(worker.deadlines.slice(before)).toContain(10000);
    // A deadline left armed keeps the worker alive to do nothing, which is
    // least welcome on a phone.
    expect(worker.deadlinesCleared).toBe(worker.deadlines.length);
  });

  it("fails as offline when there is no network and no cache to open", async () => {
    // Not as a storage error: a DOMException escaping from `caches.open()` here
    // would reach the page as an unexplained failure rather than as the one
    // thing that is true — there is nothing to serve.
    const worker = shell();
    await worker.install();
    worker.refuseOpen();
    await expect(worker.request({ path: "/", mode: "navigate" })).rejects.toThrow(
      "offline, and no shell cached",
    );
  });

  it("fails as offline when the cached shell cannot be read", async () => {
    // The read half of the storage axis: the entry is there and the disk will
    // not give it up. `offline, and no shell cached` is the honest answer —
    // there is nothing to serve — and a DOMException escaping instead would
    // reach the page as an unexplained failure.
    const worker = shell();
    await worker.install();
    worker.refuseMatch("/index.html");
    await expect(worker.request({ path: "/", mode: "navigate" })).rejects.toThrow(
      "offline, and no shell cached",
    );
  });

  it("serves the cached shell for a deep link too", async () => {
    // /actions/{id} is a real cold-launch path: a notification tap lands there.
    const worker = shell();
    await worker.install();
    const response = await worker.request({ path: "/actions/a91f3c2e", mode: "navigate" });
    await expect(response!.text()).resolves.toContain(ROOT_DIV);
  });

  it("serves the cached shell for /index.html itself", async () => {
    // The one path that is both the cache key and a real URL a browser can be
    // pointed at. A branch that skipped it — on the reasoning that the cache
    // key is not a route — would answer the app's own address with a network
    // error offline.
    const worker = shell();
    await worker.install();
    const response = await worker.request({ path: "/index.html", mode: "navigate" });
    await expect(response!.text()).resolves.toContain(ROOT_DIV);
  });

  it.each([
    ["/workshop", "an app path that shares letters with the socket and no prefix"],
    ["/ws-telemetry", "a path that looks like a socket and is not one"],
    ["/apifoo", "an api-prefixed path that is not under /api/"],
    ["/healthz", "a health-prefixed path that is not /health"],
    ["/docsx", "a docs-prefixed path FastAPI does not serve"],
    ["/docs/foo", "a path under /docs/ that is the app, not Swagger"],
  ])("serves the cached shell offline for %s — %s", async (path) => {
    // The positive side of the refusal list, and it is only assertable now that
    // a branch exists which could have claimed these: before, a wrongly-refused
    // path and a correctly-unhandled one were the same `undefined`. Every one of
    // these is 200 text/html on the live app — the app itself — so a refusal
    // that widened to a bare prefix would hand an offline phone a browser error
    // page instead of the Room.
    //
    // /ws-telemetry is the exception and is here for the anchor rather than the
    // cost: the server 404s it (spa.py's ("api/", "ws") prefixes), so offline it
    // gets a shell the network would not have given it. That divergence is
    // accepted — it is a fixture, not a route — and it is what carries the
    // `startsWith("/ws")` mutant.
    const worker = shell();
    await worker.install();
    const response = await worker.request({ path, mode: "navigate" });
    await expect(response!.text()).resolves.toContain(ROOT_DIV);
  });

  it("refreshes the shell from an online deep link, under the shell's own key", async () => {
    // One key, whatever path the navigation was for: / and /actions/{id} are
    // served the same bytes by mount_spa, and the offline branch reads SHELL. A
    // worker keyed on the request would cache a shell per deep link — each one
    // pruned by the next activate, and none of them ever read.
    const worker = shell();
    await worker.install();
    worker.respond("/actions/a91f3c2e", deployed("deployed"));
    await worker.request({ path: "/actions/a91f3c2e", mode: "navigate" });
    expect(worker.keys()).not.toContain("/actions/a91f3c2e");
    await expect(shellInCache(worker).clone().text()).resolves.toContain("deployed");
  });

  it("fails a navigation with no network and nothing cached, rather than answering with nothing", async () => {
    // A first launch that never reached a network: the worker is registered, the
    // install never completed, and there is no shell to fall back to. Throwing
    // gets the browser's own network-error page with a line in the console;
    // resolving with undefined reaches the same page through a TypeError inside
    // respondWith, saying nothing.
    const worker = shell();
    await expect(worker.request({ path: "/", mode: "navigate" })).rejects.toThrow(
      "offline, and no shell cached",
    );
  });

  it("answers a navigation straight to an asset URL network-first, and once", async () => {
    // Both branches match this one — mode "navigate" AND a pathname under
    // /assets/ — which is what a pasted asset link or devtools' "open in new
    // tab" really produces. Mode wins, and the cache and the network disagree
    // here on purpose: someone who navigates to a hashed URL is asking what is
    // deployed, and cache-first would answer with what used to be. The `return`
    // is the other half — respondWith twice for one event is an
    // InvalidStateError, and the harness throws on the second call.
    const worker = shell();
    await worker.install();
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(2)"));
    const response = await worker.request({
      path: "/assets/index-CY0Sh5Q2.js",
      mode: "navigate",
    });
    await expect(response!.text()).resolves.toBe("console.log(2)");
    // And the chunk did not become the shell: isShell refuses it.
    await expect(shellInCache(worker).clone().text()).resolves.toContain(ROOT_DIV);
  });

  it("hands the page's own request to the network, not a URL it rebuilt", async () => {
    // A Request carries the page's mode, credentials and redirect mode.
    // Re-fetching `request.url` instead turns `redirect: "manual"` into
    // `"follow"` — the classic way a worker hands a navigation a redirected
    // response it must not have, and invisible to every assertion about bodies.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("live"));
    const before = worker.handed.length;
    await worker.request({ path: "/", mode: "navigate" });
    expect(typeof worker.handed[before]).not.toBe("string");
    expect(worker.handed[before]).toMatchObject({ mode: "navigate", method: "GET" });
  });

  it("does not ask past the HTTP cache for the page's own navigation, only for the refresh", async () => {
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    await worker.request({ path: "/", mode: "navigate" });
    // The navigation is the page's request, forwarded as it arrived: rewriting
    // its cache mode is not this worker's to do.
    expect(worker.reloaded).not.toContain("/");
    // The refresh's own fetches do go past it, for the reason install's do — a
    // deploy that just landed must not be read out of the HTTP cache.
    expect(worker.reloaded).toEqual(expect.arrayContaining(NEXT_ASSETS));
  });

  it("answers a nested navigation too, which is keyed on the mode and not the destination", async () => {
    // `mode: "navigate"` and `destination: "document"` travel together for a
    // TOP-LEVEL navigation and only for one: a navigation inside an <iframe> is
    // mode "navigate", destination "iframe", and <object>/<embed> are the same
    // shape again. So the two are not interchangeable as the branch key, and
    // the mode is the one the Fetch Standard ties to "this is a navigation".
    // Keyed on the destination instead, a nested same-origin navigation falls
    // out of the handler unanswered — no cached shell offline, and no deadline
    // on a hang either, which is the whole of what this file is for.
    const worker = shell();
    await worker.install();
    const nested = { path: "/panel", mode: "navigate", destination: "iframe" };
    const response = await worker.request(nested);
    await expect(response!.text()).resolves.toContain(ROOT_DIV);
  });

  it("gives the navigation a deadline without touching the request to do it", async () => {
    // A deadline that ABORTS needs a signal; a signal is an init; and any init
    // that is not empty makes fetch() rebuild the Request. The Fetch Standard's
    // constructor then does three things to the rebuilt one: `mode: "navigate"`
    // becomes `"same-origin"`, the reload-navigation flag is unset and the
    // history-navigation flag is unset. A reload would stop being a reload, a
    // back/forward navigation would stop being one, and the request would stop
    // being a navigation at all — none of it visible in a body, and none of it
    // constructible here, because undici refuses `mode: "navigate"` outright.
    // So the navigation is fetched with no init and RACED against the deadline
    // instead, and this is the assertion that says so.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    const before = worker.handed.length;
    // Its own snapshot: install arms more deadlines than it makes fetches, so
    // the two indices are not interchangeable.
    const armed = worker.deadlines.length;
    await worker.request({ path: "/", mode: "navigate" });
    expect(worker.fetched[before]).toBe("/");
    expect(worker.inits[before]).toBeUndefined();
    // The refresh's fetches are this worker's own requests rather than the
    // page's, so they are aborted rather than raced, and they carry both halves.
    const refreshed = worker.inits.slice(before + 1);
    expect(refreshed).toHaveLength(2);
    for (const init of refreshed) expect(init).toMatchObject({ cache: "reload" });
    for (const init of refreshed) expect(init!.signal).toBeInstanceOf(AbortSignal);
    // Raced or aborted, the deadline is armed and taken back down either way:
    // one for the navigation, one for the body read of the page it answered
    // with, and two for each asset the refresh fetched — the request, and the
    // body read whole before it is put.
    expect(worker.deadlines.slice(armed)).toEqual(Array<number>(6).fill(10000));
    expect(worker.deadlinesCleared).toBe(worker.deadlines.length);
  });

  it("answers the page before the refresh it started has written anything", async () => {
    // The property `request()` cannot see, because it drains `waitUntil` before
    // it returns: `waitUntil(refresh(…))` written as `await refresh(…)` passes
    // every other assertion in this file and blocks the navigation on a
    // background fetch of the chunks the page is about to ask for itself.
    //
    // Deterministic rather than timing-dependent: this fake's `put` lands in a
    // MACROTASK, and the continuation of `await responded` is a microtask, so
    // no put can have landed by the time these run unless the handler waited
    // for it on purpose.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    const { responded, settled } = worker.dispatch({ path: "/", mode: "navigate" });
    const page = await responded;
    await expect(page!.text()).resolves.toContain("two");
    expect(worker.keys()).not.toContain(NEXT_ASSETS[0]);
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("two");
    // And it does finish, once the event's background half is let run.
    await settled;
    expect(worker.keys()).toEqual(expect.arrayContaining(NEXT_ASSETS));
    await expect(shellInCache(worker).clone().text()).resolves.toContain("two");
  });

  it("keeps the assets of the shell it published through the next activate", async () => {
    // The two halves of this file disagreeing about what the cache is for.
    // install's record names the deploy install fetched; a refresh publishes a
    // NEWER shell and the chunks it names, and neither is in any record. A
    // purge that pruned on the record alone would delete those chunks and keep
    // the shell — its key is in the record — leaving a cached page that boots
    // to a blank screen offline, which is the failure the whole phase exists to
    // prevent, arriving from the inside.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    await worker.request({ path: "/", mode: "navigate" });
    await worker.activate();
    await expect(shellInCache(worker).clone().text()).resolves.toContain("two");
    expect(worker.keys()).toEqual(expect.arrayContaining(NEXT_ASSETS));
    // The record's own entries are kept too — this is a union, not a swap — so
    // the worker that installed them can still be rolled back to.
    expect(worker.keys()).toEqual(expect.arrayContaining([...SHELL_ASSETS]));
  });

  it("serves the page, and keeps the shell it has, when the new one stalls mid-body", async () => {
    // fetch() resolves on the headers, so the refresh holds a 200 whose body
    // never finishes arriving. Without a deadline on the READ, that `text()`
    // never settles: the navigation's own waitUntil stays open, holding the
    // worker awake for a page the user was served ten seconds ago, and this
    // test would hang rather than fail.
    const worker = shell();
    await worker.install();
    worker.respond("/", stalled());
    const response = await worker.request({ path: "/", mode: "navigate" });
    // The page is answered from the network regardless: the refresh is
    // background work, and nothing it does can reach what was served.
    expect(response!.status).toBe(200);
    // And the shell that survives is the one install wrote.
    await expect(shellInCache(worker).clone().text()).resolves.toContain(ROOT_DIV);
  });

  it("serves the page, and keeps the shell it has, when a new deploy's asset stalls mid-body", async () => {
    // The refresh's own fetches, and the put that reads their bodies. A chunk
    // whose headers arrive and whose bytes do not leaves that put pending for
    // ever — inside the navigation's waitUntil, holding the worker awake for a
    // page served long ago, and with the shell never published. Declined like
    // any other asset the refresh cannot get whole: the shell that is there
    // stays. `request()` drains waitUntil, so an unbounded put hangs this test.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], stalled("text/javascript"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    const response = await worker.request({ path: "/", mode: "navigate" });
    await expect(response!.text()).resolves.toContain("two");
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("two");
    expect(worker.puts).not.toContain(NEXT_ASSETS[0]);
  });
});

describe("assets", () => {
  it("serves a hashed asset from the cache without touching the network", async () => {
    // Cache-first, and this is the only branch that is: the filename carries a
    // hash of the bytes, which is the same fact SpaCacheMiddleware serves these
    // `immutable` for a year on (measured on the running app:
    // `cache-control: public, max-age=31536000, immutable`).
    const worker = shell();
    await worker.install();
    const before = worker.fetched.length;
    const response = await worker.request(fromPage("/assets/index-CY0Sh5Q2.js"));
    await expect(response!.text()).resolves.toBe("console.log(1)");
    expect(worker.fetched.length).toBe(before);
  });

  it.each([
    ["/assets/index-CY0Sh5Q2.js", "script"],
    ["/assets/index-nhBuGG1b.css", "style"],
    ["/assets/dm-sans-latin-standard-normal-Bf69Tn_J.woff2", "font"],
  ])("serves %s to the real (cors, %s) request the page makes", async (path, destination) => {
    // The three shapes a real launch produces, all of them `mode: "cors"`.
    const worker = withFonts();
    await worker.install();
    const before = worker.fetched.length;
    const response = await worker.request({ path, mode: "cors", destination });
    expect(response).toBeDefined();
    expect(worker.fetched.length).toBe(before);
  });

  it("serves it for a URL with a query on the end, from the same key", async () => {
    // The cache is read by pathname — install's spelling — so a cache-buster or
    // a ?import query is answered out of it rather than fetched again under a
    // second key that the next activate would prune. There is deliberately no
    // route for the query'd URL, so a worker that looked the request up instead
    // goes red here rather than passing through the network quietly.
    const worker = shell();
    await worker.install();
    const response = await worker.request(fromPage("/assets/index-CY0Sh5Q2.js?v=2"));
    await expect(response!.text()).resolves.toBe("console.log(1)");
  });

  it("serves a precached asset whose name carries an escape", async () => {
    // The key is the RAW pathname, because that is what install stored. Decode
    // it here and the lookup misses on every load, refetches, and leaves a
    // second copy for the prune to collect.
    //
    // The escape is `%25` and not `%20` on purpose, and the difference is the
    // whole reason this test is worth anything: a decoded space is re-encoded
    // when the key is parsed back into a URL, so both spellings land on the same
    // entry and a decoding worker passes. A decoded `%` does not — `new URL`
    // leaves a lone `%` alone — so the two keys really are different. `%23` and
    // `%2F` behave the same way, and the last of those is the traversal
    // `isAssetUrl` already refuses. Vite writes none of them; `/assets` is a
    // StaticFiles mount and is not only Vite's to fill.
    const escaped = "/assets/a%25b-QNjW2pTd.js";
    const worker = loadWorker(source);
    worker.respond(
      "/index.html",
      html(
        `<!doctype html><html lang="en"><head>` +
          `<script type="module" crossorigin src="${escaped}"></script>` +
          `</head><body><div id="root"></div></body></html>`,
      ),
    );
    worker.respond(escaped, js("console.log(3)"));
    await worker.install();
    const before = worker.fetched.length;
    const response = await worker.request(fromPage(escaped));
    await expect(response!.text()).resolves.toBe("console.log(3)");
    expect(worker.fetched.length).toBe(before);
  });

  it("fetches and stores an asset the precache missed", async () => {
    // A font install declined (a proxy's page, a rejected fetch, a deadline) or
    // a chunk a later build split out: absent from the cache, picked up the
    // first time the page asks for it.
    const worker = shell();
    await worker.install();
    worker.respond(LATE_FONT, font());
    const response = await worker.request(fromPage(LATE_FONT, "font"));
    // Served first, stored copy second, for the reason the navigation block
    // gives: a put of the response itself rather than a clone is invisible in
    // the other order and fatal in a browser.
    await expect(response!.text()).resolves.toBe("woff2");
    expect(worker.keys()).toContain(LATE_FONT);
    await expect(
      worker.store.get(worker.cache)!.get(`${ORIGIN}${LATE_FONT}`)!.clone().text(),
    ).resolves.toBe("woff2");
  });

  it("stores it under the pathname alone, which is the key it is read by", async () => {
    // Written and read with one spelling, or the entry is never served again:
    // stored under the request's own URL, a plain request for the same file
    // misses, refetches, and the next activate prunes both. The second request
    // has no route, so only the cache can answer it.
    const worker = shell();
    await worker.install();
    worker.respond(`${LATE_FONT}?v=2`, font());
    await worker.request(fromPage(`${LATE_FONT}?v=2`, "font"));
    const fetches = worker.fetched.length;
    const response = await worker.request(fromPage(LATE_FONT, "font"));
    await expect(response!.text()).resolves.toBe("woff2");
    expect(worker.fetched.length).toBe(fetches);
    expect(worker.keys()).toContain(LATE_FONT);
  });

  it("refuses to store the SPA fallback under an asset URL", async () => {
    // If the /assets mount ever goes, mount_spa answers a removed hashed file
    // with index.html and a 200. Caching that as JavaScript is how a PWA bricks
    // itself: every launch afterwards parses a page of HTML as a module and
    // dies before the first paint, and the way back is the kill switch.
    const worker = shell();
    await worker.install();
    worker.respond("/assets/index-GONE.js", html("<!doctype html>"));
    const response = await worker.request(fromPage("/assets/index-GONE.js"));
    expect(response!.status).toBe(200);
    expect(worker.keys()).not.toContain("/assets/index-GONE.js");
  });

  it("does not store the 404 the /assets mount really answers with", async () => {
    // What the live app does today, measured: /assets/index-GONE.js is 404 with
    // `{"detail":"Not Found"}` from the StaticFiles mount — JSON, not HTML, so
    // it is the status half of the guard that has to refuse it. Stored, it would
    // pin a 404 under a hashed URL until the next activate.
    const worker = shell();
    await worker.install();
    worker.respond("/assets/index-GONE.js", notFound());
    const response = await worker.request(fromPage("/assets/index-GONE.js"));
    expect(response!.status).toBe(404);
    expect(worker.keys()).not.toContain("/assets/index-GONE.js");
  });

  it("serves a 206 without trying to store it", async () => {
    // `response.ok` is 200–299, so a guard written on it reaches `cache.put`,
    // which rejects a partial response outright — a TypeError out of a line that
    // reads as though it cannot fail, on the one path where the page is waiting
    // for the bytes. Nothing in this client sends a Range and `isRefused` turns
    // away a request that carries one, so a 206 here is a server volunteering
    // one: a disagreement to decline, not to store.
    const worker = shell();
    await worker.install();
    worker.respond(
      LATE_FONT,
      new Response("part", { status: 206, headers: { "content-type": "font/woff2" } }),
    );
    const response = await worker.request(fromPage(LATE_FONT, "font"));
    expect(response!.status).toBe(206);
    expect(worker.keys()).not.toContain(LATE_FONT);
    // Not stored is the weaker half: a handler that attempted the put and
    // swallowed the TypeError would satisfy it too. The put is never ATTEMPTED,
    // which is the difference between declining a write that cannot succeed and
    // surviving one.
    expect(worker.puts).not.toContain(LATE_FONT);
  });

  it.each([
    [201, "created: a statement about the request, not the file"],
    [202, "accepted: an answer that says the answer is not ready"],
    [203, "transformed by a proxy — the one thing a content hash promises has not happened"],
    [205, "reset, and null-bodied like a 204"],
  ])("does not store a %i (%s)", async (status) => {
    // `response.ok` is 200–299 and only one of those nine is a representation
    // of the file that was asked for. Stored under a hashed URL, any of these
    // is a module with nothing in it — or, for a 203, someone else's idea of
    // what the bytes should have been — pinned until the next activate.
    const worker = shell();
    await worker.install();
    worker.respond(
      LATE_FONT,
      // A 205 cannot carry a body at all: the Response constructor rejects one,
      // as it does for a 204.
      new Response(status === 205 ? null : "part", {
        status,
        headers: { "content-type": "font/woff2" },
      }),
    );
    const response = await worker.request(fromPage(LATE_FONT, "font"));
    expect(response!.status).toBe(status);
    expect(worker.keys()).not.toContain(LATE_FONT);
    expect(worker.puts).not.toContain(LATE_FONT);
  });

  it("serves the bytes the network returned even when the cache write is refused", async () => {
    // A phone that is full. The bytes are in hand and the network said 200, so a
    // worker that turned a refused write into a failed request would be strictly
    // worse than no worker: with none installed that chunk loads, with this one
    // it would not.
    const worker = shell();
    await worker.install();
    worker.refusePut(LATE_FONT);
    worker.respond(LATE_FONT, font());
    const response = await worker.request(fromPage(LATE_FONT, "font"));
    await expect(response!.text()).resolves.toBe("woff2");
    expect(worker.keys()).not.toContain(LATE_FONT);
  });

  it("serves an asset whose body stalls without holding the page for the write", async () => {
    // The put reads the body to the end, and this body never ends. Awaited
    // before the page is answered, that put held the page's own response —
    // headers and all — for as long as the stall lasted: strictly worse than no
    // worker, where the page at least gets the headers and whatever bytes came.
    // The page gets exactly what the network gave it, and the write, bounded,
    // runs beside it.
    const worker = shell();
    await worker.install();
    worker.respond(LATE_FONT, stalled("font/woff2"));
    const response = await worker.request(fromPage(LATE_FONT, "font"));
    expect(response!.status).toBe(200);
    expect(worker.keys()).not.toContain(LATE_FONT);
    expect(worker.puts).not.toContain(LATE_FONT);
  });

  it("answers the page before the write it started has landed", async () => {
    // The write is the worker's business and the page is not made to wait for
    // it: a put awaited before the answer passes every assertion `request()`
    // can make, because `request()` drains waitUntil first. Deterministic for
    // the reason the navigation's twin is — the fake's put lands on a
    // macrotask and this reads on a microtask.
    const worker = shell();
    await worker.install();
    worker.respond(LATE_FONT, font());
    const { responded, settled } = worker.dispatch(fromPage(LATE_FONT, "font"));
    await expect((await responded)!.text()).resolves.toBe("woff2");
    expect(worker.keys()).not.toContain(LATE_FONT);
    // And it does land, once the event's background half is let run.
    await settled;
    expect(worker.keys()).toContain(LATE_FONT);
  });

  it("serves an asset from the network when the cache cannot be opened", async () => {
    // The same standard as the navigation: a worker that cannot use its cache
    // behaves like no worker at all. Here the failure mode it replaces is a hard
    // module-load error on a phone whose network is fine.
    const worker = shell();
    await worker.install();
    worker.refuseOpen();
    const response = await worker.request(fromPage("/assets/index-CY0Sh5Q2.js"));
    await expect(response!.text()).resolves.toBe("console.log(1)");
  });

  it("does not store a 204, which has no body to store", async () => {
    // `response.ok` is 200–299 here too: a 204 stored under a hashed URL is an
    // empty chunk, and every launch afterwards loads a module with nothing in
    // it. The Cache API is perfectly happy to store one, so only this guard
    // stops it.
    const worker = shell();
    await worker.install();
    worker.respond(LATE_FONT, new Response(null, { status: 204 }));
    const response = await worker.request(fromPage(LATE_FONT, "font"));
    expect(response!.status).toBe(204);
    expect(worker.keys()).not.toContain(LATE_FONT);
  });

  it("goes to the network when the cached copy cannot be read", async () => {
    // A read that fails is not a hit and not a reason to fail the request: the
    // network still has the bytes.
    const worker = shell();
    await worker.install();
    worker.refuseMatch("/assets/index-CY0Sh5Q2.js");
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    const response = await worker.request(fromPage("/assets/index-CY0Sh5Q2.js"));
    await expect(response!.text()).resolves.toBe("console.log(1)");
  });

  it("fails an uncached asset with no network, rather than inventing one", async () => {
    // There is nothing honest to serve, and the obvious something-else — the
    // shell, under a .js URL — is the brick above by another door.
    const worker = shell();
    await worker.install();
    await expect(worker.request(fromPage("/assets/index-NEW.js"))).rejects.toThrow();
  });

  it("fails an asset the network never answers, rather than hanging on it", async () => {
    // A hung asset fetch with a perfectly good cached shell is the worst shape
    // this takes on an installed PWA: the shell paints, the standalone window
    // sits blank, and there is no browser chrome to escape with.
    const worker = shell();
    await worker.install();
    worker.hang("/assets/index-NEW.js");
    const before = worker.deadlines.length;
    await expect(worker.request(fromPage("/assets/index-NEW.js"))).rejects.toThrow();
    expect(worker.deadlines.slice(before)).toContain(10000);
  });

  it("does not ask past the HTTP cache for a runtime asset", async () => {
    // The bytes behind a hashed URL cannot have changed, so `cache: "reload"`
    // here would spend a phone's bytes re-fetching what the HTTP cache already
    // holds. install is the opposite case and asks for exactly that.
    const worker = shell();
    await worker.install();
    worker.respond(LATE_FONT, font());
    await worker.request(fromPage(LATE_FONT, "font"));
    expect(worker.fetched).toContain(LATE_FONT);
    expect(worker.reloaded).not.toContain(LATE_FONT);
  });

  it("hands the page's own request to the network, not a URL it rebuilt", async () => {
    const worker = shell();
    await worker.install();
    worker.respond(LATE_FONT, font());
    const before = worker.handed.length;
    await worker.request(fromPage(LATE_FONT, "font"));
    expect(typeof worker.handed[before]).not.toBe("string");
    expect(worker.handed[before]).toMatchObject({ mode: "cors", destination: "font" });
  });

  it("leaves a path that merely contains /assets/ alone", async () => {
    // Anchored, exactly as isAssetUrl's own prefix test is. /img/assets/logo.png
    // is 200 text/html on the live app — the SPA fallback, not the mount — and
    // it carries no hash, so nothing about it may be cached cache-first.
    const worker = shell();
    await worker.install();
    await expect(worker.request(fromPage("/img/assets/logo.png"))).resolves.toBeUndefined();
  });
});

describe("the update handshake", () => {
  it("does not take over on install", async () => {
    const worker = shell();
    await worker.install();
    // A build that swaps itself under a running conversation loses whatever was
    // on screen. The new worker waits until the user asks for it.
    expect(worker.skipWaitingCalls).toBe(0);
  });

  it("takes over when the page asks", async () => {
    const worker = shell();
    await worker.install();
    await worker.message({ type: "SKIP_WAITING" });
    expect(worker.skipWaitingCalls).toBe(1);
  });

  it("ignores every other message", async () => {
    // Three ways to get the guard wrong, one message each: another envelope (a
    // guard that never reads the type), the type as a bare string (one that
    // reads `event.data` rather than the envelope), and null, which is what
    // `postMessage(null)` delivers. Reading `.type` off null is a TypeError
    // inside the listener; the harness runs listeners synchronously, so that
    // arrives here as a rejected `message()` rather than as a browser's log line.
    const worker = shell();
    await worker.install();
    await worker.message({ type: "PING" });
    await worker.message("SKIP_WAITING");
    await worker.message(null);
    expect(worker.skipWaitingCalls).toBe(0);
  });

  it("ignores a message with no data at all", async () => {
    // `postMessage(undefined)` delivers exactly that, and it is the value null
    // cannot stand in for: a guard tightened to `event.data !== null` passes
    // every message above and reads `.type` off this one.
    const worker = shell();
    await worker.install();
    await worker.message(undefined);
    expect(worker.skipWaitingCalls).toBe(0);
  });
});
