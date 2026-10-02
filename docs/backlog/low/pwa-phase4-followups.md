# PWA phase 4 follow-ups

**Priority:** low
**Source:** `docs/superpowers/plans/2026-09-16-pwa-phase4-install-and-offline.md` — the
decisions it took knowingly, and what the worker, the update note and the two new gates
found while being built and reviewed. The architecture they refer to is
`docs/web-frontend.md`, "The service worker" and "The gates".

Every claim below was re-checked against the tree at `c49e66d`, and every byte figure
names that commit. Where the plan's task 13 list turned out to be wrong, this file says
what the code does, not what the plan expected; "Where the plan was stale" at the end is
the index.

One cost runs under several entries, so it is stated once here: **any byte change to
`web/public/sw.js` — a comment included — installs a new worker on every phone** at its
next update check, and puts the update note in front of everyone who has the app open.
A fix that touches `sw.js` is never free, and the cheap ones are worth batching.

## 1. The install rail is unbuilt, and phase 5 owns all of it

Decision 6: spec §8 puts the "install-and-enable sequence" in phase 5 with Web Push, and
every step of the six-step rail exists to reach the last one, *Allow in iOS*. Shipping
the first four with nothing behind them would be a control that pretends. So phase 4
built none of the handoff's four surfaces for it, and **the copy is final in all four**:

| Surface | Handoff |
|---|---|
| The `install` gate | `Alfred.dc.html:669` |
| The `standalone` gate | `Alfred.dc.html:670` |
| The home-screen prompt row (`promptItem`) | `Alfred.dc.html:751` |
| System › Reach | `Alfred.dc.html:385-397` |

What phase 4 hands over is an app that the rail's gesture installs correctly — the icon
set, `display: standalone`, the standalone metas in `index.html` — and a worker for the
`push` and `notificationclick` handlers to go into. Phase 5 also owes VAPID, the push
adapter and the subscription's registration.

One condition comes with the worker, and it fails silently. `activate` deletes every
cache on the origin that is not `alfred-shell-<VERSION>`, and `caches` is scoped to the
origin, not the worker. So any cache phase 5 wants for itself — notification media, a
send outbox — has to live inside `alfred-shell-<VERSION>` or be named in a keep-list in
`purge()`. Otherwise the next activate drops it, through a line written a phase earlier,
with nothing in the failure to point at it.

**Acceptance:** phase 5 builds the four surfaces from the handoff's copy as it stands,
and either stores nothing in Cache Storage or adds a keep-list to `purge()` with a test
in `src/test/sw.test.ts` proving its cache survives an activate.

## 2. The foreground update check has no floor, and downloads all of `sw.js` each time

`installServiceWorker` (`web/src/lib/sw.ts:206-215`) calls `registration.update()` on
every return to the foreground, with no minimum interval. An app switched to and from ten
times a minute checks ten times. Each check is a full download. `sw.js` is 64 324 bytes
at `c49e66d` (22 888 gzipped, and nothing in the house compresses it — a reverse proxy
may), and `mount_spa` answers the revalidation with a 200 and the whole body because
`FileResponse` ignores `If-None-Match` and `If-Modified-Since` (`docs/web-frontend.md`,
"Build / Serve / Container").

Harmless on a phone today, and invisible to the user. It is the first thing to fix if it
shows up in the logs or on a metered connection.

**Acceptance:** either `lib/sw.ts` skips a check when the last one is under a minute old,
with a test in `src/lib/sw.test.ts`; or the SPA fallback answers a matching validator with
a 304, with a test in `tests/core/channels/` — ideally both. Neither touches `sw.js`.

## 3. The update note is visible only in the Room

`UpdateNote` sits in the Room's header (`web/src/room/Room.tsx:123-124`, under
`OfflineNote`). The Workshop layer, the Door and every gate cover it, so a user who lives
in the Workshop does not see that a changed `sw.js` is waiting until they come back to
the Room. Accepted when the note was built: the Room is where the app opens, and the note
stays up for as long as a worker waits. The handoff has no design for an update anywhere
(deviation 3), so there is no second place to copy.

**Acceptance:** if the device checklist finds the note undiscoverable, a decision between
a line in the Workshop's header and a row in System › Maintenance, made with the owner,
and built from that design.

## 4. The precache and the live cache are the same cache

While `VERSION` is unchanged, a new worker installs into the cache the old worker is
still serving from: the cache name is the same. From the end of its install to its
activate, `alfred-shell-<VERSION>` therefore holds the **new** worker's shell while the
**old** worker answers the page. An offline navigation in that window boots that shell
under the old worker.

The window lasts as long as the new worker waits, and it never calls `skipWaiting()`, so
on an app that stays open it can be days. That is longer than the plan expected, but the
result is self-consistent: install writes the assets before the shell that names them, so
the shell an offline navigation finds always has its assets. And it is usually the shell
of the deploy the online app is already on.

**Acceptance:** none planned. A staging cache with a copy at activate would close it, and
that is more machinery than a self-consistent window is worth. Revisit if an offline
launch is ever reported booting a build the online app is not on, in a way that matters.

