/* Alfred's service worker. Hand-written on purpose; the reasoning is in
   docs/superpowers/plans/2026-09-16-pwa-phase4-install-and-offline.md and the
   summary is in web/README.md, "The service worker".

   AS SHIPPED TODAY this file has two handlers: `install` precaches the app
   shell, and `activate` purges every cache this version does not own and every
   entry this install did not write, then claims the open pages. It answers no
   request — there is no `fetch` handler in it yet — so everything still goes to
   the network untouched. The three rules below are the policy the rest of it is
   built under; rules 1 and 2 describe the fetch handler that later commits add,
   and this paragraph goes when they land.

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
/* The one path prefix this worker may store under, named rather than spelled
   inline because it is the whole of the refusal isAssetUrl makes. */
const ASSETS = "/assets/";
/* Every network call here gets a deadline, and that is a different guarantee
   from the `.catch()` in the font loop. A catch covers a fetch that REJECTS; it
   does nothing at all for one that never settles, which is exactly what a
   captive portal does — it accepts the connection and answers nothing. precache
   runs inside waitUntil(), so one hung request hangs the whole install until the
   browser gives up on it, and the phone is left with no worker: the outcome the
   font loop's catch exists to avoid, reached through the other door.

   Per request, not per install, so a slow-but-progressing phone is not punished
   for being slow; the ceiling is this times the eleven requests a real build
   makes, and only the fonts' share of it is survivable. */
const TIMEOUT_MS = 10000;

/* The only way this file fetches anything.

   `cache: "reload"` — the HTTP cache is the only thing that could hand a fresh
   worker the bytes a deploy just replaced. */
function fetchFresh(url) {
  const controller = new self.AbortController();
  const deadline = self.setTimeout(() => controller.abort(), TIMEOUT_MS);
  return self
    .fetch(url, { cache: "reload", signal: controller.signal })
    .finally(() => self.clearTimeout(deadline));
}

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
   widening the cache.

   `base` is what the URL is relative to, and it differs by caller because it
   differs in a browser: index.html's own references resolve against the
   document, and a url() inside a stylesheet resolves against the STYLESHEET.
   Getting that wrong cannot widen anything — the guard runs on the result — but
   it silently precaches a key nothing will ever ask for.

   Three things it is deliberately strict about, in the order they bite:
   the origin, the /assets/ prefix — an anchor, so /api/admin/assets/x and
   /assetsphalt/x are both outside it — and an encoded separator, which the URL
   parser does not treat as one and a server may: /assets/..%2fapi/x reads as a
   path under /assets/ here and as a traversal once uvicorn has decoded it. No
   name Vite emits contains a %2f.

   Known and accepted: the pathname is what survives, so a query or a fragment
   is dropped. Vite hashes into the filename and never writes either, and
   dropping them keeps one spelling of a cache key across the manifest, the
   cache and task 7's lookup. */
