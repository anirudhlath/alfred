/* Alfred's service worker. Hand-written on purpose; the reasoning is in
   docs/superpowers/plans/2026-09-16-pwa-phase4-install-and-offline.md and the
   summary is in web/README.md, "The service worker".

   Three rules, and everything in the file is under them.

   1. It caches the app shell and NOTHING ELSE. `/api/*`, `/health` and the two
      sockets are never fetched here, never stored, and never passed to
      respondWith() at all: not "network-only", but never seen, so no branch
      here can store one of their responses even by accident. `isRefused` is
      where that is decided, once, for every branch. This client's whole design
      is that it never states a server fact it has not just read; a cached API
      response is that failure with a longer half-life.
   2. A navigation is network-first. The cached shell is served on exactly one
      condition — the network did not answer inside the deadline — so a cache
      this worker has poisoned is reachable only by a phone that is offline or
      cannot reach this origin in ten seconds, and never by one whose network is
      working; and the browser's own update check on /sw.js keeps running, which
      is what lets sw.kill.js land if this file ever has to be withdrawn.
   3. Every global goes through `self.`. Valid in a worker, and the seam
      src/test/sw-harness.ts hangs a fake scope on. eslint.config.js's
      `no-restricted-globals` block is what enforces it; without that rule
      `globals.serviceworker` makes a bare `caches` or `fetch` perfectly legal.
*/

const VERSION = "v1";
const CACHE = `alfred-shell-${VERSION}`;
const SHELL = "/index.html";
/* Where install records what it precached — the shell, the files it names and
   the fonts install cached for it — so that a collection knows what install's
   deploy needs without fetching anything. A worker can be killed between
   install and activate, and a collection reads the record long after both, so
   this cannot be a variable. The app never asks for it: it is not a
   navigation and not under /assets/, the only two things this worker's routing
   will ever answer. */
const MANIFEST = "/__shell-manifest";
/* The one path prefix this worker may store under, named rather than spelled
   inline because it is the whole of the refusal isAssetUrl makes. */
const ASSETS = "/assets/";
/* Every network call here gets a deadline — the request and every wait on the
   body that follows it alike, install and runtime both — and that is a
   different guarantee from the `.catch()` in the font loop. A catch covers a
   fetch that REJECTS; it does nothing at all for one that never settles, which
   is what a black-holed route does: it accepts the connection and answers
   nothing. A body is the same hazard one layer in, and easy to miss because the
   request looks finished — fetch() resolves on the HEADERS, so a route that
   answers 200 and then goes quiet leaves `await response.text()` pending for
   ever with nothing left to reject. And Cache.put is a body read too, the
   easiest of all to miss: it reads what it is handed to the end before it
   writes, which is why every network body this file stores or reads goes
   through `whole` first. precache runs inside waitUntil(), so any of these hangs holds
   the whole install open until the browser gives up on it, and the phone is
   left with no worker: the outcome the font loop's catch exists to avoid,
   reached through the other door. A hung NAVIGATION is the same judgement one
   layer out: a request that never answers is indistinguishable from being
   offline, and the fallback for being offline is the cached shell. An
   installed PWA is where that matters most — a standalone window has no browser
   chrome to escape a hang with, so "wait forever" is not a state the user can
   leave.

   Per WAIT, not per call and not per install: a request has this long to
   answer, and each piece of its body this long to follow the last. So a stall
   is noticed this long after the last byte, wherever it happens, and a phone on
   a slow link that is still working is waited for while it keeps working — up
   to CEILING_MS, below, which no working link reaches. A total of THIS length
   on the body is what failed this build's 414 kB entry chunk at 32 KiB/s,
   12.6 s of steady progress, at 10.0 s — measured in Chromium, on a first
   visit, and on every visit after it, since a failed install leaves no worker
   and the next load starts the whole precache over.
   What a stall costs is this, once: in the shell or a chunk it fails the
   install there and then, and in a font it costs that font and the install
   carries on. */
const TIMEOUT_MS = 10000;

/* And a ceiling on each body as a whole, from its headers to its last byte,
   because a limit on silence never ends a body that is never silent: a link
   that sends a byte every few seconds is never quiet for ten. Measured in
   Chromium with no DevTools attached to the worker (DevTools keeps an
   inspected worker alive, so a lifetime measured under it is not a phone's),
   a chunk sending a byte every five seconds held an install "installing"
   until the browser's own limit on the event failed it, at 300 s; and as a
   new deploy's chunk, read by refresh() inside a navigation's waitUntil, it
   held its connection open, and the navigation's event with it, for 309 s
   after the page had been served.

   Two minutes, and generous on purpose. The largest body this worker reads is
   the entry chunk, about 415 kB, and it reaches the ceiling only on a link
   slower than 3.5 kB/s — about 28 kbit/s, sustained from the first byte to
   the last: a GPRS-class link, on which the page's own load of that chunk
   takes two minutes as well. Anything faster finishes inside it. And it ends
   well inside the browser's 300 s, so a body that will not finish is let go
   here — released, and turned into the same rejection a stall is — rather
   than held until the browser tears the event down. It is per body, not per
   event, and an event reads its bodies one after another, so an event whose
   every body crawled could still meet the browser's limit first. What it
   costs, on a link that slow: the install fails, as it does on a stall, and a
   refresh keeps the shell it has. That phone loses a new offline copy and
   never a page: a copy of the page's own response is the worker's to give up,
   and the page reads its own to the end, as it would with no worker at all. */
const CEILING_MS = 120000;

/* Two shapes of deadline, and which one a call gets is not a style choice.

   This one RACES, and it is for the two waits a signal cannot end. A
   navigation is one: it cannot be given a signal at all, for the reason
   written under `timed`, so the loser is a fetch left running behind the race,
   one per navigation — released the moment its headers arrive (see
   navigate()); what outlives the race is a request still awaiting headers,
   bounded by the browser's own network timeout. The alternative costs the
   page's Request, which is worse. The other is the
   reading of a body, inside `whole`: one race per piece, and one over all of
   them with CEILING_MS for its length — and there the loser is not left
   running: `whole` cancels what it was reading the moment a race is lost, and
   why that and not an abort is written beside it. */
