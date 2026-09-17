/* Alfred's service worker. Hand-written on purpose; the reasoning is in
   docs/superpowers/plans/2026-09-16-pwa-phase4-install-and-offline.md and the
   summary is in web/README.md, "The service worker".

   AS SHIPPED TODAY this file has one handler, `install`: it precaches the app
   shell and stops. It answers no request — there is no `fetch` handler in it
   yet — so everything still goes to the network untouched. The three rules
   below are the policy the rest of it is built under; rules 1 and 2 describe
   the fetch handler that later commits add, and this paragraph goes when they
   land.

   1. It caches the app shell and NOTHING ELSE. `/api/*`, `/health` and the two
      sockets are never fetched here, never stored, and — once there is a fetch
      handler — never passed to respondWith() at all: not "network-only", but
      never seen, so no branch here can store one of their responses even by
      accident. This client's whole design is that it never states a server fact
      it has not just read; a cached API response is that failure with a longer
      half-life.
   2. A navigation is network-first. The cached shell is served on exactly one
      path — a phone with no network — so a bad cache is invisible to an online
      phone, and the browser's own update check on /sw.js keeps running, which
      is what lets sw.kill.js land if this file ever has to be withdrawn.
   3. Every global goes through `self.`. Valid in a worker, and the seam
      src/test/sw-harness.ts hangs a fake scope on. eslint.config.js's
      `no-restricted-globals` block is what enforces it; without that rule
      `globals.serviceworker` makes a bare `caches` or `fetch` perfectly legal.
*/

const VERSION = "v1";
const CACHE = `alfred-shell-${VERSION}`;
const SHELL = "/index.html";
/* Where install records what it precached so activate can drop everything else
   without re-reading index.html. A worker can be killed between the two events,
   so this cannot be a variable. The app never asks for it: it is not a
   navigation and not under /assets/, the only two things this worker's routing
   will ever answer. */
const MANIFEST = "/__shell-manifest";

/* The origin this worker was served from, and the only one it will ever touch.
   `self.registration.scope` rather than `self.location`: the scope is what
   actually bounds a worker, and it keeps one notion of "us" in this file — the
   fetch handler's cross-origin refusal reads the same value. Not named `origin`
   because a worker already has a global of that name. */
function scopeOrigin() {
  return new URL(self.registration.scope).origin;
}

/* The single decision about what MAY be precached, taken before anything is
   fetched. This judges a URL; `isAsset` below judges the response that comes
   back. Everything the HTML names that is not a same-origin /assets/ path —
   /manifest.json, the icons, anything under /api, a CDN script somebody adds —
   is refused here, which is what keeps widening the extraction below from
   widening the cache. */
function isAssetUrl(url) {
  try {
    const resolved = new URL(url, scopeOrigin());
    return resolved.origin === scopeOrigin() && resolved.pathname.startsWith("/assets/");
  } catch {
    return false;
  }
}

/* Vite content-hashes every asset and writes the entry chunk and the stylesheet
   into index.html. That makes the HTML the precache manifest, which is why this
   worker needs no build step and no plugin.

   The extraction is deliberately wide and the refusal is isAssetUrl's alone:
   one place decides what may be stored, not two. Deduplicated because a build
   that splits writes a `modulepreload` hint beside the tag that names the same
   URL, and the same bytes twice is two fetches and two puts. */
function assetsInHtml(html) {
  const urls = [...html.matchAll(/(?:src|href)="([^"]+)"/g)]
    .map((match) => match[1])
    .filter(isAssetUrl)
    .map((url) => new URL(url, scopeOrigin()).pathname);
  return [...new Set(urls)];
}

/* The stylesheet is the fonts' manifest the way index.html is the assets': the
   build writes 8 woff2 files into /assets/ and names them from url() rules the
   HTML never mentions, so without this pass a cold offline launch renders in
   -apple-system rather than DM Sans.

   Wide extraction, one refusal — isAssetUrl again, for the same reason it is
   the only one in assetsInHtml. It is what turns away the six
   url(data:font/woff2;base64,…) rules in the real stylesheet (whose bytes are
   already cached, inside the stylesheet itself), a CDN's @font-face, and a path
   that walks out of /assets/, which is resolved before it is judged.

   Then a pathname, as assetsInHtml returns: it is what goes into MANIFEST, and
   activate prunes by comparing that record against the cache's own keys, so a
   font recorded with the stylesheet's spacing still on it is a font the next
   activate deletes.

   `.woff2` is the second, narrower question, and it is about browsers rather
   than about what may be stored: @fontsource ships a `.woff` beside almost
   every woff2 — 10 of them, 79 kB of this build's 215 kB of fonts — and
   everything that can run a service worker has supported woff2 for years. */
