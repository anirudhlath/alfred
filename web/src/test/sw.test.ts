import { describe, expect, it } from "vitest";
// The shipped bytes, not a TypeScript copy of them — same reason
// index-html.test.ts reads the shipped index.html.
import source from "../../public/sw.js?raw";
import { css, html, js, loadWorker, type Harness } from "./sw-harness";

/** What `vite build` really emits: one module script, one stylesheet. */
const SHELL_HTML = `<!doctype html><html lang="en"><head>
<link rel="manifest" href="/manifest.json" />
<script type="module" crossorigin src="/assets/index-CY0Sh5Q2.js"></script>
<link rel="stylesheet" crossorigin href="/assets/index-nhBuGG1b.css">
</head><body><div id="root"></div></body></html>`;

function shell(): Harness {
  const worker = loadWorker(source);
  worker.respond("/index.html", html(SHELL_HTML));
  worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
  worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
  return worker;
}

describe("install", () => {
  it("caches the shell and every asset index.html names", async () => {
    const worker = shell();
    await worker.install();
    expect(worker.keys()).toEqual([
      "/__shell-manifest",
      "/assets/index-CY0Sh5Q2.js",
      "/assets/index-nhBuGG1b.css",
      "/index.html",
    ]);
  });

  it("records what it cached, so activate can prune against it", async () => {
    const worker = shell();
    await worker.install();
    const listed = await worker.store
      .get("alfred-shell-v1")!
      .get("https://alfred.example.com/__shell-manifest")!
      .json();
    expect(listed).toEqual([
      "/index.html",
      "/assets/index-CY0Sh5Q2.js",
      "/assets/index-nhBuGG1b.css",
    ]);
  });

  it("fetches every part of the shell, in the order it learns about them", async () => {
    const worker = shell();
    await worker.install();
    // The HTML first, because it is the manifest; then what it named.
    expect(worker.fetched).toEqual([
      "/index.html",
      "/assets/index-CY0Sh5Q2.js",
      "/assets/index-nhBuGG1b.css",
    ]);
  });

  it("asks past the HTTP cache for every one of them", async () => {
    const worker = shell();
    await worker.install();
    // Without `cache: "reload"`, a worker installing seconds after a deploy can
    // precache the bytes that deploy just replaced, and serve them until the
    // next one. Every install fetch, not merely the HTML — spelled out rather
    // than compared against `fetched`, which two empty lists would also satisfy.
    expect(worker.reloaded).toEqual([
      "/index.html",
      "/assets/index-CY0Sh5Q2.js",
      "/assets/index-nhBuGG1b.css",
    ]);
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

  it("refuses an asset that comes back as the SPA fallback", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    // mount_spa answers any unknown path with index.html and a 200, so a hashed
    // chunk this deploy removed arrives as a page of HTML with `ok` true.
    // Caching that under a .js URL is how a PWA bricks itself.
    worker.respond("/assets/index-CY0Sh5Q2.js", html(SHELL_HTML));
    worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
    await expect(worker.install()).rejects.toThrow();
    expect(worker.keys()).toEqual([]);
  });
});