function isAssetUrl(url, base) {
  try {
    const resolved = new URL(url, base || scopeOrigin());
    return (
      resolved.origin === scopeOrigin() &&
      resolved.pathname.startsWith(ASSETS) &&
      !/%2f/i.test(resolved.pathname)
    );
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
   URL, and the same bytes twice is two fetches and two puts.

   `(url) => isAssetUrl(url)` rather than a bare `isAssetUrl`: filter passes the
   index as the second argument, which is isAssetUrl's `base`, and every asset
   after the first would resolve against a number and be refused. */
function assetsInHtml(html) {
  const urls = [...html.matchAll(/(?:src|href)="([^"]+)"/g)]
    .map((match) => match[1])
    .filter((url) => isAssetUrl(url))
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
   that walks out of /assets/, which is resolved before it is judged. `base` is
   the stylesheet's own URL, which is what a browser resolves a url() against.

   Wide really is wide, and two consequences are accepted rather than fixed. A
   url() inside a comment or a content: string is taken — the real stylesheet
   carries one comment, Tailwind's licence banner — and so is one under a
   property that has nothing to do with fonts. Both cost an install request and
   some cache bytes that nothing asks for; neither can escape isAssetUrl. The
   alternative is a CSS tokeniser inside a service worker, which is not a trade
   this file will make. Case-insensitive because CSS is: `URL(…)` and `.WOFF2`
   are the same token to a browser, and a silently missing typeface is a poor
   thing to buy with an `i`.

   Then a pathname, as assetsInHtml returns: it is what goes into MANIFEST, and
   activate prunes by comparing that record against the cache's own keys, so a
   font recorded with the stylesheet's spacing still on it is a font the next
   activate deletes.

   `.woff2` last, and it is a narrower question than the others — about
   browsers, not about what may be stored. @fontsource ships a `.woff` beside
   four of these faces and for six more it is the only file (10 in all, 80 944 of
   this build's 220 380 font bytes), and everything that can run a service worker
   has supported woff2 for years: a `.woff` is dead weight ALWAYS, whoever is
   reading. `endsWith`, not `includes`, so a `x.woff2.br` sidecar from a
   pre-compressing proxy is not mistaken for a typeface.

   The latin-ext and cyrillic subsets (56 732 of the 139 436 bytes taken) are
   NOT the same trade and are kept deliberately. Every face is unicode-range
   gated, so a browser asks for them only when the text contains those
   codepoints — but "only when" is not "never": a memory, a trigger name or a
   pasted line can contain one at any time, and the price of having guessed
   wrong is tofu on a phone with no network. Dead-always is refused; dead-until
   is bought. Duplicates are not removed here: precache() holds that property
   over every stylesheet at once, and one place is enough. */
function fontsInCss(css, base) {
  return [...css.matchAll(/url\(\s*["']?([^)"']+)/gi)]
    .map((match) => match[1])
    .filter((url) => isAssetUrl(url, base))
    .map((url) => new URL(url, base).pathname)
    .filter((path) => path.toLowerCase().endsWith(".woff2"));
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

  const shell = await fetchFresh(SHELL);
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
    const response = await fetchFresh(url);
    /* Throwing aborts the install, which is the right answer: a half-precached
       shell boots to a blank screen offline, and a worker that never installs
       leaves the previous one — or no worker at all — in charge. A deadline
       that fires here arrives as a rejection and lands in the same place. */
    if (!isAsset(response)) throw new Error(`${url} ${response.status}`);
    responses.push(response);
  }

  /* The stylesheet only. A bundle is not a list of what the app needs offline:
     any library that injects styles carries @font-face rules inside it, and
     reading 414 kB of minified JavaScript into a string to regex it is a cost
     with no return. (This build's chunk does contain the text `url(` twice, but
     both are a zero-argument method of that name in the WebSocket client, so
     the regex captures nothing from it — the rule stands on its own, not on
     that measurement.) */
  const named = [];
  for (let i = 0; i < urls.length; i += 1) {
    if (!urls[i].endsWith(".css")) continue;
    const base = new URL(urls[i], scopeOrigin()).href;
    named.push(...fontsInCss(await responses[i].clone().text(), base));
  }

  /* One fetch, one put and one manifest entry per URL, whatever names it: a
     single src: list can name a file twice, two stylesheets can name the same
     subset, and a <link rel="preload" as="font"> would have taken it in the
     asset pass already. Task 3 holds the same property over the HTML for the
     same reason — a manifest listing something twice is what activate prunes
     against. */
  const fonts = [...new Set(named)].filter((url) => !urls.includes(url));

  /* Unlike a missing chunk above, a missing font does NOT fail the install. The
     app is legible in the system stack, and an install aborted over a typeface
     leaves the phone with no worker at all — the worse of the two, and one that
     only sw.js's own bytes changing would undo. All three failures are declined
     the same way: no answer (the fetch rejects), no answer ever (the deadline
     fires, which arrives as a rejection too) and a wrong answer (a proxy's page
     under a .woff2 URL). Anything skipped is still picked up cache-first the
     first time it is asked for — by the fetch handler task 7 adds; until then
     it is simply absent. */
  const cached = [];
  for (const url of fonts) {
    const response = await fetchFresh(url).catch(() => null);
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

/* The recovery path from a bad cache, and the one place stale bytes are ever
   deleted. Two purges, because there are two ways they accumulate. */
async function purge() {
  /* Between versions: bump VERSION and the next activate takes the whole
     previous world with it. The `alfred-` prefix is not filtered on — anything
     this origin holds that is not the current cache is not ours to keep, and
     that includes whatever a previous experiment (or a Workbox trial) left.
     Awaited, not fired off: an unawaited delete is outside waitUntil, and the
     browser may stop the worker with the cache still there. Caught per delete,
     too: this is the recovery path from a bad cache, and one transient failure
     must not cost the caches after it, the entry purge below, or the claim. */
  for (const name of await self.caches.keys()) {
    if (name !== CACHE) await self.caches.delete(name).catch(() => {});
  }

  /* Within a version: every entry that is not in the manifest install just
     wrote. Asset URLs are content-hashed, so a deploy's worth of them is dead
     the moment a new worker activates — and VERSION is bumped by hand, so for
     every deploy that does not bump it this is the ONLY thing that reaches what
     the previous build of this same file cached. On iOS an origin over its
     storage budget is evicted whole rather than trimmed.

     It also reaches, by the same rule, whatever the fetch handler stores at
     runtime: an asset cached on a cache-first miss is not in the record install
     wrote, so the next activate takes it. That is the intended reading — the
     record is what this build needs, a runtime entry is re-fetchable, and the
     alternative is a cache with no ceiling — but it is a decision the fetch
     handler inherits rather than makes, so it is written down here.

     Note the window this leaves: the new worker precaches into the cache the
     old one is still serving from, so between install and activate the cached
     shell is the new HTML. Its assets are already cached by then, and the only
     reader of a cached shell is an offline navigation. */
  const cache = await self.caches.open(CACHE);
  /* Snapshotted BEFORE the record is read, and the order is the whole point. A
     worker installing concurrently (the page re-checks for an update on every
     return to the foreground) writes its entries first and its record last. Read
     the record first and any entry it wrote after that read but before this
     snapshot is deleted — and its install has already resolved, so it never
     refetches. This way round, an install that completes between the two reads
     is read from its own record, and entries written after the snapshot are not
     in it to delete. It narrows the race; it does not close it. Closing it needs
     a lock two workers can share, which a service worker does not have. */
  const entries = await cache.keys();

  /* Three ways the record can be untrustworthy — absent, wrong shape, or
     unreadable — and one reading of all three: keep everything, and let the next
     install rewrite the record. Deleting on a record you cannot read is
     guessing, and the thing it guesses with is the only copy of this app the
     phone has offline. */
  let keep;
  try {
    const listed = await cache.match(MANIFEST);
    const record = listed && (await listed.json());
    /* Array.isArray, not a truth test. A JSON string spreads into single
       characters, none of which resolve to a real key, so every precached entry
       would go — silently, with nothing rejecting. `precache()` is the only
       writer today and always writes an array; the guard is for the edit that
       changes the record's shape (`{version, files}` is the obvious one). */
    if (!Array.isArray(record)) return;
    /* Absolute, because a cache key is. The record holds pathnames — one
       spelling across the manifest, the cache and task 7's lookup — but
       comparing on the pathname alone would keep a cross-origin entry that
       merely shares one, and a poisoned CDN copy of the entry chunk is exactly
       the thing this purge is the recovery from. Resolved against the scope for
       the same reason isAssetUrl does: one notion of "us" in this file. */
    keep = new Set([MANIFEST, ...record].map((path) => new URL(path, scopeOrigin()).href));
  } catch {
    /* A body that is not JSON. /__shell-manifest is not a real endpoint —
       mount_spa answers any unknown path with index.html and a 200 — so a page
       of HTML is what the network would put under that key if anything ever
       fetched it. Uncaught, the rejection would take the claim down with it,
       AFTER the version purge above had already deleted the old world. */
    return;
  }

  for (const request of entries) {
    if (!keep.has(request.url)) await cache.delete(request).catch(() => {});
  }
}

/* claim() after purge(), not beside it: a page handed to this worker before the
   purge has finished is a page being served from a cache that is still being
   emptied underneath it. */
self.addEventListener("activate", (event) => {
  event.waitUntil(purge().then(() => self.clients.claim()));
});
