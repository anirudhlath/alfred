# Alfred — web client

The phone-first PWA that replaced the Mission Control SPA. One screen (the Room),
one interrupt (the Door), four identity gates over the top of them and two for a
launch the house has not answered yet — and, under the Room, the Workshop, where the
house's streams are read as they happen. A hand-written service worker keeps the app
shell on the phone, so it opens with no network.

Design: `docs/design/2026-09-04-pwa-client-handoff/` (tokens, copy and the
`Alfred.dc.html` prototype the presence field and slide maths were ported from).
Spec: `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md`.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server, proxying `/api/*`, `/health` and `/ws*` to `:8081` |
| `npm run build` | `tsc -b` then `vite build` → `web/dist/`, which the web channel serves |
| `npm test` | Vitest, once |
| `npm run lint` | ESLint over `web/` |

The type check lives in `build`, not `lint`. CI runs `lint`, `test` and `build`, in
that order, then serves the built `dist/` to `tests/core/channels/test_spa_ci.py`.

## Layout

```
public/         unhashed, copied into dist/ byte for byte:
  sw.js                 the service worker ("The service worker" below)
  sw.kill.js            the kill switch; nothing registers it until it is
                        copied over sw.js
  manifest.json         the manifest in the dark theme's colours
  manifest-light.json   the same in the light theme's; applyTheme swaps them
  icon.svg              the mark, as paths: the PNGs' source, and the favicon
  apple-touch-icon.png  180x180, opaque: the iOS home-screen tile
  icon-192.png          the manifests' small raster icon
  icon-512.png          the large one, which is also the maskable
src/lib/        no React: api, sockets, formatters, the presence physics,
                the action reducer, the slide maths, audio and recording,
                the stream catalogue (streams.ts), the feed reducer (feed.ts),
                the causal thread (trace.ts), the page's half of the service
                worker (sw.ts) — and one module per bench:
                memory.ts (the three-shape episodic adapter), triggers.ts
                (kinds and meta lines), system.ts (sessions, credentials,
                pairing, integrations, attention, the health cells)
src/shell/      providers, the two surfaces everything rises on, the
                ErrorBoundary the Workshop wraps its panel in, the update
                note (UpdateNote.tsx, useUpdateReady.ts) and useOnline.ts
src/gates/      setup, sign-in, expired, denied, offline and unanswered — and
                the router between them
src/room/       the one screen: presence field, headline, timeline, composer,
                and the handle into the Workshop (WorkshopHandle.tsx)
src/door/       the approval interrupt: banner, fuse, slide, deep link
src/workshop/   the Workshop layer: the bench switcher (BenchSwitcher.tsx,
                tabs.ts) and four benches, each a pure view over one hook —
                ActivityBench + EventRow/StreamChips/useActivity,
                MemoryBench + RoutineRow/useMemory,
                TriggersBench + TriggerRow/useTriggers,
                SystemBench + SystemSections/IntegrationRow/useSystem,
                over the shared SystemFrame.tsx and Switch.tsx
src/sheets/     the held-back queue and `Why Alfred did that`
src/test/       jsdom setup, shared fixtures, the contrast restatement, the
                fake service-worker scope (sw-harness.ts), and the tests of
                what lives outside src/: the workers, manifests, icons and
                index.html
```

Tests live beside their source (`lib/history.ts` → `lib/history.test.ts`).

## Conventions

- Colours are CSS custom properties (`var(--accent)`), never Tailwind palette
  colours. Both themes are defined in `src/index.css` under `:root[data-theme]`.
- Type comes from the `.t-*` classes, not ad-hoc sizes.
- Imports use the `@/` alias; siblings are relative inside `src/lib/` and in tests.
- The status vocabulary is closed (spec §10). The Room and the Door say `queued`,
  `applied`, `last true HH:MM` and `expired · not done` — the first two are the Door's
  phase pill, not the Room's; the Workshop's status line adds the
  handoff's `live · N ev/s`, `paused · N new` and `last true HH:MM · not live`.
  `unknown since HH:MM`, `takes effect within 60 s`, `hot / cold` and
  `candidate · active · dormant · archived` arrived with the phase-3 benches, as did
  `decaying` — the handoff's own word for a hot memory weighed lightly and never
  recalled, defined in `docs/web-frontend.md` because spec §10's list omits it.
  Mono, lower case — the one exception is the Door's phase pill
  (`Confirmed · queued`, `Applied`, `Expired`, `Answered`), set in the layer's own type,
  ink on paper. Do not invent new words for system state.

## What phases 1 to 4 cover

Phase 1: shell, theme and viewport; the four identity gates; the Room (presence
field, headline, status line, offline note, DND row and the held-back sheet, the
merged timeline, composer, hold-to-talk, notifications); and the Door (banner, fuse,
slide-to-confirm, the five phases, tombstones, and the `/actions/:id` deep link).

