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
    // font that was declined is a name activate keeps a slot open for and task 7
    // would look up and miss — the record has to be fact, not intent.
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
    // One per fetch, the shell and the chunks included. A `.catch()` handles a
    // fetch that rejects and does nothing at all for one that never settles, so
    // this is the only thing standing between a captive portal and an install
    // that hangs until the browser kills it.
    expect(worker.deadlines).toEqual([10000, 10000, 10000]);
    expect(worker.deadlines).toHaveLength(worker.fetched.length);
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
    expect(worker.deadlines).toEqual([10000, 10000, 10000, 10000, 10000]);
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
 * it. The navigate rows are that; the `cors` rows fall out of task 7's two
 * branches unhandled whatever the guard says, and the direct half is what pins
 * them. No row is given `navigate` it would not really arrive with.
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
    // pathname of row 2 still begins "/assets/", the prefix task 7's asset
    // branch reads, so a server that ever did normalise would have it cache a
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
    // The same request without the header is the one task 7 serves from cache.
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
    // either — task 7's asset branch keys the cache on the pathname, not the href.
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
  // are claimed by task 7's navigation branch, so those are live from the moment
  // it lands, and the `cors` rows fall out of both its branches unhandled
  // whatever the guard says. The direct block above is what pins those, and is
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
