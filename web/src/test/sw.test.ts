import { describe, expect, it } from "vitest";
// The shipped bytes, not a TypeScript copy of them — same reason
// index-html.test.ts reads the shipped index.html.
import source from "../../public/sw.js?raw";
import { css, font, html, js, loadWorker, ORIGIN, type Harness } from "./sw-harness";

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
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.json();
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
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.json();
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
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.json();
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
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.json();
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
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.json();
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
    const listed = await worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.json();
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