Phase 2: the handle under the composer and the Workshop it opens (`‹ Room`, the
status line, the four-bench switcher); the Activity bench — all eight streams live,
newest first, chips to solo one, pause and resume, `↑ older` paging by cursor, a row
expanded to its payload; and causality — `why?` on the Room's reflex rows and
`Why · causal thread` on the bench's RX rows open the `Why Alfred did that` sheet, a
column of the entries that share an id with the observation, joined by name.

Phase 3: the Workshop's other three benches, each a pure view over one hook called in
`WorkshopPanel` and gated on whether its bench is showing (see `docs/web-frontend.md`,
"The Workshop's four benches").

- **Memory** — episodic browse and search by meaning over hot and cold, an honest
  `model:` pill, the semantic documents, the routine lifecycle with its rail and
  confidence sparkline, and the scratchpad. Read-only: no route exists to forget,
  redact or promote, so the bench offers no button that pretends to.
- **Triggers** — every trigger, filtered by kind, toggled and fired. Both controls are
  fire-and-forget, so the row keeps the state the last read gave it and carries a
  `queued HH:MM · … · takes effect within 60 s` note until a fresh read says otherwise.
- **System** — health, cloud spend, quiet hours and the held-back queue, maintenance,
  auth sessions, connected services and their credential forms, passkeys and pairing,
  and the reflex attention set. Web Push (the Reach card) lands with phase 5; the card
  is deliberately absent rather than inert.

Phase 4: the app installs and opens with no network.

- **The icon set and the manifest pair** — see "The icons and the manifests" below.
- **The service worker and its kill switch** — see "The service worker" below. A
  production build registers the worker from `main.tsx` (`src/lib/sw.ts`), and
  `npm run dev` unregisters it instead.
- **The update note** — `A newer Alfred is waiting. Nothing is lost; I pick up where we
  left off.` and `Reload`, in the Room's header, while a changed `sw.js` waits.
- **Two gates for a launch that has read nothing.** `OfflineGate` (`No way to the
  house.`) shows while the phone says it is offline, and `UnansweredGate` (`The house is
  not answering.`, with `Try again`) shows when the phone is online and the house gave
  no answer. Sign-in and setup follow only the house's own answer (see
  `docs/web-frontend.md`, "The gates").

## What it does not