## 5. Two ways fonts drop out of the offline copy

- **A font install declined is runtime-cached and then pruned.** Install skips a font that
  fails rather than failing the install. The page later fetches it through the asset
  branch, which caches it — but it is in no record, and a shell names only the entry
  chunk and the stylesheet, so the next activate deletes it. The cost is one re-download
  after each activate, and activate runs only when `sw.js`'s bytes change, not on every
  deploy.
- **`refresh()` does not follow the stylesheet to its fonts.** It stores what the new
  shell names and then the shell. A deploy that changes a font's hash (an `@fontsource`
  bump) leaves the offline copy with a stylesheet naming fonts it does not hold. Offline,
  those faces fall back to the system stack — legible, and wrong — until the next install
  precaches them.

**Acceptance:** `refresh()` follows the stylesheet's `.woff2` URLs as install does,
declining rather than failing on a font, and install writes into the record any font
already cached at runtime — each with a test in `src/test/sw.test.ts`. Both are changes
to `sw.js`, so batch them (see the top of this file).

## 6. No `apple-touch-startup-image`, and nobody has looked at what iOS shows

Decision 11. `apple-touch-startup-image` is the only launch-screen mechanism iOS has, and
iOS ignores the manifest's icons outright; the per-device media-query matrix it needs is a
dozen checked-in PNGs that go stale with every new iPhone size. So the first step is an
observation, not a fix: install the app in each theme and write down what the launch
screen is — the icon on a plain ground, a white flash, a stretched screenshot. The phase-4
device checklist has the step. Only then is the matrix worth pricing against a launch
screen nobody minds. The two manifests are unaffected either way: they are Chromium's, and
`background_color` is what Android composes from.

**Acceptance:** the checklist's launch-screen observation is recorded here; then either a
decision not to ship the matrix, or the matrix with a test pinning every size it claims.

## 7. The two manifests are a copy, kept honest by a test

