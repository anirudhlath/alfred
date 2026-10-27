import { describe, expect, it } from "vitest";
// The shipped bytes, not a TypeScript copy of them — same reason
// index-html.test.ts reads the shipped index.html.
import source from "../../public/sw.js?raw";
// The same file and the kill switch, each as its exact bytes: see FIRED.
import shippedBytes from "../../public/sw.js?url&inline";
import killSwitchBytes from "../../public/sw.kill.js?url&inline";
import killSwitchSource from "../../public/sw.kill.js?raw";
// This file's own text, for the guard on every file that reads the worker.
import ownSource from "./sw.test.ts?raw";
import { STATUS_DEADLINE_MS } from "../lib/auth";
import {
  broken,
  css,
  fetchRequest,
  font,
  html,
  js,
  loadWorker,
  macrotasks,
  ORIGIN,
  stalled,
  type Harness,
  type LoadOptions,
  type RequestSpec,
} from "./sw-harness";

/**
 * Whether the kill switch is fired: sw.js a byte-for-byte copy of sw.kill.js,
 * which is what that file's HOW TO FIRE IT leaves. Nothing here is then about a
 * worker that ships — this file tests the one the copy took out — so every
 * suite below stands down, the test under `if (FIRED)` says why, and the firing
 * commit gets through CI to the deploy it exists for. Exact bytes and nothing
 * looser, so that no other edit to sw.js, however close to the switch, stands
 * these tests down: `?url&inline` hands each file over as its bytes, base64'd,
 * where `?raw` would hand over decoded text.
 */
const FIRED = shippedBytes === killSwitchBytes;

// That each import is its file's bytes is the toolchain's doing, not this
// file's, and Vite and Vitest are devDependencies a minor bump can change. Two
// equal values would stand every suite below down in a tree that is not fired;
// two URL paths, which is what `?url` alone gives, would never stand them down,
// and a fired tree would fail them. So this runs in either tree.
it("hands FIRED each file's own bytes: a base64 data: URL that decodes to that file", () => {
  for (const [file, url, text] of [
    ["sw.js", shippedBytes, source],
    ["sw.kill.js", killSwitchBytes, killSwitchSource],
  ]) {
    expect(url, file).toMatch(/^data:[^,]*;base64,/);
    const bytes = Uint8Array.from(atob(url.slice(url.indexOf(",") + 1)), (c) => c.charCodeAt(0));
    // `ignoreBOM` keeps a byte-order mark, as `?raw` does.
    expect(new TextDecoder("utf-8", { ignoreBOM: true }).decode(bytes), file).toBe(text);
  }
});

/**
 * Every source file under src/, as text: read, and none of it run. This one
 * too, which the glob alone leaves out — Vite never globs a file into itself.
 */