- **Web Push, and the install rail that leads to it** — phase 5. The standalone metas
  are in `index.html` and the gesture the rail teaches already installs a correct app,
  but there is no `install` gate, no `standalone` gate, no home-screen prompt row and no
  System › Reach card: the rail's every step exists to reach *Allow in iOS*, and steps
  with nothing behind them would be controls that pretend. Phase 5 owes all four (the
  handoff's copy for them is final), plus VAPID, the push adapter, the registration, and
  the worker's `push` and `notificationclick` handlers — and any cache it wants goes
  inside `alfred-shell-<VERSION>` or into `purge()`'s keep-list, or the next activate
  deletes it. Filed as `docs/backlog/low/pwa-phase4-followups.md` §1.
- **Four things the old SPA could do and this client cannot**, which is the whole of the
  remaining parity gap (spec §8's gate is *full capability parity with the old SPA*).
  Three of them have no endpoint to call: **creating, editing or deleting a trigger**
  (the admin API has browse, toggle and fire and nothing else — the Triggers footer says
  so out loud), **restarting a service**, and **downloading logs**. The fourth has an
  endpoint and no UI: **removing a passkey** — `DELETE /api/auth/credentials/{id}` exists
  and refuses the last one, but taking away the passkey in your hand needs a confirmation
  design first. Filed as `docs/backlog/low/pwa-phase3-followups.md` §7, §10 and §11.
- **Desktop** — phase 6.

## The service worker

`public/sw.js` is hand-written — 253 lines of code under 766 of comment at `c49e66d`, 3.0
to 1, counted with acorn's tokenizer (a line is code if any token touches it, comment if
only a comment does, and a blank line, inside a comment or not, is neither) — and shipped
verbatim by Vite's `public/` copy: no `vite-plugin-pwa`, no Workbox, no generated
precache manifest. The reasoning is in
`docs/superpowers/plans/2026-09-16-pwa-phase4-install-and-offline.md`; the short version
is that a versioned cache, an explicit purge and a documented kill switch all have to be
things a reviewer can *read in the shipped bytes*, and a negative requirement — "this
worker never caches `/api`" — is one assertion against a hand-written file and an argument
against a plugin's route table.

**Install, activate, a fetch handler with two branches, and one message.** It fetches `/index.html` past the
HTTP cache and takes the `/assets/…` URLs Vite wrote into it as the precache manifest —
the entry chunk and the stylesheet. It then reads the **stylesheet** for the `url()` rules
the HTML never mentions and takes the `.woff2` among them: 8 files, 139 436 of the
220 380 font bytes the build emits (measured at `c49e66d`). The 10 `.woff` fallbacks
`@fontsource` ships beside them are skipped, because nothing that can run a service
worker has needed one for years. All of it plus a
`/__shell-manifest` record of what it actually cached goes into `alfred-shell-<VERSION>`
(the one constant at the top of `sw.js`; the tests read it from there rather than restating
it, and this file names no version for the same reason) — the assets first, then the shell
that names them, then the record, each write finished before the next starts. That cache is
the one the previous worker is still serving from, so an install that fails part way — a
phone that fills up — must leave the old shell and its assets as they were, not a new shell
naming a chunk it never stored. The record goes last because it is the install's claim to
have finished: activate prunes by it, and the activate that reads it may be an older worker's
still waiting behind the active one, so a record written before a shell that is then refused
would have that activate delete the fonts of the shell that is still there.

Three things about how it fetches. Everything *install* asks for goes out
`cache: "reload"`, so a worker installing seconds after a deploy cannot precache the bytes
that deploy replaced — the runtime handlers deliberately do not, since a hashed asset
cannot have changed and a navigation is the page's own request to make. Every network call
in the file gets a 10-second deadline, install and runtime alike — and every **wait on the
body** that follows it does too, because `fetch()` resolves on the headers, so a route that
answers 200 and then goes quiet leaves `await response.text()` pending for ever on a
request that looks finished. The body read easiest to miss is `Cache.put`'s own: a put
reads what it is handed to the end before it writes, so every network body the worker
stores or reads goes through `whole()` first — a clone read to the end, which leaves the
original's bytes in memory for the put. (Measured in headless Chromium with no DevTools
attached to the worker — DevTools keeps an inspected worker alive, so a lifetime measured
under it, as Playwright measures, is not a phone's — against a shell naming a chunk whose
body stalls: without it the install sat `installing` until the browser's own limit on the
event failed it at 300 seconds; with it the install fails at 10.) `install` runs inside
`waitUntil()`, so any of these hangs would hold it open until the browser gave up, leaving
the phone with no worker at all, and the same hang on a navigation would leave a standalone
window waiting for ever with no chrome to escape through.

The deadline on a body is per **piece**, not per body: each read gets ten seconds to bring
the next piece, the first and the one that finds the end included. So a stall is noticed
ten seconds after the last byte, wherever it happens, and a slow link that keeps delivering
is waited for while it keeps delivering. One deadline of that length over the whole body
cannot tell those apart — measured in headless Chromium at `6dabc43`, that build's 414 kB
entry chunk arriving steadily at 32 KiB/s takes 12.6 seconds, and a ten-second deadline on
the whole body failed that install at 10.0 on a first visit and on every visit after it,
since a failed install leaves no worker; per piece, it activates at 12.6.

Over the pieces there is one **ceiling** on the whole body, `CEILING_MS`: two minutes from
its headers, because a limit on silence never ends a body that is never silent. Measured in
Chromium with no DevTools attached, a chunk sending a byte every five seconds held an
install `installing` until the browser failed it at 300 seconds, and the same chunk named by
a new deploy held the navigation's refresh, and its connection, for 309 seconds after the
page was served; with the ceiling, the install fails and the refresh lets go at 120. Two
minutes is generous on purpose: the entry chunk, 418 537 bytes at `c49e66d`, reaches it
only below 3.5 kB/s — about 28 kbit/s, a GPRS-class link, on which the page's own load of
that chunk takes as long — and it ends well inside the browser's 300. It is per body, so an event whose
every body crawled could still meet the browser's limit first. What a stall costs is ten
seconds, and a body still arriving at the ceiling costs the same things: in the shell or a
chunk it fails the install, in a font it costs that font, in a refresh the shell already
there stays — and the page's own copy of a response is never cut, since what the worker
gives up is its copy.

