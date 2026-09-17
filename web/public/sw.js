/* Alfred's service worker. Hand-written on purpose; the reasoning is in
   docs/superpowers/plans/2026-09-16-pwa-phase4-install-and-offline.md and the
   summary is in docs/web-frontend.md, "The service worker".

   Three rules govern every line below.

   1. It caches the app shell and NOTHING ELSE. `/api/*`, `/health` and the two
      sockets are never passed to respondWith() at all — not "network-only",
      but never seen, so no branch here can store one of their responses even by
      accident. This client's whole design is that it never states a server fact
      it has not just read; a cached API response is that failure with a longer
      half-life.
   2. A navigation is network-first. The cached shell is served on exactly one
      path — a phone with no network — so a bad cache is invisible to an online
      phone, and the browser's own update check on /sw.js keeps running, which
      is what lets sw.kill.js land if this file ever has to be withdrawn.
   3. Every global goes through `self.`. Valid in a worker, and the seam
      src/test/sw-harness.ts hangs a fake scope on.
*/

const VERSION = "v1";
const CACHE = `alfred-shell-${VERSION}`;
const SHELL = "/index.html";
/* Where install records what it precached so activate can drop everything else
   without re-reading index.html. A worker can be killed between the two events,
   so this cannot be a variable. It is never requested by the app: it is not a
   navigation and not under /assets/, so the fetch handler never answers for it. */
const MANIFEST = "/__shell-manifest";

/* Vite content-hashes every asset and writes the entry chunk and the stylesheet
   into index.html. That makes the HTML the precache manifest, which is why this
   worker needs no build step and no plugin. */
function assetsInHtml(html) {
  return [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((match) => match[1]);
}

/* mount_spa (core/channels/spa.py) answers ANY unknown path with index.html and
   a 200, so `response.ok` is not proof that an asset exists: a hashed file a
   deploy has removed comes back as a page of HTML. Storing that under a .js URL
   is how a PWA bricks itself, so the content type decides, not the status. */
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

  const responses = [];
  for (const url of urls) {
    const response = await self.fetch(url, { cache: "reload" });
    /* Throwing aborts the install, which is the right answer: a half-precached
       shell boots to a blank screen offline, and a worker that never installs
       leaves the previous one — or no worker at all — in charge. */
    if (!isAsset(response)) throw new Error(`${url} ${response.status}`);
    responses.push(response);
  }

  await cache.put(SHELL, shell);
  for (let i = 0; i < urls.length; i += 1) await cache.put(urls[i], responses[i]);
  await cache.put(MANIFEST, new Response(JSON.stringify([SHELL, ...urls])));
}

self.addEventListener("install", (event) => {
  event.waitUntil(precache());
});
