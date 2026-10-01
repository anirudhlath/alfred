/* The kill switch. It is a real, shipped, tested worker and not a comment.

   WHAT IT IS FOR
   A bad precache on an installed iOS PWA is very hard for a user to clear:
   there is no address bar, no developer tools and no reliable "clear site
   data" for a home-screen app. The only lever left is the bytes at /sw.js.

   HOW TO FIRE IT
       cp web/public/sw.kill.js web/public/sw.js
   then rebuild and deploy. A real copy over sw.js — not a redirect, not a
   symlink, not an import. Every installed copy of Alfred takes this worker on
   its next update check (the browser runs one on every navigation, and
   src/lib/sw.ts runs one on every return to the foreground), drops every cache,
   unregisters itself, and leaves a plain web page that still works.

   WHY DELETING /sw.js DOES NOT WORK
   mount_spa (core/channels/spa.py:42-49) answers any unknown path with index.html
   and a 200. With sw.js removed, the browser's update check fetches a page of
   HTML, rejects it on MIME type, and the update FAILS — leaving the old worker
   installed and still serving its cache. Deleting the file is the one action
   that looks like a fix and is not.

   WHY IT STOPS
   While these bytes are at /sw.js, every page load installs this worker
   again: main.tsx registers /sw.js on every load, and the registration the
   last copy dropped is gone, so each load makes a new one. A copy that
   reloaded the load it was registered from would therefore never stop — and a
   draft of this file did exactly that, because it claimed the open pages
   before it listed them: in Chromium, 659 fetches of /sw.js in 8 s, each one
   a reload. So it reloads the pages the OLD worker controlled, and only
   those. Activation hands every one of them to this worker before the
   activate event fires, so they are exactly what matchAll() lists; the load
   that registered a copy is controlled by nothing, and is not listed. A copy
   in a fresh registration finds nothing to reload, and only the copy that
   replaced the real sw.js ever reloads anything — each page it was serving,
   once.

   LAST RESORT, if a phone is bricked AND has no network: delete the home-screen
   icon and add it again. Nothing is lost; the session and the conversation are
   on the server.

   AFTERWARDS: restore sw.js from git, ship the fix, and let phones re-register
   on their next launch.
*/
self.addEventListener("install", () => {
  /* The opposite of sw.js, deliberately. This worker exists for the case where
     the app cannot be driven, so there is nobody to ask for permission. */
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      /* First, and with no options: the defaults are the window clients this
         worker controls, which here are the pages the old worker controlled —
         see WHY IT STOPS. And no claim(), here or anywhere. Those pages are
         this worker's already. All a claim adds is the pages nothing controls
         — the loop's own engine, put before this line — and it leaves them
         controlled by a worker on its way out, whose registration a later
         register("/sw.js") then revives instead of replacing: in Chromium,
         with two pages open and the claim put after this line, the run ended
         with this worker still installed. */
      const stale = await self.clients.matchAll();
      /* Every cache, and all of them before the unregistration, never after.
         A registration no page is using — the copy each later load installs,
         which is the second chance at a cache an earlier copy could not
         delete — is cleared as soon as unregister() resolves, and Chromium
         stops this worker there: with the unregistration moved first, such a
         copy left every cache it found. Caught per delete: one cache that
         will not go must not keep the others, the unregistration or the
         reloads. */
      for (const name of await self.caches.keys()) await self.caches.delete(name).catch(() => {});
      /* Awaited, and before any reload. A navigation that starts while the
         registration exists is matched to it, and Chromium holds it until
         this worker has activated, which waits on this function. Measured
         with the reloads started before the unregistration had finished: a
         page with nothing else to reload it was never reloaded, and this
         worker stayed "activating"; and the app's page, which sw.ts was
         reloading anyway, revived the registration with its own
         register("/sw.js"). */
      await self.registration.unregister();
      /* Each caught on its own, because a rejection here says little. The
         page's own reload is already under way — activation fired
         `controllerchange` at it and src/lib/sw.ts reloads on that — and
         Chromium holds that one, lets this navigation replace it, and rejects
         this promise all the same: "Cannot navigate to URL". A page that has
         closed rejects too. None of it may cost another page its reload. */
      await Promise.all(stale.map((client) => client.navigate(client.url).catch(() => {})));
    })(),
  );
});

/* No fetch handler at all. Nothing this worker controls is served from storage. */