The deadline comes in two shapes and the choice is forced. Install's requests and the
asset branch's **abort**, with a 10-second `AbortSignal` on the wait for the headers, which
also cancels the request. A navigation **races**, because attaching a signal means passing
a non-empty init, and a non-empty init makes `fetch()` rebuild the Request — which turns
`mode: "navigate"` into `"same-origin"` and clears the reload- and history-navigation
flags. (The same rebuild resets the referrer to the worker: measured in Chromium, the asset
branch's forwarded requests arrive with `Referer: …/sw.js`. Harmless for a same-origin
hashed file; not nothing.) Forwarding the page's navigation untouched is worth one fetch
left in flight after the race. Each read of a body races too, and so does the body as a
whole against the ceiling, and there the loser is not left in flight: `whole()` **cancels**
what it was reading — the clone and the original, every branch of the body the worker
holds. Not an abort, although aborting the request's controller would release the read as
well: an abort errors *every* branch of the body, and in the asset branch and a
navigation's refresh the page is reading the other one. A cancel stops at the tee, so it
lets go of install's and the refresh's own fetches and never of the page's. A response the
worker turns down on its headers — a shell that is not HTML, a page of HTML under a hashed
URL — is cancelled unread the same way, through `release()`, and so is a copy of the page's
response that the refresh or the asset branch's write turns down before reading it: a body
nobody reads and nobody cancels keeps its request open. Measured in Chromium with no
DevTools attached, a font and a deploy's chunk turned down unread each held their
connection until the browser stopped the idle worker 30 seconds later; released, they close
at once. And a missing *font* is survivable where a missing *chunk* is not: the app is
legible in the system stack, so the font loop declines and carries on while the asset loop
throws.

What may be cached is one function, `isAssetUrl`: same origin, under `/assets/`, no encoded
separator. The extraction either side of it is deliberately wide so that widening it cannot
widen the cache.

**`activate` is the purge, and it is the recovery path from a bad cache.** Two of them.
Every cache whose name is not the current one is deleted outright — no `alfred-` prefix
filter, because anything this origin holds that is not the current cache is not ours to
keep — so bumping `VERSION` takes the whole previous world with it. Then, inside the
current cache, every entry that neither the `/__shell-manifest` record nor the shell now in
the cache names (the union is the next paragraph): asset URLs are content-hashed, and
`VERSION` is bumped by hand, so for every deploy that does *not* bump it this is the only
thing that reaches what the previous build of the same file cached. Entries are compared
on the full URL, not the pathname, so a cross-origin copy sharing a path with something
precached goes too. It also reaches whatever the `fetch` handler stores at runtime, by the
same rule: an asset cached on a cache-first miss is in no record, so it stays only if the
cached shell names it — the chunks a refresh published are — and the next activate takes
anything else, such as a font install declined and the page fetched later. That is
intended: what the record and the shell name is what the phone needs offline, any other
runtime entry is re-fetchable, and the alternative is a cache with no ceiling.

The keep-set is that record **union what the shell now in the cache names**, and the second
half is not optional. A navigation's refresh publishes a newer deploy's chunks and then its
HTML, and neither is in any record — so pruning on the record alone would delete the assets
of the very page the next offline launch boots, while keeping the page itself, whose key is
in the record. That is a shell that renders a white screen: the failure this whole file
exists to prevent, arriving from the inside. It is read out of the cache rather than off
the network, because the question at activate is what the entries on disk are *for*. The
cost is one cycle of hysteresis — a shell that install has since replaced keeps a few dead
chunks for one activate — and a shell that will not read is treated exactly like a record
that will not parse: keep everything.

**A record it cannot trust means keep everything.** All three shapes — absent, not a list,
or not readable as JSON — are read the same way, because deleting on a record you cannot
read is guessing, and the thing it guesses with is the only copy of the app the phone has
offline. The two that are not merely defensive: a JSON *string* would spread into single
characters and take every precached entry with it, silently; and an unreadable body would
reject `purge()` itself, so the claim never happens — after the version purge has already
dropped the old cache. Each delete is caught on its own for the same reason: one transient
storage failure must not cost the caches behind it, the entry purge, or the claim.

Only then does it `clients.claim()` — a page handed to this worker mid-purge is being
served from a cache that is still being emptied underneath it. The one race left is a
worker *installing* while this one activates: the entry keys are snapshotted before the
record is read, which narrows it, and closing it would need a lock two workers do not have.

**The `fetch` handler refuses first and then branches twice.** `isRefused` is the whole of
rule 1 in one predicate — every request this worker must never answer — and it runs before
either branch, deliberately: a hand-typed API URL in the address bar arrives with mode
`navigate`, and a navigation branch that ran first would cache whatever came back as this
app's shell. Everything that is neither refused nor claimed by a branch — `/manifest.json`,
`/icon.svg`, `/apple-touch-icon.png` — still reaches the network untouched, which is not
the same thing as refused.

What it refuses, and why each is not obvious. Anything but a `GET`, and anything carrying a
`Range` — a ranged request asks for part of a body and a cache-first branch answers with a
whole one. Any scheme that is not this origin's: a `blob:` URL's origin is the origin
*inside* it, so it compares equal to ours and the origin test alone does not settle the
question. Any other origin, compared on `url.origin` so that
`https://<this-host>@evil.invalid/` reads as `evil.invalid` and `https://user@<this-host>/x`
reads as us. And then the path list — `/api/`, `/health`, the two sockets, and FastAPI's
own `/docs`, `/docs/oauth2-redirect`, `/redoc` and `/openapi.json`, which are live on this
origin, outside `/api/`, and `text/html`: one navigation to `/docs` would otherwise write
Swagger UI into the shell cache and every offline launch would render it.