function deadlined(work, ms = TIMEOUT_MS) {
  let deadline;
  const expiry = new Promise((_, reject) => {
    deadline = self.setTimeout(() => reject(new Error("deadline exceeded")), ms);
  });
  /* Cleared on either outcome, which is also what keeps the loser quiet: an
     expiry that never fires never rejects, and Promise.race has already
     attached its own handler to a `work` that loses, so a late rejection from
     whatever lost is handled rather than unhandled. */
  return Promise.race([work, expiry]).finally(() => self.clearTimeout(deadline));
}

/* Lets go of a response this file has decided against, by cancelling its body
   unread. A body nobody reads and nobody cancels is not finished with: the
   request behind it stays open, and the connection with it, for as long as the
   server keeps sending, and for ever on one that stalls, with nothing left in
   this file to end it. Measured in Chromium with no DevTools attached to the
   worker (DevTools keeps an inspected worker alive), a font and a deploy's
   chunk turned down unread each held their connection until the browser
   stopped the idle worker, 30 s on.

   So every response turned down on its headers comes through here — and so
   does a navigation's that arrives after the shell was served — and so does
   every copy of one that is turned down unread or that `whole` gives up on. A
   cancel stops at a tee (see `whole`): released here, a copy of the PAGE's
   response frees only the worker's branch, and the request ends when the page
   is done with its own. Not awaited — nothing here waits for the network to
   let go — and its rejection swallowed: cancelling a body that has already
   errored rejects with that error, which whoever met it first has handled. */
function release(response) {
  if (response.body) response.body.cancel().catch(() => {});
}

/* Resolves with `response` once every byte of its body is here, no piece of it
   having taken longer than the deadline to follow the last and the whole of it
   inside the ceiling — the precondition for handing a network response to
   Cache.put, and for reading one at all.

   A put reads the body it is given to the END before it writes anything, so a
   put of a network response is a body read in everything but name, and one
   whose headers arrived and whose bytes stall leaves the put pending for ever.
   Not hypothetical: measured in Chromium with no DevTools attached to the
   worker, a shell naming a chunk whose body stalled held the install
   "installing" on a put nothing had bounded, until the browser's own limit on
   the event failed it at 300 s. A put takes no signal, and racing the put
   itself would leave it in flight, free to land later in a cache that has
   since decided against it.

   So a CLONE is read to the end and the original is what gets put. clone()
   tees the stream, and the branch nobody has read yet is handed every chunk
   the other one pulls: once the clone is whole, so is the original, in memory,
   and the put that reads it never touches the network. The price is the body
   held twice while that happens — 414 kB for this build's entry chunk.

   The deadline is on each READ, not on the body, and that is the whole of the
   difference between a stall and a slow link. One deadline over the whole
   body, as this once had, cannot tell them apart: it failed a 414 kB chunk
   arriving steadily at 32 KiB/s (see TIMEOUT_MS). And every read, the first
   included — a body that never sends a byte is a stall too — through to the
   one that finds the end: a body that sends some and then stops is a stall
   as well, and a read of one piece would have called it whole and handed the
   put a stream that never finishes. Over all the reads is the ceiling, armed
   when this starts — the headers are in by then — which no working link
   reaches and a body still trickling at the end of it does (see CEILING_MS).

   A body that loses either race is CANCELLED, not left running: the clone's
   reader, and the original's body through `release`, which is every branch of
   the stream this function holds. Not awaited, because a branch's cancel
   settles only once its twin is cancelled too, and nothing here needs to wait
   for the network to be let go. Cancelled, and not aborted: aborting `timed`'s
   controller after the headers would release the read as well, by erroring
   the body — but it errors EVERY branch of it, and the request behind
   `response` is not always the worker's. Every caller hands this a copy only
   the worker reads, and twice the request behind that copy is the PAGE's: the
   asset branch's write, and a refresh's read of the HTML its navigation was
   served (which has no controller to abort in the first place). An abort there
   would end the page's own read of the same bytes — the write reaching the
   page, which is the one thing the asset branch is shaped to prevent. A cancel
   stops at the tee: it reaches the network only when nothing else is reading,
   so it lets go of install's and refresh()'s own fetches, and never of the
   page's. */
async function whole(response) {
  const reader = response.clone().body.getReader();
  const end = async () => {
    for (;;) {
      const { done } = await deadlined(reader.read());
      if (done) return response;
    }
  };
  try {
    return await deadlined(end(), CEILING_MS);
  } catch (error) {
    reader.cancel(error).catch(() => {});
    release(response);
    throw error;
  }
}

/* This one ABORTS, so the request is really cancelled rather than orphaned, and
   it is how install and the asset branch fetch. It bounds the wait for the
   headers and is cleared when they arrive; the body after them is `whole`'s,
   a deadline per piece.

   Takes what fetch() takes, so a handler can forward the page's own Request —
   which carries its mode, its credentials, its redirect mode and its headers;
   re-fetching `request.url` instead quietly turns `redirect: "manual"` into
   `"follow"` and `credentials: "omit"` into `"same-origin"` and drops the
   headers, and can hand the page a redirected response it must not have.
   (Measured in node: `new Request(req, { cache: "reload" })` keeps all four,
   `new Request(req.url, ...)` keeps none.)

   Keeps those four, and not everything. Any init that is not empty makes
   fetch() rebuild the Request, and the Fetch Standard's constructor resets a
   handful of things when it does. For an asset the one that shows is the
   referrer, reset to "client" — the worker itself: measured in Chromium, a
   fetch that went through here arrives with `Referer: …/sw.js` where the page's
   own request named the page. Harmless for a same-origin hashed file nothing
   on the server reads it for; not harmless everywhere, which is the next
   paragraph.

   Why a NAVIGATION cannot come through here. Three more of those resets fire on
   the same rebuild: a mode of "navigate" becomes "same-origin", the
   reload-navigation flag is unset, and the history-navigation flag is unset. So
   the act of attaching a signal demotes the page's navigation to an ordinary
   fetch — and the demotion is invisible from a test, because `mode: "navigate"`
   cannot be constructed outside the browser's own navigation machinery at all
   (undici throws `invalid request mode navigate`, which is why the harness's
   requests are plain objects). navigate() therefore hands `self.fetch` the
   event's Request and no init, and takes its deadline from `deadlined` above. */