function fontsInCss(css) {
  return [...css.matchAll(/url\(\s*["']?([^)"']+)/g)]
    .map((match) => match[1])
    .filter(isAssetUrl)
    .map((url) => new URL(url, scopeOrigin()).pathname)
    .filter((path) => path.endsWith(".woff2"));
}

/* `/assets` is a StaticFiles MOUNT (core/channels/spa.py:39-40), not part of the
   SPA fallback, so a hashed file a deploy has removed comes back 404 with an
   `application/json` body — `{"detail":"Not Found"}` — and it is `response.ok`
   that catches it. tests/core/channels/test_spa.py:155-160 pins that 404 server
   side.

   The content-type half is defence in depth for the day that stops being true:
   if the mount is ever removed the fallback answers any unknown path with
   index.html and a 200, and a proxy or a captive portal can answer 200 with a
   page of its own at any time. Storing either under a .js URL is how a PWA
   bricks itself, so both halves stay. */
function isAsset(response) {
  return response.ok && !(response.headers.get("content-type") || "").includes("text/html");
}

function isShell(response) {
  return response.ok && (response.headers.get("content-type") || "").includes("text/html");
}

async function precache() {
  const cache = await self.caches.open(CACHE);

  /* `cache: "reload"` on every one of these: the HTTP cache is the only thing
     that could hand a fresh worker the bytes a deploy just replaced. */
  const shell = await self.fetch(SHELL, { cache: "reload" });
  if (!isShell(shell)) throw new Error(`shell ${shell.status}`);
  const urls = assetsInHtml(await shell.clone().text());

  /* A 200 page of text/html that names no hashed asset at all is not this app.
     Two that really answer that description: the reverse proxy's own default
     page, while a proxy host is being edited or the DNS record has moved; and
     the dev server's index.html, which names /@vite/client and /src/main.tsx
     and nothing hashed. Either would install cleanly as the shell and then stay
     — `install` only re-runs when these bytes change, so the recovery from one
     is sw.kill.js, not a reload. */
  if (urls.length === 0) throw new Error("shell names no assets");

  const responses = [];
  for (const url of urls) {
    const response = await self.fetch(url, { cache: "reload" });
    /* Throwing aborts the install, which is the right answer: a half-precached
       shell boots to a blank screen offline, and a worker that never installs
       leaves the previous one — or no worker at all — in charge. */
    if (!isAsset(response)) throw new Error(`${url} ${response.status}`);
    responses.push(response);
  }

  /* The stylesheet only. The entry chunk is 400 kB of minified JavaScript that
     already contains `url(` — a method of that name, in the WebSocket client —
     and any library that injects styles carries @font-face rules in it. A
     bundle is not a list of what the app needs offline. */
  const fonts = [];
  for (let i = 0; i < urls.length; i += 1) {
    if (!urls[i].endsWith(".css")) continue;
    fonts.push(...fontsInCss(await responses[i].clone().text()));
  }

  /* Unlike a missing chunk above, a missing font does NOT fail the install. The
     app is legible in the system stack, and an install aborted over a typeface
     leaves the phone with no worker at all — the worse of the two, and one that
     only sw.js's own bytes changing would undo. Both failures are declined the
     same way: no answer (the fetch rejects) and a wrong answer (a proxy's page
     under a .woff2 URL). Anything skipped is still picked up cache-first the
     first time it is asked for. */
  const cached = [];
  for (const url of fonts) {
    const response = await self.fetch(url, { cache: "reload" }).catch(() => null);
    if (response && isAsset(response)) cached.push([url, response]);
  }

  /* Nothing is written until everything has been validated, so a shell that
     fails on its second asset leaves the previous worker's cache untouched
     rather than half replaced. */
  await cache.put(SHELL, shell);
  for (let i = 0; i < urls.length; i += 1) await cache.put(urls[i], responses[i]);
  for (const [url, response] of cached) await cache.put(url, response);
  /* The fonts are listed too: activate prunes everything this record does not
     name, so a font cached but unlisted is deleted before it is ever served. */
  await cache.put(
    MANIFEST,
    new Response(JSON.stringify([SHELL, ...urls, ...cached.map(([url]) => url)])),
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(precache());
});