The list is applied to **the pathname decoded the way uvicorn decodes it**, and not to the
raw one, because uvicorn `unquote()`s the whole path before Starlette routes it:
`/%68ealth` is `/health` to the server, and one percent-escape used to walk straight past
the guard. Only the decoded form, because every entry in the list is escape-free and
decoding rewrites nothing but escapes — a raw pathname the list matches decodes to one it
still matches, so a second test on the raw form could refuse nothing the first does not.

A path that cannot be decoded at all is refused rather than guessed at, as is one whose
decoded form contains a `.` or `..` segment. `new URL` normalises the plain forms long
before this runs, but not `/%2e%2e%2fapi/x` — which does *not* reach the API, measured:
Starlette does not normalise dot segments, so it falls through to `mount_spa` and answers
200 `text/html`. The rule is there so that it never has to: the raw pathname of
`/assets/..%2fapi/x` still begins `/assets/`, which is the prefix the asset branch reads
as a cache key, so a server that ever did normalise would have it cache a 401 as a
hashed chunk.

**A navigation is network-first; an `/assets/` URL is cache-first.** Those are the two
branches, and nothing else is answered at all. A navigation goes to the network, and the
cached shell is served on exactly one condition: that fetch did not answer inside the
deadline. The deadline is part of that condition — a request that is accepted and never
answered is indistinguishable from being offline, and an installed PWA is where that
matters most, because a standalone window has no browser chrome to escape a hang with. If
there is no cached shell either, the navigation throws rather than resolving with nothing.
The branch is keyed on `mode === "navigate"` and not on `destination === "document"`: those
travel together for a top-level navigation and only for one, since a navigation inside an
`<iframe>` is mode `"navigate"`, destination `"iframe"`.

**A cache failure is not a network failure, and the difference is the whole of decision 8.**
`cache.put` can reject — a phone that is full answers `QuotaExceededError`, and an installed
PWA on iOS is exactly the thing that meets it — and `caches.open` can reject outright, which
Safari does under storage pressure and in some private modes. None of that is allowed to
reach the network verdict: every cache operation in both handlers is outside the `try` that
decides "offline", each one is caught on its own, and a worker that cannot use its cache
serves exactly what a browser with no worker would. Caught together with the fetch — which
is one `catch` one line too wide — a failed *write* answers an online phone out of its
cache, which is decision 8 exactly inverted.

**The shell and the assets it names are published together, or not at all.** `sw.js` is
byte-identical across most deploys (`VERSION` is bumped by hand), so a deploy usually runs
no new install and no new activate: the refresh on a navigation is the only thing that ever
updates the offline copy of the app. Writing the new HTML on its own would leave the cache
holding a shell that names hashes it does not have — and since `asset()` propagates a
network failure by design, the next cold launch with no network is a white screen rather
than the old app. So the refresh fetches what the new HTML names, reads each body whole,
stores those first, and publishes the shell last; if any of it fails — a body that stalls,
or is still arriving at the ceiling, included — the shell already there stays, whole. An
offline copy that is self-consistent and one deploy old beats a newer one that cannot boot.
It runs in `waitUntil()`, so the page is never blocked on it, and nothing it does can change
what that navigation was served.

What that ordering costs, written down because nothing else says it: a refresh that stops
half way — a phone that fills up between the chunks and the HTML — leaves chunks in the
cache that no record names and no cached shell names. They are not dead weight while they
last: the asset branch serves them cache-first to any page whose HTML names them, starting
with the online page whose navigation began the refresh, and the next online navigation to
that deploy finds them cached and has only the HTML left to publish. What they lack is a
claim on the next activate. They are bounded (what a shell names is the entry chunk and the
stylesheet, 486 880 bytes at `c49e66d`) and they are collected by that activate, which is the
same collector every other runtime entry relies on. Nothing deletes them earlier on
purpose: a refresh that deleted would be racing a concurrent install writing the same
bytes, and "named by nothing" is only a settled fact at activate.

Two refusals gate the refresh. `isShell` — 200 **and** `text/html` — is what stops an error
page becoming the app's offline face: nginx serving a stale cached copy with
`proxy_cache_use_stale` answers 502 with the app's own HTML, and a misconfigured host can
answer 200 with the same bytes under the wrong content type. (Not a captive portal: over
https it cannot intercept at all, and what it does answer is 200 `text/html`, which this
accepts.) And a 200 HTML page that names **no** hashed asset is refused exactly as `install`
refuses it — the reverse proxy's own default page while a host is being edited is that page,
and one navigation through it would otherwise overwrite a good shell.