function timed(input, init) {
  const controller = new self.AbortController();
  const deadline = self.setTimeout(() => controller.abort(), TIMEOUT_MS);
  return self
    .fetch(input, { ...init, signal: controller.signal })
    .finally(() => self.clearTimeout(deadline));
}

/* How install fetches, and only install.

   `cache: "reload"` — the HTTP cache is the only thing that could hand a fresh
   worker the bytes a deploy just replaced. The runtime handlers deliberately do
   NOT ask for it: a hashed asset is immutable, so the HTTP cache is the right
   answer there and reloading it would spend a phone's bytes re-fetching
   something that cannot have changed; and a navigation is the page's own
   request, which is not this worker's to rewrite. */
function fetchFresh(url) {
  return timed(url, { cache: "reload" });
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
   cache and the asset branch's lookup. */
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
   a collection prunes by comparing that record against the cache's own keys,
   so a font recorded with the stylesheet's spacing still on it is a font the
   next collection deletes.

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
   `application/json` body — `{"detail":"Not Found"}` — and it is the status half
   that catches it. tests/core/channels/test_spa.py:155-160 pins that 404 server
   side.

   The content-type half is defence in depth for the day that stops being true:
   if the mount is ever removed the fallback answers any unknown path with
   index.html and a 200, and a reverse proxy — or anything else answering in
   this origin's place — can serve a page of its own under a hashed URL at any
   time. (Not a captive portal: this origin is https, where it cannot intercept
   at all.) Storing either under a .js URL is how a PWA bricks itself, so both
   halves stay. */
/* 200 and not `response.ok`, which is 200–299. None of the other eight is a
   representation of the thing that was asked for, and they fail differently
   enough to be worth naming. A 201, 202 or 203 answers about the request rather
   than with the file — a 203 in particular says a proxy has TRANSFORMED the
   body, which is the one thing a content-hashed URL promises has not happened.
   A 204 or 205 has no body at all and would cache an empty chunk, and the Cache
   API is perfectly happy to store one. A 206 is a PARTIAL body, which the Cache
   API rejects a put of outright — a TypeError out of a line that reads as
   though it cannot fail, on the one path (an asset the precache missed) where
   the page is waiting for the answer; nothing here sends a Range and isRefused
   turns away a request that carries one, so a 206 means the server volunteered
   one, which is a disagreement to decline rather than to store.

   Declined here rather than left to the `.catch()` on the put, which both
   handlers now have: a write that cannot succeed is not attempted at all. The
   catch is there for the write that fails on a phone this worker cannot inspect
   — a full disk — not as cover for one this file can see coming. None of these
   statuses is reachable from this app today, and every one of them is one proxy
   away. */
function isStored(response) {
  return response.status === 200;
}

function isAsset(response) {
  return isStored(response) && !(response.headers.get("content-type") || "").includes("text/html");
}

function isShell(response) {
  return isStored(response) && (response.headers.get("content-type") || "").includes("text/html");
}

async function precache() {
  const cache = await self.caches.open(CACHE);

  const shell = await fetchFresh(SHELL);
  if (!isShell(shell)) {
    release(shell);
    throw new Error(`shell ${shell.status}`);
  }
  /* Read `whole` like every body stored below, before any of it is read for
     what it names: fetch() resolves on the headers, so a shell whose body never
     finishes arriving would hang install here, past every deadline above, with
     the phone left on whatever worker it had. Then read again, from memory. */
  const urls = assetsInHtml(await (await whole(shell)).clone().text());

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
       that fires here arrives as a rejection and lands in the same place,
       whether it is the request's or the body's: `whole` is what makes a body
       that never finishes part of this check rather than a put that never
       returns, three paragraphs down, after the writing has started. A
       response turned down here is released first, as every one this file
       turns down is (see `release`). */
    if (!isAsset(response)) {
      release(response);
      throw new Error(`${url} ${response.status}`);
    }
    responses.push(await whole(response));
  }

  /* The stylesheet only. A bundle is not a list of what the app needs offline:
     any library that injects styles carries @font-face rules inside it, and
     reading 414 kB of minified JavaScript into a string to regex it is a cost
     with no return. (This build's chunk does contain the text `url(` twice, but
     both are a zero-argument method of that name in the WebSocket client, so
     the regex captures nothing from it — the rule stands on its own, not on
     that measurement.)

     Read with no deadline of its own, and that is not an oversight: `whole`
     has already read this body to the end, every piece of it inside one, so
     every byte of it is in memory and there is nothing left to wait on. */
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
     same reason — a manifest listing something twice is what a collection
     prunes against. */
  const fonts = [...new Set(named)].filter((url) => !urls.includes(url));

  /* Unlike a missing chunk above, a missing font does NOT fail the install. The
     app is legible in the system stack, and an install aborted over a typeface
     leaves the phone with no worker at all — the worse of the two, and one that
     only sw.js's own bytes changing would undo. All four failures are declined
     the same way: no answer (the fetch rejects), no answer ever (the deadline
     fires, which arrives as a rejection too), a wrong answer (a proxy's page
     under a .woff2 URL, released unread) and an answer that never finishes (a
     body that stalls, or is still trickling at the ceiling, which `whole` turns
     into the same rejection). Anything skipped is still
     picked up cache-first the first time it is asked for — by the fetch
     handler's asset branch, which stores what it fetched under the same
     pathname key — and kept until the next collection, which takes what
     neither the record nor the cached shell names (see `collect`). */
  const cached = [];
  for (const url of fonts) {
    const response = await fetchFresh(url)
      .then((fetched) => {
        if (isAsset(fetched)) return whole(fetched);
        release(fetched);
        return null;
      })
      .catch(() => null);
    if (response) cached.push([url, response]);
  }

  /* Nothing is written until everything has been validated — every body
     included, which is what `whole` is for — so a shell that fails on its
     second asset leaves the previous worker's cache untouched rather than half
     replaced. That cache is the one the previous worker is still serving from,
     and it is why the shell goes in LAST, after everything it names, the order
     refresh() publishes in and for the same reason: every body is in memory by
     now, so the one way a put can still fail is storage — a phone that fills up
     part way through — and a new shell written first and its chunk refused
     after it is a white screen offline, left in the live cache by an install
     that failed. This way round a failure part way leaves the shell that was
     there and the assets it names; the new entries written before it are
     named by nothing, and the next collection takes them. */
  for (let i = 0; i < urls.length; i += 1) await cache.put(urls[i], responses[i]);
  for (const [url, response] of cached) await cache.put(url, response);
  await cache.put(SHELL, shell);
  /* The record last of all, once the shell's write has landed: a record is
     this install's claim to have FINISHED. Every collection prunes by it, and
     the worker that reads it need not be this one — the active worker collects
     after each refresh, and a worker already waiting when this install fails
     stays waiting, and runs the same purge over this same cache when the page
     posts SKIP_WAITING. Written before a shell that is then refused, the record
     would outlive the install that wrote it, and the next collection would
     delete the fonts of the shell still there, which only the record they came
     in with names. The fonts are listed for the same reason the rest is: a
     collection takes what neither this record nor the cached shell names, and
     a shell names no font, so a font cached but unlisted is deleted before it
     is served. */
  await cache.put(
    MANIFEST,
    new Response(JSON.stringify([SHELL, ...urls, ...cached.map(([url]) => url)])),
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(precache());
});