const SOURCES: Record<string, string> = {
  ...import.meta.glob<string>("/src/**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }),
  "/src/test/sw.test.ts": ownSource,
};

/**
 * A string literal — in any of the three quotes — that ends in `public/sw.js`,
 * with or without a query: the shape of every way a file here takes the
 * worker's bytes — an import of it `?raw` or `?url&inline`, or its path handed
 * to `readFileSync`. A comment that only names the file is not one.
 */
const LOADS_THE_WORKER = /["'`][^"'`\n]*public\/sw\.js(?:\?[^"'`\n]*)?["'`]/;

// The kill switch has to get through CI to its deploy, so every test that reads
// the worker's bytes must stand down on a fired tree. One that did not —
// auth.test.ts reading TIMEOUT_MS out of the worker — turned `ci-ok` red on the
// one commit that exists to unbrick phones. Keyed on what loads the bytes, not
// on what mentions the file: lib/auth.ts names it in a comment and reads
// nothing. Runs in either tree, because it is about the files that test the
// worker and not about the worker.
it("finds every file that reads the worker's bytes standing down when the switch is fired", () => {
  const loaders = Object.keys(SOURCES).filter((path) => LOADS_THE_WORKER.test(SOURCES[path]));
  // Not vacuous: the two files that test the worker are found by the same rule.
  expect(loaders).toEqual(
    expect.arrayContaining(["/src/test/sw.test.ts", "/src/test/sw-server-routes.test.ts"]),
  );
  for (const path of loaders) {
    expect(SOURCES[path], path).toMatch(/^const FIRED = /m);
    expect(SOURCES[path], path).toMatch(/\.skipIf\(FIRED\)/);
  }
});

// What that guard counts as a load: the worker's path in any of the three
// quotes — a template literal is how a path joined onto a root is usually
// written — and never a comment that only names the file. It checks per file,
// and a path built from pieces gets past it; what tests the property itself is
// the web job's run of this suite on a fired copy (.github/workflows/ci.yml).
it("counts the worker's path in any quote as a load, and a mention as none", () => {
  for (const load of [
    'import source from "../../public/sw.js?raw";',
    "readFileSync('public/sw.js', 'utf8');",
    "readFileSync(`${root}/public/sw.js`, 'utf8');",
  ]) {
    expect(LOADS_THE_WORKER.test(load), load).toBe(true);
  }
  for (const mention of ["// web/public/sw.js is the worker", " * see public/sw.js, rule 3"]) {
    expect(LOADS_THE_WORKER.test(mention), mention).toBe(false);
  }
});

if (FIRED) {
  it("stands down: web/public/sw.js is the kill switch, fired — sw.kill.js byte for byte", async ({
    annotate,
  }) => {
    expect(shippedBytes).toBe(killSwitchBytes);
    // A passing test's name is not in CI's log, and a skipped count says
    // nothing of why. In GitHub Actions this is a warning on the run itself.
    await annotate(
      "The kill switch is fired: web/public/sw.js is sw.kill.js byte for byte, so sw.test.ts's tests of the real worker stand down until the firing commit is reverted.",
      "warning",
    );
  });
}

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

function shell(options: LoadOptions = {}): Harness {
  const worker = loadWorker(source, options);
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

/** `text` in `n` pieces, in order and none of them empty: a body as a slow link sends it. */
const split = (text: string, n: number): string[] =>
  Array.from({ length: n }, (_, i) =>
    text.slice(Math.floor((i * text.length) / n), Math.floor(((i + 1) * text.length) / n)),
  );

/**
 * The two ways a body stalls, and a worker has to survive both. After some of
 * it — the one a worker that reads a single piece and calls the body done
 * cannot see, which is how that worker passed every test here once. And before
 * any of it, which is the one a deadline armed only when the first piece lands
 * never ends. `undefined` is `stalled()`'s default: some bytes, then nothing.
 */
const STALLS = [
  ["after some of it", undefined],
  ["before any of it", ""],
] as const;

/**
 * The ceiling `whole` puts on a body, from its headers to its end: sw.js's
 * CEILING_MS, restated rather than read, as the ten-second deadline is.
 */
const CEILING = 120000;

/**
 * A body that never goes quiet and never ends: a byte every seven seconds, for
 * nine minutes. Never silent for ten seconds, so the deadline on silence never
 * fires on it, and only the ceiling ends it — at a time no piece falls due on,
 * so which of the two fires first is never a tie.
 */
const unending = (worker: Harness, type: string): Response =>
  worker.trickle(type, Array<string>(80).fill("x"), { every: 7000 });

/** A list of deadlines in ascending order: how many of each, not when each was armed. */
const ascending = (deadlines: readonly number[]): number[] => [...deadlines].sort((a, b) => a - b);

/**
 * `"pending"` if `read` has neither resolved nor rejected once a few real
 * macrotasks have run — which, after a request has settled, is a read nothing
 * is still feeding and nothing has ended. Not a statement about how long
 * anything takes: the harness's clock is not the one this waits on, and neither
 * is the wall's. Macrotasks are counted, not timed (see `macrotasks`), so a
 * machine that is slow, or stalls part way, gets the same answer as one that
 * is not — a `"settled"` that takes a few steps is not lost to a 20 ms sleep.
 */
const pending = (read: Promise<unknown>): Promise<string> =>
  Promise.race([
    read.then(
      () => "settled",
      () => "rejected",
    ),
    macrotasks().then(() => "pending"),
  ]);

/**
 * Real macrotasks until `done` says so, and one more after it, so that whatever
 * got there has finished what it set off. For work nothing awaits — a timer
 * due on the harness's clock, an install part way through its writes — that a
 * test can see only the consequences of. Gives up after a hundred, and the
 * assertion after it is what fails. The hundred are macrotasks, not time, so
 * load cannot make this give up earlier: every one lets each step waiting take
 * one, however slowly they run (`macrotasks` says why). The waits below need
 * one to three; the last, which waits for a deletion that must NOT come, is
 * meant to use all hundred.
 */
const until = async (done: () => boolean): Promise<void> => {
  for (let i = 0; i < 100 && !done(); i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
};

/** A 404 from the /assets StaticFiles mount: JSON, not the SPA's HTML fallback. */
const notFound = () =>
  new Response('{"detail":"Not Found"}', {
    status: 404,
    headers: { "content-type": "application/json" },
  });

describe.skipIf(FIRED)("install", () => {
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

  it("asks past the HTTP cache for the HTML, and for nothing it names", async () => {
    const worker = shell();
    await worker.install();
    // /index.html is the one URL a deploy rewrites under the same name, so it
    // is the one an HTTP cache could answer with the deploy before; without
    // `cache: "reload"`, a worker installing seconds after a deploy could
    // precache that and serve it until the next one. A hashed file's bytes
    // cannot differ from what the HTTP cache holds under its name, and asking
    // past it for one re-downloaded, on every first install, the chunk and
    // stylesheet the page had fetched a moment before. Spelled out rather than
    // compared against `fetched`, which two empty lists would also satisfy.
    expect(worker.fetched).toEqual(["/index.html", ...SHELL_ASSETS]);
    expect(worker.reloaded).toEqual(["/index.html"]);
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

  it("keeps the shell it had when it is a font's write that fails", async () => {
    // "After everything it names" includes what it names through its
    // stylesheet. A shell written before the fonts, with a font's write refused
    // after it, leaves a new shell whose typefaces are not in the cache — not a
    // white screen, but a new offline copy the install that wrote it did not
    // finish.
    const worker = withFonts();
    await worker.install();
    worker.respond("/index.html", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css(SHELL_CSS));
    worker.refusePut(SHELL_FONTS[0]);
    await expect(worker.install()).rejects.toThrow(/storage error/);
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("two");
  });

  it("fails, and leaves the record it found, when it is the shell's own write that is refused", async () => {
    // The record goes in AFTER the shell, and install waits on both. A record is
    // an install's claim to have finished: activate prunes by it, and the
    // activate that reads it need not be this worker's — a worker already
    // waiting when this install fails stays waiting, and when the page posts
    // SKIP_WAITING it runs the same purge over this same cache. Written before
    // a shell that is then refused, the new record outlives the install that
    // wrote it, and that purge keeps by a deploy the cache never finished
    // taking: everything the failed install wrote stays, named by a record for
    // a shell that is not there. And a shell write that is not awaited lets the
    // install succeed with its shell refused.
    const worker = withFonts();
    await worker.install();
    const before = worker.keys();
    const record = await recordIn(worker);
    worker.respond("/index.html", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css(NEXT_CSS));
    worker.respond(NEXT_FONT, font());
    worker.refusePut("/index.html");
    await expect(worker.install()).rejects.toThrow(/storage error/);
    await expect(recordIn(worker)).resolves.toEqual(record);
    // What the purge then does with it: everything the failed install wrote is
    // named by nothing, and everything the first one wrote is still named.
    await worker.activate();
    expect(worker.keys()).toEqual(before);
  });
});

describe.skipIf(FIRED)("install, and the fonts", () => {
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

  it("leaves the fonts to the HTTP cache, as it leaves the chunks", async () => {
    const worker = withFonts();
    await worker.install();
    // Hashed like the chunks, and for the same reason not asked for past the
    // HTTP cache: their names change when their bytes do. Only the HTML is.
    expect(worker.fetched).toEqual(expect.arrayContaining(SHELL_FONTS));
    expect(worker.reloaded).toEqual(["/index.html"]);
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
    // carries @font-face rules inside it, and reading the entry chunk into a
    // string to find out costs the install and returns nothing. The planted
    // rule below is the shape that would be taken.
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

describe.skipIf(FIRED)("install, and the network that never answers", () => {
  it("gives every request a deadline, and every wait on a body another", async () => {
    const worker = shell();
    await worker.install();
    // One per fetch — the shell and both chunks — and one per READ of each
    // body, which is the half that is easy to miss: fetch() resolves on the
    // HEADERS, so a route that answers 200 and then goes quiet leaves
    // `await text()` pending for ever on a request that looks finished — and
    // `Cache.put` is a read of the body too, which is why the chunk the worker
    // never reads itself gets them. A `.catch()` handles a fetch that rejects
    // and does nothing at all for any of these hangs, so they are the only
    // thing standing between a captive portal and an install that waits until
    // the browser kills it.
    expect(worker.fetched).toHaveLength(3);
    // Per read and not per body, because a deadline on the whole body cannot
    // tell a stall from a slow link. This harness sends each of these bodies in
    // one piece, so that is two reads apiece — the piece, and the end — and
    // three fetches and three bodies make nine. Each body has a ceiling as
    // well, two minutes from its headers to its end, which no working link
    // reaches and a body still trickling at the end of it does. The
    // stylesheet's font scrape then reads from memory, with nothing left to
    // wait on.
    expect(ascending(worker.deadlines)).toEqual([
      ...Array<number>(9).fill(10000),
      ...Array<number>(3).fill(CEILING),
    ]);
    // And takes each one back down once what it was guarding is done. A
    // deadline left armed fires ten seconds later against nothing, holding the
    // worker awake to do it.
    expect(worker.deadlinesCleared).toBe(worker.deadlines.length);
  });

  it.each([
    ["the shell", "/index.html", "text/html; charset=utf-8", SHELL_HTML],
    ["the entry chunk", SHELL_ASSETS[0], "text/javascript", "console.log(1)"],
    ["the stylesheet", SHELL_ASSETS[1], "text/css", SHELL_CSS],
    ["a font", SHELL_FONTS[0], "font/woff2", "woff2".repeat(4)],
  ])("installs %s arriving slowly, for as long as it keeps arriving", async (_name, path, type, body) => {
    // Measured in Chromium: the 414 kB entry chunk of `6dabc43`'s build at
    // 32 KiB/s is 12.6 s of steady progress, and a deadline on the whole body
    // failed that install at 10.0 s — on a first visit, and on every visit
    // after it, since a failed install leaves no worker and the next load
    // starts the precache again. A deadline is for a stall, and this is not one: ten pieces 1.26 s
    // apart, every one of them inside the deadline and the body as a whole
    // well past it.
    const worker = withFonts();
    worker.respond(path, worker.trickle(type, split(body, 10), { every: 1260 }));
    await worker.install();
    expect(worker.now).toBeGreaterThanOrEqual(12600);
    // Stored whole, and not merely stored: the put reads the original after
    // the deadlined read of its twin, so what lands is every piece, in order.
    const stored = worker.store.get(worker.cache)!.get(`${ORIGIN}${path}`);
    await expect(stored!.clone().text()).resolves.toBe(body);
  });

  it("declines a body ten seconds after its last piece, not ten seconds after its first", async () => {
    // The deadline is a limit on SILENCE. Three pieces four seconds apart and
    // then nothing: a worker waiting on the next piece notices the stall ten
    // seconds after the last one, 22 s in; one with a deadline on the whole
    // body gives up at 10 s, in the middle of a download that was still going.
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond(
      SHELL_ASSETS[0],
      worker.trickle("text/javascript", ["console", ".log", "(1)"], { every: 4000, stall: true }),
    );
    worker.respond(SHELL_ASSETS[1], css("body{color:red}"));
    await expect(worker.install()).rejects.toThrow("deadline exceeded");
    expect(worker.now).toBe(3 * 4000 + 10000);
  });

  it.each([
    ["the shell", "/index.html", "text/html; charset=utf-8"],
    ["the entry chunk", SHELL_ASSETS[0], "text/javascript"],
  ])("fails the install two minutes after %s's headers, however steadily it is still arriving", async (_name, path, type) => {
    // A limit on silence never ends a body that is never silent. Measured in
    // Chromium with no DevTools attached, a chunk sending a byte every five
    // seconds held the install "installing" until the browser's own limit on
    // the event failed it, at 300 s. The ceiling ends it at two minutes, the
    // way a stall is ended, and lets the connection go.
    const worker = withFonts();
    worker.respond(path, unending(worker, type));
    await expect(worker.install()).rejects.toThrow("deadline exceeded");
    expect(worker.now).toBe(CEILING);
    expect(worker.released).toEqual([path]);
    expect(worker.puts).toEqual([]);
  });

  it("installs without a font still arriving two minutes after its headers", async () => {
    // The same ceiling on the loop that is allowed to lose things: the font is
    // let go and the install carries on. Not asserted: the clock once the
    // install is done, which runs on past the ceiling while the rest is
    // written. A worker with no ceiling fails this on what it stores — the
    // font, whole, nine minutes in.
    const worker = withFonts();
    worker.respond(SHELL_FONTS[0], unending(worker, "font/woff2"));
    await worker.install();
    expect(worker.released).toEqual([SHELL_FONTS[0]]);
    expect(worker.puts).not.toContain(SHELL_FONTS[0]);
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", ...SHELL_ASSETS, SHELL_FONTS[1], "/index.html"].sort(),
    );
  });

  it.each([
    ["the shell", "/index.html", "text/html; charset=utf-8"],
    ["the entry chunk", SHELL_ASSETS[0], "text/javascript"],
    ["a font", SHELL_FONTS[0], "font/woff2"],
  ])("lets go of the connection when it gives up on %s's body", async (_name, path, type) => {
    // A stall this worker has declined should END, not merely be ignored: left
    // running, the read keeps the request open, and the connection with it,
    // behind a decision already made. Every one of these is install's own
    // fetch — nobody else holds a branch of the body — so cancelling the
    // branches the worker holds is what releases it. (A body the PAGE is also
    // reading is the opposite case, and is under the navigation and the asset
    // branch.) Two outcomes, one release: the shell and the chunk fail the
    // install, and the font is declined and the install carries on.
    const worker = withFonts();
    worker.respond(path, stalled(type));
    await worker.install().catch(() => {});
    expect(worker.released).toEqual([path]);
  });

  it.each([
    ["the shell", "/index.html", "application/json"],
    ["the entry chunk", SHELL_ASSETS[0], undefined],
    ["a font", SHELL_FONTS[0], undefined],
  ])("lets go of the connection when it turns %s down unread", async (_name, path, type) => {
    // The other way a worker is finished with a body: it has refused the
    // response on its headers — a page that is not HTML where the shell should
    // be, a page of HTML under a hashed URL — and wants none of it. Read by
    // nothing and cancelled by nothing, that body holds its connection open
    // for as long as the server keeps it coming, and for ever on one that
    // stalls: the decision is made and the request outlives it. Measured in
    // Chromium with nothing inspecting the worker, a font turned down this way
    // held its connection until the worker was stopped for being idle, 30 s on.
    // The shell and the chunk fail the install and the font is skipped; all
    // three are released before anything else happens.
    const worker = withFonts();
    worker.respond(path, stalled(type));
    await worker.install().catch(() => {});
    expect(worker.released).toEqual([path]);
  });

  it("turns down a shell with no body at all for what it is, its status", async () => {
    // A 204 has nothing to let go of, and the letting go must not trip over
    // that on its way to the install's real reason for failing.
    const worker = withFonts();
    worker.respond("/index.html", new Response(null, { status: 204 }));
    await expect(worker.install()).rejects.toThrow("shell 204");
    expect(worker.released).toEqual([]);
  });

  it("lets go of nothing on an install that went well", async () => {
    const worker = withFonts();
    await worker.install();
    expect(worker.released).toEqual([]);
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
    // Five fetches — shell, two chunks, two fonts — and four bodies at two
    // reads and one ceiling apiece: the shell's, both chunks', and the font
    // that answered. The one that hung never had a body to read.
    expect(worker.fetched).toHaveLength(5);
    expect(ascending(worker.deadlines)).toEqual([
      ...Array<number>(5 + 4 * 2).fill(10000),
      ...Array<number>(4).fill(CEILING),
    ]);
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

  it.each(STALLS)("fails the install when the shell answers and its body stalls %s", async (_when, sent) => {
    // The hang one layer in, and the one a deadline on the REQUEST does not
    // reach: fetch() resolves on the headers, so this install holds a 200
    // text/html whose `text()` never settles. Undeadlined it does not fail —
    // it waits, inside waitUntil, until the browser gives up on the install and
    // leaves the phone on whatever worker it had, which is the same outcome as
    // the black hole above through a door the request's own deadline cannot
    // see. A test for it hangs rather than failing, which is why the assertion
    // is on the rejection.
    const worker = loadWorker(source);
    worker.respond("/index.html", stalled(undefined, sent));
    await expect(worker.install()).rejects.toThrow("deadline exceeded");
    expect(worker.keys()).toEqual([]);
  });

  it.each(STALLS)("fails the install when it is the stylesheet's body that stalls %s", async (_when, sent) => {
    // The second read, and the one nothing else in this file would notice: the
    // stylesheet is fetched, validated and then read again to scrape @font-face
    // out of it. A shell whose CSS body never finishes is not installable — the
    // bytes to cache are not all there — so this fails rather than installing a
    // stylesheet that is half a file.
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    worker.respond("/assets/index-nhBuGG1b.css", stalled("text/css", sent));
    await expect(worker.install()).rejects.toThrow("deadline exceeded");
    expect(worker.keys()).toEqual([]);
  });

  it.each(STALLS)("fails the install when it is the entry chunk's body that stalls %s", async (_when, sent) => {
    // The body nothing in this file reads — except `Cache.put`, which reads
    // every body it is handed to the end before it writes. So a put of a
    // network response is a body read, and without a deadline of its own this
    // one waits for ever: measured in Chromium with no DevTools attached to the
    // worker, a shell naming a chunk whose headers arrive and whose bytes stall
    // left the install "installing" until the browser's own limit on the event
    // failed it, at 300 s. It fails here, before a single write — the shell
    // included, since a new shell naming a chunk the cache does not hold is a
    // white screen offline, in the cache the previous worker is still serving
    // from.
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", stalled("text/javascript", sent));
    worker.respond("/assets/index-nhBuGG1b.css", css(SHELL_CSS));
    await expect(worker.install()).rejects.toThrow("deadline exceeded");
    expect(worker.keys()).toEqual([]);
    expect(worker.puts).toEqual([]);
  });

  it.each(STALLS)("installs without a font whose body stalls %s", async (_when, sent) => {
    // The same hang on the loop that is allowed to lose things: a typeface
    // whose bytes stall is declined like one that never answers, and the
    // install carries on without it rather than waiting on its put for ever.
    const worker = withFonts();
    worker.respond(SHELL_FONTS[0], stalled("font/woff2", sent));
    await worker.install();
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", ...SHELL_ASSETS, SHELL_FONTS[1], "/index.html"].sort(),
    );
    expect(worker.puts).not.toContain(SHELL_FONTS[0]);
  });

  it("fails the install when the entry chunk's body breaks off, leaving no rejection unhandled", async () => {
    // A connection reset half way through a body: the read rejects rather than
    // hangs, and a body that has errored rejects the cancel that gives it up,
    // too. Left unhandled, that second rejection is a line in a browser's
    // console and a failed run here.
    const worker = withFonts();
    worker.respond(SHELL_ASSETS[0], broken("text/javascript"));
    await expect(worker.install()).rejects.toThrow("network error");
    expect(worker.keys()).toEqual([]);
  });

  it("installs without a font whose body breaks off", async () => {
    const worker = withFonts();
    worker.respond(SHELL_FONTS[0], broken("font/woff2"));
    await worker.install();
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", ...SHELL_ASSETS, SHELL_FONTS[1], "/index.html"].sort(),
    );
  });
});

describe.skipIf(FIRED)("activate", () => {
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

  it("prunes on the record alone when there is no cached shell beside it", async () => {
    // The other half of the union, and the one that is not the same reading as
    // the test above. A shell that will not READ may name anything, so nothing
    // can be pruned on its account; a shell that is simply ABSENT names
    // nothing, the record is intact, and the union is the record. Keeping
    // everything here instead keeps a dead build's chunks for no page at all.
    // No path in this worker leaves a record with no shell — install writes the
    // shell before the record, and nothing here deletes it — so, like the
    // missing record above, it is the reading that is pinned.
    const worker = shell();
    await worker.install();
    const entries = worker.store.get(worker.cache)!;
    entries.delete(at("/index.html"));
    entries.set(at("/assets/index-OLD.js"), js("old"));
    await worker.activate();
    expect(worker.keys()).toEqual(["/__shell-manifest", ...SHELL_ASSETS].sort());
  });

  it("keeps everything when a stylesheet the cached shell names cannot be read", async () => {
    // The keep-set takes in the typefaces the cached shell's stylesheets name,
    // which is a third cache read. A stylesheet that will not read may name any
    // typeface, so it gets the same reading as a shell that will not: keep
    // everything.
    const worker = withFonts();
    await worker.install();
    const orphan = "/assets/geist-mono-latin-500-normal-YINYabwD.woff2";
    worker.store.get(worker.cache)!.set(at(orphan), font());
    worker.refuseMatch(SHELL_ASSETS[1]);
    await worker.activate();
    expect(worker.keys()).toContain(orphan);
  });

  it("prunes as usual when a stylesheet the cached shell names is not in the cache", async () => {
    // And the other half, as for the shell: a stylesheet that is simply absent
    // names nothing. No path in this worker leaves a cached shell without its
    // stylesheet, so it is the reading that is pinned.
    const worker = withFonts();
    await worker.install();
    const entries = worker.store.get(worker.cache)!;
    entries.delete(at(SHELL_ASSETS[1]));
    entries.set(at("/assets/index-OLD.js"), js("old"));
    await worker.activate();
    expect(worker.keys()).not.toContain("/assets/index-OLD.js");
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
 * `worker.cache` makes into VERSION. Reached on each call rather than once as
 * this file loads: in the fired tree sw.js is the kill switch, which has no
 * `isRefused`, and a throw at load would fail the file before its suites could
 * stand down (see FIRED).
 */
const refuses = (spec: RequestSpec): boolean =>
  loadWorker(source, { expose: ["isRefused"] }).inner.isRefused!(fetchRequest(spec));

describe.skipIf(FIRED)("what the worker refuses to touch", () => {
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

describe.skipIf(FIRED)("the fetch handler, and what it declines to answer", () => {
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

/** The two files deploy `n` of a run of them writes — the shape of NEXT_ASSETS. */
const deployAssets = (n: number): string[] => [`/assets/index-D${n}q2mZa.js`, `/assets/index-C${n}8pLm1x.css`];

/** Route deploy `n`: its page at `/`, and the two files it names. */
const routeDeploy = (worker: Harness, n: number): string[] => {
  const assets = deployAssets(n);
  worker.respond("/", deployed(`deploy ${n}`, assets));
  worker.respond(assets[0], js(`console.log(${n})`));
  worker.respond(assets[1], css(`body{order:${n}}`));
  return assets;
};

/**
 * What the cached shell names that the cache does not hold — the files an
 * offline launch of it would fail to load. Empty is the property every
 * collection must keep.
 */
const missing = async (worker: Harness): Promise<string[]> => {
  const page = await shellInCache(worker).clone().text();
  const named = [...page.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((match) => match[1]);
  return named.filter((path) => !worker.keys().includes(path));
};

/** A typeface the next deploy's stylesheet names and this one's does not. */
const NEXT_FONT = "/assets/dm-sans-latin-standard-normal-Q7rT2vXk.woff2";
const NEXT_CSS = `@font-face{font-family:DM Sans Variable;font-display:swap;src:url(${NEXT_FONT})format("woff2-variations")}`;

/** Install's record, as it stands in the cache. */
const recordIn = (worker: Harness): Promise<unknown> =>
  worker.store.get(worker.cache)!.get(`${ORIGIN}/__shell-manifest`)!.clone().json();

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

describe.skipIf(FIRED)("navigations", () => {
  it("serves the live page when there is a network", async () => {
    // Decision 8, and the whole of it: an online phone renders the deploy that
    // is live right now, never the cache. That is what makes a poisoned cache
    // something only a phone that is offline, or cannot reach this origin
    // inside the deadline, can see.
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

  it("lets go of the answer that comes after the shell was served, the moment it comes", async () => {
    // The deadline ends the WAIT on a navigation, not the request: a navigation
    // can carry no signal (see "gives the navigation a deadline without
    // touching the request to do it"), so the fetch that lost the race carries
    // on behind it. Here its headers come two seconds after the shell was
    // served, and a body nobody reads and nobody cancels holds its connection
    // for as long as the server keeps it open — measured in Chromium with no
    // DevTools attached to the worker, 28 s after the headers, until the
    // browser stopped the idle worker.
    const worker = shell();
    await worker.install();
    worker.respond("/", stalled());
    worker.late("/", 12000);
    const start = worker.now;
    const response = await worker.request({ path: "/", mode: "navigate" });
    expect(worker.now).toBe(start + 10000);
    expect(worker.released).toEqual([]);
    await expect(response!.text()).resolves.toContain(ROOT_DIV);
    await until(() => worker.now >= start + 12000);
    expect(worker.released).toEqual(["/"]);
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

  it("gives a navigation as long as the page gives the house to answer", () => {
    // The worker serves the cached shell once TIMEOUT_MS passes with no answer
    // from the network, and lib/auth.ts calls the house silent once
    // STATUS_DEADLINE_MS does. The page and the worker agree on when the house
    // is "not answering": shorter on the page, and a slow but working link is
    // called silent; longer, and a launch that will get no answer sits on the
    // field for nothing. Read here, among the tests of the worker, so that it
    // stands down with them when the kill switch is fired: the switch has no
    // TIMEOUT_MS to read.
    const match = /^const TIMEOUT_MS = (\d+);$/m.exec(source);
    expect(match).not.toBeNull();
    expect(STATUS_DEADLINE_MS).toBe(Number(match?.[1]));
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

  it("asks past the HTTP cache for nothing on a navigation, the page's or the refresh's", async () => {
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    const before = worker.fetched.length;
    const reloads = worker.reloaded.length;
    await worker.request({ path: "/", mode: "navigate" });
    expect(worker.fetched.slice(before)).toEqual(["/", ...NEXT_ASSETS]);
    // The navigation is the page's request, forwarded as it arrived: rewriting
    // its cache mode is not this worker's to do. And what the refresh fetches
    // is the files the new page names, hashed — the HTTP cache can only hold
    // the same bytes under those names, and often does, since the page fetches
    // them too.
    expect(worker.reloaded.slice(reloads)).toEqual([]);
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
    // page's, so they are aborted rather than raced: a signal, and nothing
    // else. No `cache: "reload"` — a hashed file's bytes cannot have changed
    // under its name.
    const refreshed = worker.inits.slice(before + 1);
    expect(refreshed).toHaveLength(2);
    for (const init of refreshed) expect(Object.keys(init!)).toEqual(["signal"]);
    for (const init of refreshed) expect(init!.signal).toBeInstanceOf(AbortSignal);
    // Raced or aborted, the deadline is armed and taken back down either way:
    // one for the navigation, and one for each read of the page's body it
    // answered with — two, the piece and the end — and for each asset the
    // refresh fetched, the request and the two reads of the body that is read
    // whole before it is put. And a ceiling on each of those three bodies.
    expect(ascending(worker.deadlines.slice(armed))).toEqual([
      ...Array<number>(1 + 2 + 2 * 3).fill(10000),
      ...Array<number>(3).fill(CEILING),
    ]);
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

  it("holds two deploys at most, however many deploys it refreshes to", async () => {
    // An ordinary deploy changes no byte of sw.js, so it installs nothing and
    // activates nothing: refresh() is what takes a phone to it, publishing the
    // new shell and the two files it names. The shell it replaced, and that
    // shell's files, are then named by nothing. Collected only at activate —
    // which waits on sw.js's own bytes — they stayed: measured in Chromium
    // against the real server, the cache grew by an entry chunk and a
    // stylesheet on every web deploy, with no limit. Collected after each
    // refresh, it holds install's record and deploy, and the latest deploy.
    const worker = shell();
    await worker.install();
    await worker.activate();
    const counts: number[] = [];
    for (let n = 1; n <= 10; n += 1) {
      routeDeploy(worker, n);
      await worker.request({ path: "/", mode: "navigate" });
      counts.push(worker.keys().length);
    }
    expect(counts).toEqual(Array<number>(10).fill(6));
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", "/index.html", ...SHELL_ASSETS, ...deployAssets(10)].sort(),
    );
    await expect(shellInCache(worker).clone().text()).resolves.toContain("deploy 10");
  });

  it("collects nothing while a worker is installing into the same cache", async () => {
    // A changed sw.js installs into the cache this worker serves from, writing
    // the files its shell names first and its record last, so until the record
    // lands those files are named by nothing. A refresh that collected then
    // would delete them under the install, which has fetched them once and
    // will not again, and the shell it then writes would name files that are
    // not there: a white screen offline.
    const worker = shell();
    await worker.install();
    await worker.activate();
    worker.respond("/index.html", deployed("the update", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    const letGo = worker.holdPut(NEXT_ASSETS[1]);
    const installing = worker.install();
    await until(() => worker.puts.includes(NEXT_ASSETS[1]));
    expect(worker.keys()).toContain(NEXT_ASSETS[0]);
    // A navigation lands mid-install, on a deploy newer still.
    routeDeploy(worker, 3);
    await worker.request({ path: "/", mode: "navigate" });
    await expect(shellInCache(worker).clone().text()).resolves.toContain("deploy 3");
    expect(worker.keys()).toContain(NEXT_ASSETS[0]);
    letGo();
    await installing;
    await expect(shellInCache(worker).clone().text()).resolves.toContain("the update");
    await expect(missing(worker)).resolves.toEqual([]);
    // Once nothing is installing, the next refresh collects what neither the
    // new record nor the shell it publishes names.
    await worker.request({ path: "/", mode: "navigate" });
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", "/index.html", ...NEXT_ASSETS, ...deployAssets(3)].sort(),
    );
  });

  it("keeps the files of a shell it served from the cache, through a refresh that publishes another", async () => {
    // A launch with no network is served the cached shell, and its page asks
    // for the files that shell names a moment later. A refresh that publishes
    // a newer shell in that moment, and collects on the record and the shell
    // it published, takes them — the record names install's deploy, not this
    // one — and the page, with no network, cannot load its own entry chunk.
    const worker = shell();
    await worker.install();
    await worker.activate();
    const first = routeDeploy(worker, 1);
    await worker.request({ path: "/", mode: "navigate" });
    // Deploy 2's refresh, stopped with its files written and its shell not yet.
    routeDeploy(worker, 2);
    const letGo = worker.holdPut("/index.html");
    const two = worker.dispatch({ path: "/", mode: "navigate" });
    await two.responded;
    await until(() => worker.puts.filter((key) => key === "/index.html").length === 3);
    // A launch with no network — no route answers it — is served deploy 1's
    // shell, and deploy 1's files no longer answer from the network either.
    for (const url of first) worker.hang(url);
    const launch = await worker.request({ path: "/activity", mode: "navigate" });
    await expect(launch!.text()).resolves.toContain("deploy 1");
    // Deploy 2's shell lands and its refresh collects; then the page asks for
    // the files its own shell names.
    letGo();
    await two.settled;
    await expect(shellInCache(worker).clone().text()).resolves.toContain("deploy 2");
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", "/index.html", ...SHELL_ASSETS, ...first, ...deployAssets(2)].sort(),
    );
    const [chunk, stylesheet] = first;
    expect((await worker.request(fromPage(chunk)))!.status).toBe(200);
    expect((await worker.request(fromPage(stylesheet, "style")))!.status).toBe(200);
  });

  it("remembers one shell it served from the cache, the last, and the fonts its stylesheet names", async () => {
    // Kept until a later launch from the cache replaces it, or the worker
    // stops: one shell's worth, so a collection leaves three deploys at most.
    // And its stylesheet's fonts with it, which that page asks for as it asks
    // for its chunk.
    const worker = withFonts();
    await worker.install();
    await worker.activate();
    const typefaces = [
      "/assets/dm-sans-latin-standard-normal-R1kq7Lmw.woff2",
      "/assets/dm-sans-latin-standard-normal-T3vw9Npz.woff2",
      "/assets/dm-sans-latin-standard-normal-W6mx4Hqc.woff2",
    ];
    const deploy = (n: number): string[] => {
      const assets = deployAssets(n);
      worker.respond("/", deployed(`deploy ${n}`, assets));
      worker.respond(assets[0], js(`console.log(${n})`));
      worker.respond(
        assets[1],
        css(`@font-face{font-family:DM Sans Variable;src:url(${typefaces[n - 1]})format("woff2-variations")}`),
      );
      worker.respond(typefaces[n - 1], font());
      return assets;
    };
    const record = ["/__shell-manifest", "/index.html", ...SHELL_ASSETS, ...SHELL_FONTS];
    // No route answers /activity: a launch with no network, from the cache.
    const launch = async () => (await worker.request({ path: "/activity", mode: "navigate" }))!.text();
    const first = deploy(1);
    await worker.request({ path: "/", mode: "navigate" });
    await worker.request(fromPage(typefaces[0], "font"));
    await expect(launch()).resolves.toContain("deploy 1");
    // The next online navigation publishes deploy 2 and collects, and what
    // that launch was served stays.
    const second = deploy(2);
    await worker.request({ path: "/", mode: "navigate" });
    await worker.request(fromPage(typefaces[1], "font"));
    expect(worker.keys()).toEqual([...record, ...first, typefaces[0], ...second, typefaces[1]].sort());
    // A launch served deploy 2's shell replaces deploy 1's, and the next
    // collection takes deploy 1's files.
    await expect(launch()).resolves.toContain("deploy 2");
    const third = deploy(3);
    await worker.request({ path: "/", mode: "navigate" });
    expect(worker.keys()).toEqual([...record, ...second, typefaces[1], ...third].sort());
  });

  it("keeps the files of a shell it served from the cache while that shell is still being read", async () => {
    // What a launch was served is recorded when the match answers, not once
    // its body has been read, and a collection that comes to it in between
    // waits for the names. Recorded only once read, they are not there for
    // that collection, which takes the files the page is about to ask for.
    const worker = shell();
    await worker.install();
    await worker.activate();
    const first = routeDeploy(worker, 1);
    await worker.request({ path: "/", mode: "navigate" });
    // A launch with no network — no route answers it — is served deploy 1's
    // shell, and the disk is slow to hand over its body.
    const letGo = worker.holdRead("/index.html");
    const launch = await worker.request({ path: "/activity", mode: "navigate" });
    for (const url of first) worker.hang(url);
    // Deploy 2's refresh publishes its shell and collects meanwhile, given
    // every chance to take deploy 1's files while the read is held.
    routeDeploy(worker, 2);
    const two = worker.dispatch({ path: "/", mode: "navigate" });
    await two.responded;
    await until(() => !worker.keys().includes(first[0]));
    letGo();
    await two.settled;
    await expect(launch!.text()).resolves.toContain("deploy 1");
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", "/index.html", ...SHELL_ASSETS, ...first, ...deployAssets(2)].sort(),
    );
    const [chunk, stylesheet] = first;
    expect((await worker.request(fromPage(chunk)))!.status).toBe(200);
    expect((await worker.request(fromPage(stylesheet, "style")))!.status).toBe(200);
  });

  it("keeps everything while the shell it last served will not read, until a launch replaces it", async () => {
    // Which files that page will ask for is unknown, and a collection does not
    // guess at what a shell it cannot read named: it keeps everything, as it
    // does for any shell it cannot read. Not for ever — the next launch from
    // the cache replaces what was served, and the collection after it takes
    // what nothing names again.
    const worker = shell();
    await worker.install();
    await worker.activate();
    const first = routeDeploy(worker, 1);
    await worker.request({ path: "/", mode: "navigate" });
    // A file nothing names, which any collection that reads its shells takes.
    worker.respond(LATE_FONT, font());
    await worker.request(fromPage(LATE_FONT, "font"));
    // A launch with no network is served deploy 1's shell, and the disk fails
    // to hand over its body.
    worker.refuseRead("/index.html");
    await worker.request({ path: "/activity", mode: "navigate" });
    const second = routeDeploy(worker, 2);
    await worker.request({ path: "/", mode: "navigate" });
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", "/index.html", ...SHELL_ASSETS, ...first, LATE_FONT, ...second].sort(),
    );
    // A launch served a shell that reads, and the next collection takes the
    // rest.
    const launch = await worker.request({ path: "/activity", mode: "navigate" });
    await expect(launch!.text()).resolves.toContain("deploy 2");
    const third = routeDeploy(worker, 3);
    await worker.request({ path: "/", mode: "navigate" });
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", "/index.html", ...SHELL_ASSETS, ...second, ...third].sort(),
    );
  });

  it("refreshes one navigation at a time, so a slow refresh is not collected under", async () => {
    // Two navigations close together, the first refresh still reading a slow
    // stylesheet when the second has published its shell. Run side by side,
    // the second collects the first's chunk — named by nothing yet — and the
    // first then publishes the older shell over the newer, naming a file that
    // is gone. One at a time, the later deploy is the one left, whole.
    const worker = shell();
    await worker.install();
    await worker.activate();
    const first = routeDeploy(worker, 1);
    worker.respond(first[1], worker.trickle("text/css", split("body{order:1}", 10), { every: 1260 }));
    const one = worker.dispatch({ path: "/", mode: "navigate" });
    routeDeploy(worker, 2);
    const two = worker.dispatch({ path: "/", mode: "navigate" });
    await Promise.all([one.settled, two.settled]);
    await expect(shellInCache(worker).clone().text()).resolves.toContain("deploy 2");
    await expect(missing(worker)).resolves.toEqual([]);
    expect(worker.keys()).toEqual(
      ["/__shell-manifest", "/index.html", ...SHELL_ASSETS, ...deployAssets(2)].sort(),
    );
  });

  it("still refreshes after a refresh that failed", async () => {
    // One at a time is a queue, and a queue that stops at its first failure
    // stops the offline copy moving for as long as this worker lives: every
    // refresh after it waiting on one that will never succeed.
    const worker = shell();
    await worker.install();
    await worker.activate();
    worker.respond("/", deployed("unfetchable", NEXT_ASSETS));
    await worker.request({ path: "/", mode: "navigate" });
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("unfetchable");
    routeDeploy(worker, 2);
    await worker.request({ path: "/", mode: "navigate" });
    await expect(shellInCache(worker).clone().text()).resolves.toContain("deploy 2");
  });

  it("keeps the fonts the cached shell's stylesheet names, and collects a superseded deploy's", async () => {
    // A deploy that changes a typeface's hash publishes a stylesheet naming the
    // new file, and the page fetches it through the asset branch: no record
    // names it, and the shell names only the stylesheet. Kept on those two
    // alone, it went at the next refresh's collection, and every offline
    // launch after it rendered in the system stack until sw.js next changed.
    const worker = withFonts();
    await worker.install();
    await worker.activate();
    const typefaces = [
      "/assets/dm-sans-latin-standard-normal-R1kq7Lmw.woff2",
      "/assets/dm-sans-latin-standard-normal-T3vw9Npz.woff2",
    ];
    const deploy = (n: number, typeface: string) => {
      const assets = deployAssets(n);
      worker.respond("/", deployed(`deploy ${n}`, assets));
      worker.respond(assets[0], js(`console.log(${n})`));
      worker.respond(
        assets[1],
        css(`@font-face{font-family:DM Sans Variable;src:url(${typeface})format("woff2-variations")}`),
      );
      worker.respond(typeface, font());
    };
    // Deploy 1 changes the typeface, and the page asks for it.
    deploy(1, typefaces[0]);
    await worker.request({ path: "/", mode: "navigate" });
    await worker.request(fromPage(typefaces[0], "font"));
    // Deploy 2 changes the chunk and the stylesheet, and its stylesheet still
    // names deploy 1's typeface.
    deploy(2, typefaces[0]);
    await worker.request({ path: "/", mode: "navigate" });
    expect(worker.keys()).toContain(typefaces[0]);
    // Deploy 3 changes the typeface again, so deploy 1's is named by nothing.
    deploy(3, typefaces[1]);
    await worker.request({ path: "/", mode: "navigate" });
    await worker.request(fromPage(typefaces[1], "font"));
    expect(worker.keys()).toEqual(
      [
        "/__shell-manifest",
        "/index.html",
        ...SHELL_ASSETS,
        ...SHELL_FONTS,
        ...deployAssets(3),
        typefaces[1],
      ].sort(),
    );
  });

  it.each(STALLS)("serves the page, and keeps the shell it has, when the new one stalls %s", async (_when, sent) => {
    // fetch() resolves on the headers, so the refresh holds a 200 whose body
    // never finishes arriving. Without a deadline on the READ, that `text()`
    // never settles: the navigation's own waitUntil stays open, holding the
    // worker awake for a page the user was served ten seconds ago, and this
    // test would hang rather than fail.
    const worker = shell();
    await worker.install();
    worker.respond("/", stalled(undefined, sent));
    const start = worker.now;
    const { responded, settled } = worker.dispatch({ path: "/", mode: "navigate" });
    const response = await responded;
    // Answered with the clock where it was. On this clock a hold costs no
    // time, so the clock is the only witness that the page did not wait for the
    // refresh: a handler that did would answer at ten seconds, when the
    // refresh's read gives up — and pass every assertion below.
    expect(worker.now).toBe(start);
    await settled;
    expect(worker.now).toBe(start + 10000);
    // The page is answered from the network regardless: the refresh is
    // background work, and nothing it does can reach what was served.
    expect(response!.status).toBe(200);
    // And the shell that survives is the one install wrote.
    await expect(shellInCache(worker).clone().text()).resolves.toContain(ROOT_DIV);
    // The refresh gives up on its COPY of the body and on nothing more. The
    // page holds the other branch, so the connection is the page's to keep:
    // not released, and the page's own read is still open — not errored, not
    // cut short — exactly as it would be with no worker installed. Aborting the
    // request instead would have ended the page's read with the worker's.
    expect(worker.released).toEqual([]);
    await expect(pending(response!.text())).resolves.toBe("pending");
  });

  it.each(STALLS)("serves the page, and keeps the shell it has, when a new deploy's asset stalls %s", async (_when, sent) => {
    // The refresh's own fetches, and the put that reads their bodies. A chunk
    // whose headers arrive and whose bytes do not leaves that put pending for
    // ever — inside the navigation's waitUntil, holding the worker awake for a
    // page served long ago, and with the shell never published. Declined like
    // any other asset the refresh cannot get whole: the shell that is there
    // stays. `request()` drains waitUntil, so an unbounded put hangs this test.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], stalled("text/javascript", sent));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    const start = worker.now;
    const { responded, settled } = worker.dispatch({ path: "/", mode: "navigate" });
    const response = await responded;
    // Answered before anything the refresh armed has fired, and the refresh
    // let go ten seconds later — see the stall above for why the clock is the
    // assertion that sees a page held for it.
    expect(worker.now).toBe(start);
    await settled;
    expect(worker.now).toBe(start + 10000);
    await expect(response!.text()).resolves.toContain("two");
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("two");
    expect(worker.puts).not.toContain(NEXT_ASSETS[0]);
    // That fetch is the refresh's own, so giving up on it lets it go.
    expect(worker.released).toEqual([NEXT_ASSETS[0]]);
  });

  it("lets go of a new deploy's asset two minutes after its headers, however steadily it is still arriving", async () => {
    // refresh() reads its own fetches inside the navigation's waitUntil, and a
    // chunk that never goes quiet is never cut by the deadline on silence.
    // Measured in Chromium with no DevTools attached, one sending a byte every
    // five seconds held the connection open, and the worker awake for it, for
    // 309 s, until the browser's own limit on the event. The ceiling lets go of
    // it at two minutes, and the shell that is there stays.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], unending(worker, "text/javascript"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    const start = worker.now;
    const { responded, settled } = worker.dispatch({ path: "/", mode: "navigate" });
    await responded;
    expect(worker.now).toBe(start);
    await settled;
    expect(worker.now).toBe(start + CEILING);
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("two");
    expect(worker.puts).not.toContain(NEXT_ASSETS[0]);
    expect(worker.released).toEqual([NEXT_ASSETS[0]]);
  });

  it("gives up its copy of a page still arriving two minutes on, and leaves the page its own", async () => {
    // The refresh's read of the page's body, under the same ceiling. It is a
    // copy, so giving up on it cancels only the copy: the shell that is there
    // stays, and the page reads on past the ceiling to the last byte, as it
    // would with no worker installed.
    const worker = shell();
    await worker.install();
    worker.respond("/", unending(worker, "text/html; charset=utf-8"));
    const start = worker.now;
    const { responded, settled } = worker.dispatch({ path: "/", mode: "navigate" });
    const response = await responded;
    await settled;
    expect(worker.now).toBe(start + CEILING);
    await expect(shellInCache(worker).clone().text()).resolves.toContain(ROOT_DIV);
    expect(worker.released).toEqual([]);
    await expect(response!.text()).resolves.toBe("x".repeat(80));
    expect(worker.now).toBeGreaterThan(start + CEILING);
  });

  it("lets go of a new deploy's asset it turns down unread", async () => {
    // A page of HTML under a hashed URL the new deploy names — what this
    // origin's SPA fallback answers for a file it does not have. refresh()
    // turns it down on its headers, and that fetch is its own: nothing else
    // will ever read the body or end it, so the refresh does. Measured in
    // Chromium with nothing inspecting the worker, one left alone held its
    // connection until the worker was stopped for being idle, 30 s on.
    const worker = shell();
    await worker.install();
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], stalled());
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    await worker.request({ path: "/", mode: "navigate" });
    await expect(shellInCache(worker).clone().text()).resolves.not.toContain("two");
    expect(worker.released).toEqual([NEXT_ASSETS[0]]);
  });

  it.each([
    ["it is not HTML", false],
    ["there is no cache to keep it in", true],
  ])("lets go of its copy of the page when %s, so the page alone holds the connection", async (_why, unopenable) => {
    // The refresh's copy of what the page is reading, turned down before a
    // byte of it is read. A copy nobody reads and nobody cancels keeps the
    // connection open after the page has let go of its own — a request is torn
    // down only once every branch of its body is done with — and for as long as
    // the copy lives, the bytes the page reads are queued in it as well.
    const worker = shell();
    await worker.install();
    worker.respond("/", stalled(unopenable ? undefined : "application/json"));
    if (unopenable) worker.refuseOpen();
    const response = await worker.request({ path: "/", mode: "navigate" });
    // The page's to keep while it reads...
    expect(worker.released).toEqual([]);
    // ...and gone the moment it stops, with nothing of the worker's left
    // holding it. The page's cancel itself waits on the copy: left alone, the
    // copy would keep it pending.
    await expect(pending(response!.body!.cancel())).resolves.toBe("settled");
    expect(worker.released).toEqual(["/"]);
  });

  it.each([
    ["its page", "/"],
    ["a chunk its page names", NEXT_ASSETS[0]],
  ])("publishes a new deploy when %s arrives slowly, for as long as it keeps arriving", async (_name, path) => {
    // install's slow link, met on a navigation: every piece inside the
    // deadline, the body as a whole past it. Cut off there, the refresh keeps
    // the old shell — survivable, but it is the ONLY thing that moves the
    // offline copy between installs, so a phone on a slow link would never get
    // a newer one.
    const worker = shell();
    await worker.install();
    const bodies: Record<string, [string, string]> = {
      "/": ["text/html; charset=utf-8", deployedHtml("two", NEXT_ASSETS)],
      [NEXT_ASSETS[0]]: ["text/javascript", "console.log(2)"],
    };
    worker.respond("/", deployed("two", NEXT_ASSETS));
    worker.respond(NEXT_ASSETS[0], js("console.log(2)"));
    worker.respond(NEXT_ASSETS[1], css("body{color:blue}"));
    const [type, body] = bodies[path];
    worker.respond(path, worker.trickle(type, split(body, 10), { every: 1260 }));
    await worker.request({ path: "/", mode: "navigate" });
    expect(worker.now).toBeGreaterThanOrEqual(12600);
    await expect(shellInCache(worker).clone().text()).resolves.toContain("two");
    const stored = worker.store.get(worker.cache)!.get(`${ORIGIN}${NEXT_ASSETS[0]}`);
    await expect(stored!.clone().text()).resolves.toBe("console.log(2)");
  });
});

describe.skipIf(FIRED)("assets", () => {
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

  it.each(STALLS)("serves an asset whose body stalls %s without holding the page for the write", async (_when, sent) => {
    // The put reads the body to the end, and this body never ends. Awaited
    // before the page is answered, that put held the page's own response —
    // headers and all — for as long as the stall lasted: strictly worse than no
    // worker, where the page at least gets the headers and whatever bytes came.
    // The page gets exactly what the network gave it, and the write, bounded,
    // runs beside it.
    const worker = shell();
    await worker.install();
    worker.respond(LATE_FONT, stalled("font/woff2", sent));
    const start = worker.now;
    const { responded, settled } = worker.dispatch(fromPage(LATE_FONT, "font"));
    const response = await responded;
    // "Without holding the page" is a statement about TIME, and on this clock a
    // hold takes none: a handler that waited for the write would pass every
    // other line here, answering at ten seconds, once the write gave up. So the
    // clock is read the moment the page has its answer, and it has not moved.
    expect(worker.now).toBe(start);
    await settled;
    expect(worker.now).toBe(start + 10000);
    expect(response!.status).toBe(200);
    expect(worker.keys()).not.toContain(LATE_FONT);
    expect(worker.puts).not.toContain(LATE_FONT);
    // And the write's giving up reaches the page no more than its waiting did.
    // This request is the PAGE's, so the worker cancels only its own copy: the
    // connection is not released, and the page's read is still open, as it
    // would be with no worker at all. An abort of the request would have
    // errored it — the write reaching the page by the one door left.
    expect(worker.released).toEqual([]);
    await expect(pending(response!.text())).resolves.toBe("pending");
  });

  it("gives up the write's copy of an asset still arriving two minutes on, and leaves the page its own", async () => {
    // The write is bounded like every other read of a body, and what it gives
    // up is its own copy: the page's read runs on past the ceiling to the last
    // byte, exactly as it would with no worker installed.
    const worker = shell();
    await worker.install();
    worker.respond(LATE_FONT, unending(worker, "font/woff2"));
    const start = worker.now;
    const { responded, settled } = worker.dispatch(fromPage(LATE_FONT, "font"));
    const response = await responded;
    expect(worker.now).toBe(start);
    await settled;
    expect(worker.now).toBe(start + CEILING);
    expect(worker.puts).not.toContain(LATE_FONT);
    expect(worker.released).toEqual([]);
    await expect(response!.text()).resolves.toBe("x".repeat(80));
    expect(worker.now).toBeGreaterThan(start + CEILING);
  });

  it("lets go of its copy of an asset it has no cache to store in, so the page alone holds the connection", async () => {
    // The write's copy, turned down before a byte of it is read: the store will
    // not open. The navigation's twin above says why the copy is cancelled
    // rather than dropped.
    const worker = shell();
    await worker.install();
    worker.refuseOpen();
    worker.respond(LATE_FONT, stalled("font/woff2"));
    const response = await worker.request(fromPage(LATE_FONT, "font"));
    expect(worker.released).toEqual([]);
    await expect(pending(response!.body!.cancel())).resolves.toBe("settled");
    expect(worker.released).toEqual([LATE_FONT]);
  });

  it("hands the page an asset whose body breaks off just as it broke, and stores nothing", async () => {
    // The page's read fails the way it would with no worker installed, and the
    // write gives up on its copy without a rejection escaping the event.
    const worker = shell();
    await worker.install();
    worker.respond(LATE_FONT, broken("font/woff2"));
    const response = await worker.request(fromPage(LATE_FONT, "font"));
    await expect(response!.text()).rejects.toThrow("network error");
    expect(worker.keys()).not.toContain(LATE_FONT);
  });

  it("stores an asset that arrives slowly, for as long as it keeps arriving", async () => {
    // The runtime twin of install's slow link: every piece inside the
    // deadline, the whole past it. Cut off, the page still got its bytes — the
    // write is beside the answer — but the next offline launch would not have
    // them.
    const worker = shell();
    await worker.install();
    worker.respond(LATE_FONT, worker.trickle("font/woff2", split("woff2".repeat(4), 10), { every: 1260 }));
    const start = worker.now;
    const { responded, settled } = worker.dispatch(fromPage(LATE_FONT, "font"));
    const response = await responded;
    // The page has its headers at once, not once the write has the whole body.
    expect(worker.now).toBe(start);
    await settled;
    // The PAGE's copy, whole, and this is the assertion that reaches the
    // request's own deadline. That one is on the headers alone and taken down
    // when they arrive; left armed past them, it fires at ten seconds with this
    // body still arriving, and an abort errors every branch of a body — the
    // page's along with the write's — where a cancel would stop at the tee.
    await expect(response!.text()).resolves.toBe("woff2".repeat(4));
    expect(worker.now).toBeGreaterThanOrEqual(12600);
    expect(worker.keys()).toContain(LATE_FONT);
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
    // holds. install and refresh() leave every hashed file to it the same way;
    // the one thing either asks past it for is the HTML, which a deploy
    // rewrites under the same name.
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

describe.skipIf(FIRED)("the update handshake", () => {
  /**
   * Every worker here installs over an older one that is still active: a
   * changed sw.js arriving at a phone that already runs the app, which is the
   * only way a worker ever ends up waiting. A first install has nothing to wait
   * behind, so it cannot tell a worker that never skips waiting from one that
   * skips exactly when there is someone to take over from.
   */
  const update = (): Harness => shell({ update: true });

  it("does not take over on install", async () => {
    const worker = update();
    await worker.install();
    // A changed sw.js that took over by itself would do it under a running
    // conversation and lose whatever was on screen. The new worker waits until
    // the user asks for it.
    expect(worker.skipWaitingCalls).toBe(0);
  });

  it("takes over when the page asks", async () => {
    const worker = update();
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
    const worker = update();
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
    const worker = update();
    await worker.install();
    await worker.message(undefined);
    expect(worker.skipWaitingCalls).toBe(0);
  });
});