An `/assets/` URL is cache-first because the filename carries a hash of the bytes, which is
the same fact `SpaCacheMiddleware` serves them `immutable` for a year on. The cache is keyed
on the **raw pathname** — install's spelling, so one file has one entry in the cache, in the
record and in the prune; a hashed name asked for with a query on the end is served the bytes
already stored, and decoding the key instead would miss on every load. A miss goes to the
network, with the same deadline, and stores what comes back only if it is a 200 that is not
HTML: `/assets` is a `StaticFiles` mount today and answers a removed hash with a JSON 404,
but if that mount ever went, `mount_spa` would answer with `index.html` and a 200, and a page
of HTML stored under a `.js` URL is how a PWA bricks itself. 200 rather than `response.ok`
(200–299) is deliberate, because none of the other eight is a representation of the file
that was asked for: a 204 or 205 has no body and would cache an empty chunk, a 203 says a
proxy has *transformed* the body — the one thing a content hash promises has not happened —
and a 206 is a partial body the Cache API rejects outright, as a `TypeError` from a line
that reads as though it cannot fail. They are declined rather than attempted and caught:
the `catch` on the put is there for the write this worker cannot see coming, a full disk,
not as cover for one it can. A network failure propagates — there is nothing honest to serve
for an asset that was never cached and cannot be fetched — but a *storage* failure does not:
the bytes are in hand and the page gets them. The write runs **beside** the answer, in
`waitUntil()`, not in front of it: the put reads its copy of the body to the end, so awaited
first it held the page's response — headers and all — for as long as the body took, and for
ever on one that stalled (measured in Chromium with no DevTools attached to the worker,
against a stalled chunk: awaited, the page had no headers 15 seconds in; beside the answer,
it had a 200 and the first bytes 42 ms after asking). The page gets exactly what the network
gave it, when it gave it.

The cost of cache-first with no expiry, written down because nothing else says it: an entry
that is wrong stays wrong until something deletes it. There is no revalidation, and
`purge()` only runs when a new worker activates, which only happens when `sw.js`'s own bytes
change. What stands between that and a poisoned cache is upstream — a hashed name cannot be
poisoned by a deploy, the store guard refuses what is not an asset, and the kill switch is
the floor.

**A new worker waits, and one message takes it over.** `install` never calls
`skipWaiting()`, so a changed `sw.js` installs, precaches and then waits for every page the
old worker controls to close, rather than activating under someone mid-conversation —
activate prunes the cache down to the new build and then claims every open page. The one way it
activates sooner is the one message it takes, `{ type: "SKIP_WAITING" }`, which the page's
`applyUpdate()` in `src/lib/sw.ts` posts when the user taps `Reload` on the Room's update note
(`src/shell/UpdateNote.tsx`), the note that shows while a worker waits. Anything else posted
to it is ignored, and a `null` or `undefined` one does not throw. What this carries
is a change to `sw.js` itself, not a deploy: `VERSION` is bumped by hand and the file is
byte-identical across most deploys, so most deploys install nothing, leave nothing waiting,
and are taken at the next navigation by the network-first branch.

**`src/lib/sw.ts` registers it**, from one `installServiceWorker()` call in `main.tsx` after
the app is handed to React. In a production build that registers `/sw.js` at the root; in dev
it unregisters every worker and deletes every cache instead, because a worker left behind by
`npm run preview` outlives the preview server and would serve `npm run dev` a stale shell on
the same origin. A `register()` that rejects — `/sw.js` answered with `index.html` by
`mount_spa`, which is what a deploy without the file looks like — is reported with
`console.error`, and on a phone with no worker yet it is the only sign. A phone that already
has one gets no rejection there, since `register()` answers at once without fetching; it
hears the same refusal from the check `installServiceWorker` runs on every return to the
foreground (`registration.update()` — an installed PWA is brought back rather than
relaunched, and the browser itself only checks at a navigation), which fails quietly offline
and with a `console.warn` online. A worker that reaches `installed` behind an existing
controller is announced through `onUpdateReady()`, which the Room's update note subscribes
to (`src/shell/useUpdateReady.ts`), and the page reloads once when the new worker takes
control — but not when a first worker claims a page that had none, which `activate`'s
`clients.claim()` does on every first visit. There is one install per page: a second
replaces the first, a dev one included, and the first attaches nothing more, even if it was
still inside `register()`.

Three rules govern it, and they are restated at the top of the file itself:

1. **It caches the app shell and nothing else.** `/api/*`, `/health`, the two sockets and
   FastAPI's docs routes are never fetched, never stored, and are never passed to
   `respondWith()` — `isRefused` is where that is decided, once, for every branch. This
   client's whole design is that it never states a server fact it has not just read; a
   cached API response is that failure with a longer half-life.