/* The recovery path from a bad cache, run at activate. Two purges, because
   there are two ways stale bytes accumulate: between versions, which only this
   reaches, and within one, which `collect` reaches here and after every
   refresh() that publishes a shell. */
async function purge() {
  /* Between versions: bump VERSION and the next activate takes the whole
     previous world with it. The `alfred-` prefix is not filtered on — anything
     this origin holds that is not the current cache is not ours to keep, and
     that includes whatever a previous experiment (or a Workbox trial) left.
     Awaited, not fired off: an unawaited delete is outside waitUntil, and the
     browser may stop the worker with the cache still there. Caught per delete,
     too: this is the recovery path from a bad cache, and one transient failure
     must not cost the caches after it, the collection below, or the claim. */
  for (const name of await self.caches.keys()) {
    if (name !== CACHE) await self.caches.delete(name).catch(() => {});
  }
  await collect(await self.caches.open(CACHE));
}

/* Within a version: every entry that neither install's record nor the shell
   now in the cache names — the union is spelled out below. Asset URLs are
   content-hashed, so a deploy's worth of them is dead the moment nothing names
   them — and VERSION is bumped by hand, so for every deploy that does not bump
   it this is the ONLY thing that reaches what an earlier deploy left here. On
   iOS an origin over its storage budget is evicted whole rather than trimmed.

   Run by purge() at activate, and by refresh() once it has published a shell
   while no worker is installing. Activate alone was not enough: an ordinary
   deploy changes no byte of this file, so it installs nothing and activates
   nothing, and each refresh() left the shell it replaced, and that shell's
   files, named by nothing until sw.js next changed. Measured in Chromium
   against the real server, the cache grew by an entry chunk and a stylesheet
   on every web deploy, with no limit. Now each collection leaves two deploys
   at most: the record's, and the one the cached shell names.

   It also reaches, by the same rule, whatever the fetch handler stores at
   runtime, and the rule cuts both ways there. An asset cached on a cache-first
   miss is in no record, so it is kept only if the cached shell names it — the
   chunks a refresh published with its shell are, which is the point of the
   union — and anything else the next collection takes, which is usually the
   next online navigation's: a font install declined and the page fetched
   later, a font only a newer deploy's stylesheet names, a chunk a build splits
   out and loads on demand. That is the intended reading — what the record and
   the shell name is what the phone needs offline, any other runtime entry is
   re-fetchable, and the alternative is a cache with no ceiling — but it is a
   decision the fetch handler inherits rather than makes, so it is written down
   here.

   Note the window an install leaves: the new worker precaches into the cache
   the old one is still serving from, so from the end of install to activate
   the cached shell is the new HTML. Its assets are already cached by then —
   install writes the shell after everything it names — and the only reader of
   a cached shell is a navigation that did not get its answer from the network:
   a phone that is offline, or that cannot reach this origin inside the
   deadline. */