`manifest.json` and `manifest-light.json` differ in `theme_color` and `background_color`
only, and `src/test/manifest.test.ts` ("differs between the two files in nothing but those
two colours") is what keeps them so. A `vite build` hook that emitted the light one from
the dark one would remove the copy. It would also be the first build-time codegen in this
tree, for two 32-line files a test already holds together.

**Acceptance:** revisited only if a third manifest is ever wanted.

## 8. A stopped house behind the reverse proxy shows the proxy's error page

A navigation is answered by whatever the network answers. The cached shell is served only
when the fetch rejects or does not answer inside `TIMEOUT_MS` (decision 8), so a 502 is
passed through. A stopped house behind the reverse proxy is exactly that: the proxy
answers at once with its own 502 page. The app never boots, so the unanswered gate — which
says the right thing — never rises. Reasoned from the code and decision 8, not measured.

The gate is reached on the routes where the network does not answer for the house: no
route at all, a refused connection (about a second, with the read's one retry), a hang
(about 20 s, §9), or the house's own 500 to the status read.

**Acceptance:** the device checklist records what a phone shows with the house stopped
behind the proxy. If it is the proxy's page, a decision with the owner: either keep
decision 8 as it stands, or have the navigation branch serve the cached shell for a 502,
503 or 504 that is not `isShell` — with a test, and a change to `sw.js`.

## 9. What the unanswered gate leaves open

Ruling R16 replaced the plan's task 13 item 8, which accepted a phone on a captive portal
seeing `SignInGate`. With the phone online, no failure of the auth read now reaches sign-in
or setup — they follow only the house's own answer. Three things are left:

- **A session store that is down reads as signed out, by the house's own answer.**
  `AuthCookieMiddleware` treats a failed session lookup as "not authenticated"
  (`core/identity/auth_middleware.py:39-40`), so `GET /api/auth/status` answers 200 with
  `authenticated: false`, and `SignInGate` says `signed out` when the truth is that the
  house could not read who you are. The client is right to believe the answer; the answer
  is wrong.
- **The title is loose for the house's own 500.** `The house is not answering.` is exact
  for no route, a refusal and a hang, and approximate for a 500, when the house did answer
  and the answer was an error. The copy is invented (deviation 12), and the owner signs it
  off.
- **Two deadlines back to back on a hang.** A house that accepts connections and never
  answers costs the navigation's 10 s and then the auth read's 10 s, so the gate rises about
  20 s after the tap. Each deadline is right on its own; the page cannot know the
  navigation already waited.

**Acceptance:** the status route (or the middleware) answers 503 when the session lookup
fails, with a test in `tests/core/identity/`, so the client's existing mapping shows the
unanswered gate; the owner rules on the title; the 20 s is measured on the device and
revisited only if it reads as a hang.

## 10. Nothing tests that the built `dist/sw.js` exists

`tests/core/channels/test_spa_ci.py` asserts `/` and that the auth routes are not shadowed,
and nothing else. A build that dropped `public/sw.js` — or a fallback that answered it
with `index.html` — would pass CI, and that regression is the one that silently leaves
every phone on its old worker: the browser refuses an update served as HTML and keeps what
it has. The page reports a rejected registration with `console.error`, on a phone with no
worker yet, and nowhere a CI run can see it.

**Acceptance:** `test_spa_ci.py` asserts that `/sw.js` and `/sw.kill.js` answer 200 with a
JavaScript content type, not `text/html`.

## 11. Node's `DEP0205` warning on every build and test run

`module.register() is deprecated` prints on every `vitest run` and `vite build` (measured
on Node v26.8.2). It comes from `@tailwindcss/node@4.3.0`, whose `dist/index.mjs` calls
`module.register(…esm-cache-loader)` on load, when Vite loads `vite.config.ts`.
`@tailwindcss/vite` 4.3.0 pins it, and its own `tailwindcss` compiler, at exactly 4.3.0
through `web/package-lock.json` — although `web/package.json` asks for
`"tailwindcss": "^4.3.3"`, the plugin compiles with its own. Upstream:
tailwindlabs/tailwindcss#19893, closed by PR #20028; `@tailwindcss/node` 4.3.1, 4.3.2 and
4.3.3 call `module.registerHooks()` where it exists.

**Acceptance:** `@tailwindcss/vite` bumped to `^4.3.1` or later, with the warning gone from
`npm run build`. The plugin's compiler moves with it and can change the built CSS, which
shifts the asset hashes and the byte figures `web/README.md` quotes at `c49e66d` — those
are dated, so they stay true, but re-measure them in the same change if the docs should
describe the new build.

## 12. A page that lived through the kill switch never re-registers

After the kill switch, every page loaded while it was deployed holds a registration that
no longer exists, and `registration.update()` on each return to the foreground rejects
with `InvalidStateError`. That costs one `console.warn` per foreground per open page while
online, and nothing on screen. But such a page takes the restored worker only at its next
launch, and an installed PWA can stay open for days.

The fix is for `lib/sw.ts` to register again when the foreground `update()` rejects with
`InvalidStateError`, so an open page takes the restored worker at its next foreground. It
has a cost: while the switch is still deployed, every foreground would install, run and
unregister a fresh copy of it. That is bounded and does not loop, but it is a new path into
install logic that took several review rounds to get right.

**Acceptance:** the re-registration, with tests in `src/lib/sw.test.ts`, and its own
Chromium and device measurements of both cases: the switch still deployed, and the switch
reverted.

## 13. `sw.js`'s comments quote byte figures from an earlier build

The comments at `web/public/sw.js:64`, `:85`, `:162`, `:166`, `:464` and `:901` say "this
build's 414 kB", "about 415 kB" and "482 kB". At `c49e66d` the entry chunk is 418 537 bytes
and the two files a shell names come to 486 880. The arguments still hold (the ceiling
check is "below 3.5 kB/s" either way), and `web/README.md` dates its own figures instead.

**Acceptance:** reworded to name the build they were measured on — and only alongside a
real change to `sw.js`, never as a change of its own (see the top of this file).

---

## Where the plan was stale

Task 13's list in `docs/superpowers/plans/2026-09-16-pwa-phase4-install-and-offline.md`
was written before tasks 1 to 12 and their fix rounds. Each claim below is corrected in
the plan in place, marked *(corrected in task 13)*; this is the index.

1. **Decision 7: "a build that swaps itself".** A waiting worker exists only when
   `/sw.js`'s own bytes change, so `SKIP_WAITING` carries a changed worker, not a deploy.
   An ordinary deploy reaches an open app at its next navigation, network-first.
2. **Item 2's "the file is ~4 kB".** It is 64 324 bytes at `c49e66d`, downloaded whole on
   every check (§2).
3. **Item 4's "a window of seconds".** The window lasts as long as the new worker waits,
   which can be days (§4).
4. **Item 5's "one re-download per deploy".** It is per activate, which is per change to
   `sw.js`; and `refresh()` misses fonts a second way (§5).
5. **Item 8's captive portal on `SignInGate`.** Replaced by the unanswered gate (§9).
6. **The docs step's captive portal as the reason for `isShell`.** Over https a portal
   cannot intercept, and a portal's page is 200 `text/html`, which `isShell` accepts. It is
   refused because it names no hashed asset. `isShell` stops an error page carrying the
   app's HTML, and a 200 that is not HTML.
7. **The docs step's keep-set "the sentinel".** It is the record **and** what the cached
   shell names, or a refresh's newer shell would lose its assets.
8. **The kill switch's "rebuild, deploy" and "restore `sw.js`".** It is fired by committing
   the copy alone through a normal pull request, and undone by reverting that commit.
9. **The manifest pair's "on iOS it is `background_color` this really fixes".** Corrected
   in task 2 already: iOS's launch screen comes from `apple-touch-startup-image`, and the
   swap may be inert there.
10. **The checklist's update step.** A deploy with a visible change shows no note; the
    tester has to change `sw.js`'s bytes to see it.
11. **The checklist's deep link "from a previous notification".** iOS opens no link into a
    home-screen app, so the step runs in a Safari tab.