2. **Navigations are network-first**, so a cache this worker has poisoned is reachable
   only by a phone that is offline or cannot reach this origin inside the deadline, never
   by one whose network is working; and the browser's own update check on `/sw.js` keeps
   running — which is what lets a kill switch land if this file ever has to be withdrawn.
3. **Every global goes through `self.`**, which is the seam `src/test/sw-harness.ts`
   replaces to evaluate the shipped bytes in a fake `ServiceWorkerGlobalScope`. It is
   enforced by `no-restricted-globals` in `eslint.config.js`, not by convention:
   `globals.serviceworker` would otherwise make a bare `caches` perfectly legal.

Two things to know before editing it. **A 200 is not proof of anything** — `/assets` is a
StaticFiles mount (`core/channels/spa.py:39-40`), so a removed hashed chunk answers 404
with a JSON body, while every *other* unknown path answers `index.html` with a 200, so the
worker checks the status *and* the content type. And **a shell that names no `/assets/` at
all is refused**: a reverse proxy's default page and the dev server's own `index.html` are
both 200 `text/html`, and either would install cleanly as the shell and then stay, because
`install` only re-runs when `sw.js`'s own bytes change.

**The kill switch is `public/sw.kill.js`**, a worker that ships in every build and that
nothing registers. Copied over `public/sw.js` and deployed, it is the one remote way to empty
an installed phone: every installed copy takes it at its next update check, drops every cache
on the origin, unregisters, and reloads each page the old worker was serving, once. Its header
is the procedure, and says why deleting `sw.js` instead changes nothing and why the switch
reloads only those pages — the page that registered it, reloaded, would register it again.
Fire it as a commit of its own — `cp web/public/sw.kill.js web/public/sw.js`, committed
alone, through a pull request like any other. While `sw.js` is `sw.kill.js` byte for byte,
and only then, `sw.test.ts` and `sw-server-routes.test.ts` stand down, each with one passing
test that says the switch is fired (a warning on the run in GitHub Actions), so CI passes
and the merge deploys it. Afterwards, revert that commit. Two things follow from the fired
tree. `sw-server-routes.test.ts`'s checks of `core/channels/spa.py` and `web_server.py`
stand down with the rest, so a server-route change merged while the switch is deployed is
first checked on the revert pull request, and fails there. And every page loaded while
the switch is deployed ends up holding a registration that no longer exists: while the
phone is online, each of its returns to the foreground logs one `InvalidStateError` as
`service worker update check failed`, one per open page, for as long as the page lives —
after the revert too, until it is relaunched. Nothing shows on screen, and those pages take
the restored worker only at their next launch (`docs/backlog/low/pwa-phase4-followups.md`
§12).