async function collect(cache) {
  /* Snapshotted BEFORE the record is read, and the order is the whole point. A
     worker installing concurrently (the page re-checks for an update on every
     return to the foreground) writes its entries first and its record last. Read
     the record first and any entry it wrote after that read but before this
     snapshot is deleted — and its install has already resolved, so it never
     refetches. This way round, an install that completes between the two reads
     is read from its own record, and entries written after the snapshot are not
     in it to delete. It narrows the race; it does not close it. Closing it needs
     a lock two workers can share, which a service worker does not have; what
     refresh() has instead is a look at the registration first (see the end of
     refresh()). */
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
       spelling across the manifest, the cache and the asset branch's lookup
       — but comparing on the pathname alone would keep a cross-origin entry
       that merely shares one, and a poisoned CDN copy of the entry chunk is
       exactly the thing purge() is the recovery from. Resolved against the
       scope for the same reason isAssetUrl does: one notion of "us" in this
       file. */
    keep = new Set([MANIFEST, ...record].map((path) => new URL(path, scopeOrigin()).href));
  } catch {
    /* A body that is not JSON. /__shell-manifest is not a real endpoint —
       mount_spa answers any unknown path with index.html and a 200 — so a page
       of HTML is what the network would put under that key if anything ever
       fetched it. Uncaught at activate, the rejection would take the claim
       down with it, AFTER purge() had already deleted the old world. */
    return;
  }

  /* The union, and the second half of it is not optional. install's record says
     what install fetched; the shell in the cache says what the phone will
     actually render offline, and after a runtime refresh those are different
     documents. refresh() writes a newer deploy's chunks and then its HTML, and
     neither is in any record — so pruning on the record alone deletes the
     assets of the very page the next offline launch boots, while keeping the
     page itself (its key, /index.html, is in the record) and leaving a shell
     that renders a blank screen. That is the failure this whole file exists to
     prevent, arriving from the inside.

     Read out of the cache, not off the network: the question is what the
     entries on disk are for, not what the server is serving now — and after a
     refresh, the shell read back is the one it has just published. The cost is
     the record's deploy, kept beside the latest for as long as install's record
     stands, which is until sw.js next changes: two deploys at most, and one
     when the record and the shell agree. */
  try {
    const shell = await cache.match(SHELL);
    /* Not cloned: Cache.match hands back a new Response every call, so this body
       is this function's own. */
    const named = shell ? assetsInHtml(await shell.text()) : [];
    for (const url of named) keep.add(new URL(url, scopeOrigin()).href);
  } catch {
    /* The same reading as an unreadable record one paragraph up: a shell that
       will not read is not a licence to guess at what it named. Keep
       everything, and let the next install rewrite both. */
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

self.addEventListener("message", (event) => {
  /* The only message this worker takes, and the only way it ever activates
     early. Its counterpart is applyUpdate() in src/lib/sw.ts; between them they
     are how a CHANGED sw.js reaches an installed app that is still open.
     Without them the new worker waits for every page the old worker controls
     to close, and a reload does not count: the old page stays until the new
     one's response has arrived, so the two overlap and the old worker never
     runs out of pages.

     A changed sw.js, and not a new build. VERSION is bumped by hand and this
     file is byte-identical across most deploys, so most deploys install
     nothing and leave nothing waiting: they are taken at the next navigation,
     by navigate(), and never pass through here.

     `event.data &&` first, because a page can post null and `.type` read off
     it is a TypeError inside the listener. Anything else is ignored. */
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

/* The paths this worker never answers, named rather than spelled inline for the
   reason ASSETS is named: this is the whole of rule 1, and a list that is read
   is a list that can be checked against the server's.

   Two match kinds, because the server has two. Exact, then "under", so that the
   anchoring is visible rather than inferred from four inline `startsWith`es.

   THE SAME LIST LIVES IN TWO OTHER PLACES AND NEITHER AGREES WITH THIS ONE.
   core/channels/spa.py:24-25 has ("api/", "ws") as prefixes plus {"health"} —
   which is what makes /ws-telemetry a 404 rather than an app path — and
   web/vite.config.ts's dev proxy has /api, /health and /ws. Unifying the three
   is not this file's job and is not attempted here.

   The docs routes are in neither of those: they are FastAPI's own defaults, and
   create_app() (core/channels/web_server.py:447) overrides none of them, which
   src/test/sw-server-routes.test.ts pins. They matter because they are 200
   text/html and outside /api/ — so without them one navigation to /docs writes
   Swagger UI into the shell cache under isShell(), and every offline launch
   renders Swagger until the next online navigation. /docs/oauth2-redirect is
   listed too; it is HTML from the same source. /openapi.json is JSON rather than
   HTML and is listed for the same reason the sockets are: it is not this app.

   The anchoring is not uniform in what it buys, and one reason covering all of
   it would be wrong. For /api/ and /health it is offline behaviour AND
   correctness: <Route path="*"> plus mount_spa means /apifoo and /healthz are
   really served the app (measured: 200 text/html), so widening either to a bare
   prefix hands a phone with no network a browser error page instead of the app.
   For /ws it buys only that the offline case stays coherent — spa.py's
   _NON_SPA_PREFIXES is ("api/", "ws"), so every /ws* path 404s, /ws-telemetry
   included (measured), and over-refusing one costs nothing an online phone can
   see. Anchoring it is still right; it is simply not paid for by the same
   argument, and /ws-telemetry is a fixture rather than a route.

   /ws and /ws/telemetry cannot reach a fetch handler at all — a WebSocket
   upgrade is not a fetch. They are named so nobody deletes a guard they think is
   dead, and so nobody adds a caching branch they think is missing. */
const NEVER = ["/health", "/ws", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"];
const NEVER_UNDER = ["/api/", "/ws/"];

function isNeverAnswered(pathname) {
  return NEVER.includes(pathname) || NEVER_UNDER.some((prefix) => pathname.startsWith(prefix));
}

/* Rule 1 of the header, and the whole of it in one predicate, so that every
   branch the fetch handler grows is downstream of one refusal rather than
   beside a chain of early returns that the next branch is free to precede.

   True means: respondWith() is never called, so nothing below can store this
   response, serve it, or see it. Stronger than "network-only", and it is the
   one thing in this file worth over-testing — a cached API response is this
   client stating a server fact it has not just read, with a half-life.

   False does NOT mean "answered". /manifest.json, /icon.svg and
   /apple-touch-icon.png are not refused and are not answered either: they fall
   out of the branches below unhandled, which is a different thing.

   A function rather than inline tests because a refusal that cannot be called
   cannot be tested on its own, and the half of this guard that matters most is
   the paths it must NOT refuse — invisible from outside, where "refused" and
   "unhandled" are both an untouched request. src/test/sw-harness.ts reaches it
   by name. */
function isRefused(request) {
  /* A POST is never a shell request, and the Cache API cannot store one anyway. */
  if (request.method !== "GET") return true;

  /* A Range asks for part of a body and every branch below answers with a whole
     one — a different answer to a different question, and the canonical way a
     cache-first worker stalls a media element in Safari. Nothing in this client
     sends one: there is no <audio> or <video> element in src/, and a spoken
     reply arrives over /ws as base64 and is decoded through an AudioContext
     (src/lib/audio.ts). Named for the reason /ws is named — so the branch that
     would break under one is never written. */
  if (request.headers.get("range")) return true;

  const url = new URL(request.url);

  /* "Same origin" is two questions and the second is easy to miss: a blob: URL's
     origin is the origin INSIDE it, so blob:https://this-host/<uuid> compares
     EQUAL to ours below while its "pathname" is a whole URL and nothing this
     worker could fetch or key a cache on. A service worker is only ever
     dispatched for http(s), so this is unreachable — but the comparison under it
     reads as if it were sufficient, and it is not. */
  if (url.protocol !== "https:" && url.protocol !== "http:") return true;

  /* Nothing in this client loads a cross-origin URL, and if one ever appears it
     is not the shell and must not land in the shell's cache. On the origin, and
     never on the href: credentials are not part of an origin, so
     https://this-host@evil.invalid/ is evil.invalid and is refused here, while
     https://user@this-host/assets/x is us and is not. */
  if (url.origin !== scopeOrigin()) return true;

  /* The pathname the SERVER will route on, not the one the URL carries. uvicorn
     unquote()s the whole path before Starlette sees it, so /%68ealth is /health
     to the server and something else entirely to `startsWith`. Measured against
     the live app: /%68ealth and /healt%68 both return the real health JSON, and
     /%61pi/admin/overview and /ap%69/admin/overview both return the real API's
     401. One escaped character defeated the entire list.

     Only the decoded form is judged, and adding the raw one back would be the
     %2f mistake again: every entry in both lists is escape-free, and decoding
     rewrites nothing but escapes, so a raw pathname the lists match decodes to
     one they still match. A raw term could refuse nothing this one does not,
     and no fixture can tell the two apart — which is how it was found, by a
     mutant that deleted it and killed no test at all.

     A pathname that cannot be decoded at all is refused rather than guessed at:
     decodeURIComponent throws on %zz, on a lone %, and on bytes that are not
     UTF-8, while Python's unquote leaves the first two as literal text and
     replaces the third. That is precisely a path this worker and the server do
     not agree about, and the app it would be guessing with is the only offline
     copy the phone has. (The server answers /%zz with the shell, so the cost is
     one malformed URL that boots online only.)

     Dot segments last, and not for the reason first written here. Measured on
     the live app: /%2e%2e%2fapi/admin/overview is 200 text/html — the shell —
     because Starlette does not normalise dot segments, so the unroutable path
     falls through to mount_spa. It does not reach the API, and this rule does
     not rest on that staying true. An ASGI server that DID normalise would
     route /assets/..%2fapi/x to the real API while the raw pathname still
     begins "/assets/", which is the prefix the asset branch below reads: a 401
     cached as a hashed asset. Today that one is a 404 from the /assets mount.

     `new URL` has already normalised /x/../api/y and /%2e%2e/api/y down to
     /api/y before anything here reads them; only an encoded separator keeps a
     dot segment alive this far. A lone "." is held to the same rule for the
     same reason, and costs a shell for /%2e%2fapi/y, which is 200 text/html
     today. The %2f special case this replaced is gone rather than kept beside
     it: a rule with a general form must not sit next to an instance of itself,
     and the instance also refused /%2Fhealth, which is the app. */
  let decoded;
  try {
    decoded = decodeURIComponent(url.pathname);
  } catch {
    return true;
  }
  if (decoded.split("/").some((segment) => segment === "." || segment === "..")) return true;

  return isNeverAnswered(decoded);
}

/* Every cache read and every cache write the two runtime handlers make goes
   through here, and `undefined` is a first-class answer: the CacheStorage is
   unavailable, and this worker has nothing to offer that the browser could not
   do better on its own.

   It is not a theoretical failure. Safari drops the whole store under storage
   pressure and in some private modes, and an installed PWA on iOS is evicted
   whole rather than trimmed when the origin goes over budget. Before this was
   caught, `caches.open()` sat outside the try in both handlers, so an origin
   whose storage had gone answered a working network with the browser's
   network-error page for a navigation and a hard module-load failure for a
   chunk — a worker actively making a healthy phone worse. The standard for this
   whole file is one line: a worker that cannot use its cache must behave like
   no worker at all. */
async function openCache() {
  try {
    return await self.caches.open(CACHE);
  } catch {
    return undefined;
  }
}

/* One cached entry, or undefined for any reason at all — a miss, a store that
   will not open, a read that fails. Every caller's next move is the same for
   all three. */
async function cached(key) {
  const cache = await openCache();
  if (!cache) return undefined;
  return cache.match(key).catch(() => undefined);
}

/* Publishing a shell means publishing the assets it names, or it means
   publishing a white screen.

   sw.js is byte-identical across most deploys — VERSION is bumped by hand — so
   a deploy runs no new install and no new activate: this function is the ONLY
   thing that ever updates the offline copy of the app in between. Write the new
   HTML on its own and the cache holds a shell naming hashes that are not in it;
   asset() propagates a network failure by design, so the next cold launch with
   no network is a blank page rather than the old app. Lose the network between
   the navigation and the page's own asset requests — a tunnel, a backgrounded
   app, a rollout that lands mid-flight — and that is the state it stays in.

   So: fetch what the new HTML names, read each body whole, put those first,
   and publish the shell last. If any of it fails — a body that stalls
   included — the old shell stays, whole. An offline copy that is
   self-consistent and one deploy old beats a newer one that cannot boot.

   Runs in waitUntil() rather than before the response, so a deploy's first
   navigation is not blocked on re-fetching the chunk the page is about to ask
   for anyway. Nothing here can affect what the page was served: the caller
   swallows every failure, which is the whole of the difference between a phone
   that is full and a phone that is offline. Treating the first as the second is
   how a failed write serves an ONLINE phone its cached shell — decision 8
   inverted by a `catch` one line too wide.

   What the entries this writes cost, and what collects them. collect() keeps
   the union of install's record and what the cached shell NAMES, so a shell
   published here and the chunks it names are kept or dropped together; they
   are never pruned apart. Once this has published its shell, the one it
   replaced names nothing, and the collection at the end of this function takes
   that shell's chunks unless the record names them. A refresh that stops in
   the middle publishes nothing and collects nothing: the order below is assets
   first and shell last, so a phone that fills up part way through has written
   new chunks and not the HTML that names them, and those are orphans — in no
   record and named by no cached shell. Not dead weight while they last: the
   asset branch serves them, cache-first, to any page whose HTML names them,
   beginning with the online page whose navigation started this refresh; and
   the next online navigation to that deploy finds them cached and has only
   the HTML left to publish. The next collection keeps them if the shell it
   finds names them and takes them if not, so nothing this writes outlives it
   unnamed: what this function fetches is what the HTML NAMES — the entry
   chunk and the stylesheet — and a collection leaves two deploys at most. */
async function refresh(response) {
  /* isShell, and the two halves buy different things. The status half stops an
     ERROR page becoming the app's offline face: nginx serving a stale cached
     copy under `proxy_cache_use_stale` answers 502 with the app's own HTML, and
     stored here that is what every offline launch renders until the next
     successful one. The content-type half is the only thing this buys over a
     bare `response.ok` — a 200 that is not HTML, which is a host answering with
     something that is not a page at all.

     Not, as this comment once said, a captive portal: a portal answers 200
     text/html, which passes both halves, and over https it cannot intercept in
     the first place. The refusal below is what turns its page away, because it
     names no hashed asset.

     Either way out, before a byte of it is read, the copy is released: it is a
     branch of the body the page is reading, and one left alone keeps the
     request open after the page has let go of its own (see `release`). */
  if (!isShell(response)) {
    release(response);
    return;
  }
  const cache = await openCache();
  if (!cache) {
    release(response);
    return;
  }

  /* `whole` for the reason the fetch had a deadline: a 200 whose body stalls
     half way leaves this pending for ever, and this one runs inside the
     navigation's own waitUntil — the event stays open, holding the worker
     awake, for a page the user was served long ago. With `whole` that is ten
     seconds of silence at most, and two minutes in all for a body that keeps
     trickling (CEILING_MS). A copy the page holds the other half of, so giving
     up here cancels this copy and leaves the page's read alone. */
  const urls = assetsInHtml(await (await whole(response)).clone().text());
  /* The same refusal precache() makes, for the same reason and against the same
     page: a 200 text/html from this origin that names no hashed asset is not
     this app. The reverse proxy's default page while a host is being edited is
     exactly that, and so is a dev server's index.html. install declines to
     start from one; without this, the first navigation writes one straight over
     a good shell, and every offline launch afterwards renders it. */
  if (urls.length === 0) return;

  for (const url of urls) {
    /* Already there, whether install precached it or an earlier refresh did.
       The overlap with what the page is fetching for itself is unavoidable —
       both start from the same HTML — but a deploy that changes one chunk must
       not re-fetch the other. */
    if (await cache.match(url)) continue;
    const named = await fetchFresh(url);
    /* Turned down on its headers, and this fetch is the refresh's own: nothing
       else will ever read this body or end it. */
    if (!isAsset(named)) {
      release(named);
      return;
    }
    /* `whole` for the reason precache() has it: the put reads this body to the
       end, and a chunk whose bytes stall would hold the put — and with it this
       navigation's waitUntil — open for ever, with the shell never published;
       one whose bytes trickle, for as long as the browser let the event live
       (309 s, measured — see CEILING_MS). A deadline or the ceiling arrives as
       a rejection, which the caller swallows, the connection is let go, and the
       shell that is there stays. */
    await cache.put(url, await whole(named));
  }
  await cache.put(SHELL, response);
  /* Then the collection, now that the shell this replaced names nothing — but
     not while a worker is installing. A changed sw.js precaches into this same
     cache, the files its shell names first and its record last, and until the
     record lands those files are named by nothing: collected now, they would
     go from under an install that fetched them once and will not again, and
     the shell it publishes would name files that are gone. A worker installed
     and waiting writes nothing, and no other refresh in this worker is part
     way through its writes, because navigate() runs them one at a time. What
     is left is an install that starts after this look, writes its files before
     collect() lists the cache and its record after collect() reads the record:
     the race purge() narrows, narrowed here by the same order (see
     `collect`). */
  if (!self.registration.installing) await collect(cache);
}

/* The refresh this worker is running, or ran last, with every one queued
   before it: see the end of navigate(). A worker stopped and started again
   begins with nothing queued, as it begins with nothing running. */
let refreshing = Promise.resolve();

/* A navigation is network-first, and this function is the whole of decision 8.
   Every online launch renders the deploy that is live right now, so a cache
   this worker has poisoned can be seen only by a phone that is offline or
   cannot reach this origin inside the deadline — never by one whose network is
   working — and the browser's own update check on /sw.js runs whatever this
   cache holds, which is what lets sw.kill.js land if this file has to be
   withdrawn.

   "Online" is decided by the fetch and the deadline on it, and by nothing else.
   Every cache operation below is outside that judgement: a store that will not
   open and a write that is refused are not a network, and a handler that
   catches them together answers a phone with a perfectly good network out of
   its own cache. */
async function navigate(event) {
  /* `self.fetch` directly, with the event's own Request and NO init: an init
     would rebuild it and demote `mode: "navigate"` to "same-origin" — see
     `timed`, which is why this one call does not use it. The deadline comes
     from the race instead, and the price is that a fetch which loses the race
     is left in flight rather than aborted, until its headers arrive. */
  const fetching = self.fetch(event.request);
  let response;
  try {
    response = await deadlined(fetching);
  } catch {
    /* Rejected, or never answered at all — the deadline lands here as its own
       rejection, with the request still running behind it. The two are one
       thing from a phone: the page is not coming. This is the only path on
       which a cached page is ever served.

       And the answer that comes after all is turned down unread, the moment its
       headers arrive: the page has its shell, and a body nobody reads and
       nobody cancels holds its connection for as long as the server keeps it
       open. Measured in Chromium with no DevTools attached to the worker, a
       navigation answered at 12 s held its connection 28 s past its headers,
       until the browser stopped the idle worker. A request still waiting for
       headers is the browser's to end. Its rejection is swallowed, as the race
       already swallows it: one that fails late is nobody's to report. */
    fetching.then(release, () => {});
    const shell = await cached(SHELL);
    if (shell) return shell;
    /* Nothing fetched and nothing cached — a first launch that never got a
       network, or one whose storage has gone. Throwing hands the browser its
       own network-error page. Resolving with undefined would reach the same
       page through a TypeError inside respondWith, saying nothing. */
    throw new Error("offline, and no shell cached");
  }
  /* The clone is the copy that may be stored; the original is the page's, and
     nothing below is allowed to touch it. Storing the response itself passes a
     test written the wrong way round and fails in a browser: the put spends the
     body the page is about to read.

     Queued behind the refresh before it, never run beside it: side by side, the
     later one collects what the earlier has written and not yet named, and the
     earlier can then publish its older shell over the later's, naming files
     that are gone. The copy waits its turn unread, holding what the page reads
     until then — one HTML document. Each refresh's failure is swallowed where
     it joins the queue, so one that fails does not stop those behind it. */
  const copy = response.clone();
  refreshing = refreshing.then(() => refresh(copy)).catch(() => {});
  event.waitUntil(refreshing);
  return response;
}

/* Cache-first, and this is the only branch in this file that is. The filename
   carries a hash of the bytes, so the bytes behind one of these URLs never
   change — the same fact SpaCacheMiddleware serves them on for a year
   (core/channels/spa.py; measured against the running app:
   `cache-control: public, max-age=31536000, immutable`). A deploy does not
   invalidate them, it stops naming them, and collect() is what takes the ones
   no longer named — after the refresh that stops naming them, or at the next
   activate.

   The cost of cache-first with no expiry, written down because nothing else in
   this file says it: an entry that is wrong is wrong until something deletes
   it. There is no revalidation here, and collect() takes only what neither
   install's record nor the cached shell names, so a wrong entry stays for as
   long as either of them names it. The mitigations are upstream — a hashed
   name cannot be poisoned by a deploy, isAsset refuses what is not an asset,
   and the kill switch is the floor. */
async function asset(event, pathname) {
  /* Keyed on the pathname, not the request, and both halves of that matter: it
     is the spelling precache() and MANIFEST use, so one URL has one entry here,
     in the record and in the prune; and a hashed file asked for with a query on
     the end is served the bytes already stored rather than fetched again under
     a second key that the next collection would then delete. Raw, not
     decoded — the raw pathname is what install stored. */
  const stored = await cached(pathname);
  if (stored) return stored;

  const response = await timed(event.request);
  /* isAsset refuses what must not be stored: /assets is a StaticFiles mount
     today, so a hash a deploy has removed comes back 404 with a JSON body — but
     if that mount ever goes, mount_spa answers any unknown path with index.html
     and a 200, and a page of HTML stored under a .js URL is how a PWA bricks
     itself, since every launch afterwards parses it as a module and dies before
     the first paint.

     What passes it is written BESIDE the answer, in waitUntil, not in front of
     it. The put reads its copy of the body to the end, so awaited here it held
     the page's own response — headers and all — for as long as the body took,
     and for ever on one that stalled: strictly worse than no worker, where the
     page at least has the headers and whatever bytes came. Now the page gets
     exactly what the network gave it, when the network gave it, and nothing
     the write does can reach it. The clone is taken here, before the page has
     read a byte of the original, because a clone of a body already read is a
     TypeError. */
  if (isAsset(response)) event.waitUntil(store(pathname, response.clone()).catch(() => {}));
  /* A network failure propagates rather than being answered from somewhere
     else. There is nothing honest to serve for an asset that was never cached
     and cannot be fetched, and the obvious something-else — the shell, which is
     what a fallback would hand back — is the brick above by another door. */
  return response;
}

/* The asset branch's write, with every failure it can meet swallowed by its
   caller, and each for the same reason: none of them is the page's problem.
   The open may fail — no usable CacheStorage — and the copy is released
   unread, as refresh() releases its own (see `release`). The body may never
   finish, which `whole` bounds. And the put itself may be REFUSED — a phone
   that is full — which must cost the page nothing at all: the bytes are in
   hand, the network said 200, and a worker that turned that into a failed
   request would be strictly worse than no worker. */
async function store(pathname, response) {
  const cache = await openCache();
  if (!cache) {
    release(response);
    return;
  }
  await cache.put(pathname, await whole(response));
}

/* Judged before any branch, deliberately: a hand-typed API URL in the address
   bar arrives with mode "navigate", and a navigation branch that ran first
   would cache whatever came back as this app's shell. */
self.addEventListener("fetch", (event) => {
  if (isRefused(event.request)) return;
  const url = new URL(event.request.url);

  /* Mode first, and it is a real decision rather than an order of convenience.
     A navigation to a hashed asset URL — a pasted link, devtools' "open in new
     tab" — matches both branches, and it must be answered network-first like
     any other navigation: cache-first would serve a stale chunk to someone who
     navigated to it precisely to see what is deployed. The `return` is the
     other half: respondWith twice for one event is an InvalidStateError, and it
     would take the navigation down with it.

     The test for what arrives here with mode "navigate" is the browser's, not
     this file's: every /assets/* request the real app makes is mode "cors",
     because index.html marks the module script and the stylesheet
     `crossorigin`. A branch keyed on anything but "navigate" — `!== "no-cors"`
     was the mutant — routes every chunk, stylesheet and typeface through
     navigate() and serves the HTML shell as a JavaScript module offline. */
  if (event.request.mode === "navigate") {
    event.respondWith(navigate(event));
    return;
  }

  /* A bare prefix test on a raw pathname, and it is safe ONLY because isRefused
     has already run: that is what turned away a cross-origin URL, a ranged GET,
     and a pathname whose decoded form holds a dot segment, before this trusts
     the pathname as a cache key. `/assets/..%2fapi/x` begins with ASSETS as far
     as startsWith is concerned and is a traversal once uvicorn has decoded it.
     Move the refusal after this branch and this line needs isAssetUrl instead. */
  if (url.pathname.startsWith(ASSETS)) {
    event.respondWith(asset(event, url.pathname));
  }

  /* Everything else — /manifest.json, /icon.svg, /apple-touch-icon.png — falls
     out of here unhandled, which is not the same thing as refused: the browser
     fetches them exactly as it would with no worker installed. They are small,
     they are read while the phone is online, and SpaCacheMiddleware's third
     tier revalidates them on every load. */
});