The tests are `src/test/sw.test.ts` (the worker, driven event by event),
`src/test/sw-kill.test.ts` (the kill switch, through the same fake scope),
`src/test/sw-harness.test.ts` (the fake scope itself — it has to model `ExtendableEvent`,
`Cache.put`, `Clients.matchAll` and which worker controls each open page closely enough that
a test passing here means a worker that runs on a phone), `src/test/sw-server-routes.test.ts`
(the `NEVER` and `NEVER_UNDER` lists, read out of `sw.js` and held against the routes
`create_app` and `mount_spa` declare) and `src/lib/sw.test.ts` (the page's side, against a fake `navigator.serviceWorker`).
That `sw-server-routes.test.ts` reads the lists with a regular expression and `JSON.parse`
is why `sw.js` is not run through a formatter: a trailing comma added to either array
breaks the parse.

## The icons and the manifests

`public/icon.svg` is the source — the handoff's home-screen mark (`Alfred.dc.html:530`), a
capital `A` drawn as two stroked paths on a full-bleed square — and the three PNGs beside
it are rendered from it once and committed. It is geometry rather than `<text>` because a
`<text>` icon renders in whatever font the rasteriser has, and
`@fontsource-variable/dm-sans` ships `woff2` only, which neither `rsvg-convert` nor `resvg`
can load. It is full-bleed rather than rounded because iOS applies its own mask and
composites transparency on a home-screen tile as **black**, so the PNGs must carry no alpha
at all (spec §4.2). And `icon-512.png` is both `any` and `maskable`: the drawing's furthest
inked pixel is 170.5 px from the centre of the 512 canvas, inside the maskable safe
circle's 204.8 px radius, so one file is correct with a mask and without one.

### Regenerating the icons

The icons are checked-in bytes, not a build step. Nothing in the build, the container
image or CI rasterises anything — the container's `webbuild` stage is unchanged — so these
are needed only by whoever changes the drawing:

```bash
cd web
rsvg-convert -w 180 -h 180 -b '#25221F' public/icon.svg -o public/apple-touch-icon.png
rsvg-convert -w 192 -h 192 -b '#25221F' public/icon.svg -o public/icon-192.png
rsvg-convert -w 512 -h 512 -b '#25221F' public/icon.svg -o public/icon-512.png
```

`-b` is what forces the opaque background. ImageMagick is a working substitute:

```bash
cd web
magick -background '#25221F' -density 1024 public/icon.svg -resize 180x180 -alpha remove -alpha off public/apple-touch-icon.png
magick -background '#25221F' -density 1024 public/icon.svg -resize 192x192 -alpha remove -alpha off public/icon-192.png
magick -background '#25221F' -density 1024 public/icon.svg -resize 512x512 -alpha remove -alpha off public/icon-512.png
```

If either tool leaves an alpha channel in place, rewrite the file without one, for example
`magick public/icon-192.png -alpha off PNG24:public/icon-192.png`. `src/test/icons.test.ts`
decodes the committed PNGs and checks the pixels — the two colours and their census, the
mark's reach against the maskable safe circle, and a scan down the middle that reads apex,
counter, crossbar, feet — so a regenerated icon that came from a different drawing fails
rather than ships. Both colours come from `--bg` and `--accent` via `contrast.ts`, so
retuning the palette fails that test until the icon is redrawn and re-rendered.

### The manifests

There are two, `manifest.json` and `manifest-light.json`, identical but for `theme_color`
and `background_color` (`#25221F` in the first, `#F6F3EE` in the second), and `applyTheme`
swaps the `<link rel="manifest">` alongside the `theme-color` meta; `src/test/manifest.test.ts`
holds them to that. **Read that narrowly, because it is not yet known to fix anything on a
phone.** A manifest is consumed at *install* time, so the most this can do is align an
install with the theme in force at Add to Home Screen; it does not follow the theme
afterwards, and someone who installs at 22:00 has a dark launch screen at noon for ever.
On iOS it may do nothing at all: WebKit fetches at most one manifest per document load and
has no observer for a later `href` change, and the swap runs after parsing, so the
manifest iOS reads may always be the dark one `index.html` ships. iOS also builds its
launch screen from `apple-touch-startup-image` links, which this app does not ship
(`docs/backlog/low/pwa-phase4-followups.md` §6) — not from the manifest. Chromium *does*
observe the link, which is where the value is; it is also where the cost is, since Chrome's
WebAPK update check fingerprints exactly those two colours, and crossing 07:00 or 19:00
may schedule a silent reinstall about once a day. None of this is measured: the phase-4
device checklist is the only thing that can settle it, and it is unrun.

## Things worth knowing before you change something

- `--app-height` and `--viewport-top` are the visual viewport's height and offset,
  written by `installViewportVars()`. `100vh` is wrong in Safari; do not reach for it,
  and do not try to compute a keyboard inset by subtracting the two viewports from
  each other — that is the phase-2 bug that clipped the composer off the top of the
  screen (see `docs/web-frontend.md`, "iOS constraints"). Anything that depends on
  the keyboard depends on the field's focus instead.
- All audio plays through one `AudioContext` unlocked by the first tap
  (`lib/audio.ts`). A fresh `new Audio()` is silently dropped on iOS until then.
- `MediaRecorder` must negotiate `audio/mp4` → `audio/aac` → default. Safari has
  no WebM and throws from the constructor if you ask for it.
- A 200 from `POST /api/actions/{id}/confirm` means **queued**. `applied` comes
  only from the `home_action_results` telemetry stream.
- 401 and 403 raise a gate over whatever is on screen; they never redirect. The
  last-known Room stays visible behind them. `GET /api/auth/status` is the exception:
  that route has no network gate and answers a signed-out session with a 200, so its 401
  or 403 is a proxy's and `api()` announces neither — a launch that meets one shows the
  unanswered gate, and a signed-in phone keeps its screen (`docs/web-frontend.md`, "The
  gates").
- The Workshop keeps time by the Redis entry id (`idMs` in `lib/streams.ts`), never
  by `event.timestamp` — the id is the server's clock and has a zone; the ISO stamp
  has neither. The Room still reads `timestamp`, as it always has.
- Telemetry subscriptions are reference-counted (`TelemetrySocket.subscribe` /
  `unsubscribe`): the Door holds `home_action_results` for the app's lifetime and the
  Workshop holds all eight only while it is up. Always pair them.
- The causal thread (`lib/trace.ts`) is a client-side heuristic over one page per
  stream and a ±10-minute window; solid links are id joins, dashed links are
  adjacency in time, and the footnote says exactly what was searched — including how
  many streams could not be read back far enough. It never says "not caused by".
