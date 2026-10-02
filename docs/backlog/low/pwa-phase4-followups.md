# PWA phase 4 follow-ups

**Priority:** low
**Source:** `docs/superpowers/plans/2026-09-16-pwa-phase4-install-and-offline.md` — the
decisions it took knowingly, and what the worker, the update note and the two new gates
found while being built and reviewed. The architecture they refer to is
`docs/web-frontend.md`, "The service worker" and "The gates".

Every claim in §1 to §12 was re-checked against the tree at `c49e66d`, and every byte
figure names the commit it was measured at; §13 and §14 came out of task 12b's review
round and were checked at `13648ec`, except the fixtures check in §13's acceptance, added
in that task's third fix round and checked at `2489885`. The branch's final fix wave
changed the worker under §1, §2, §5 and §14, which were re-checked at `0972685`, its last
change to the code; filed §15 and §16; and closed the entry that was §13, `sw.js`'s
comments quoting byte figures from an earlier build, by rewording them before the first
deploy.
Where the plan's task 13 list turned out to be wrong, this file says what the code does,
not what the plan expected; "Where the plan was stale" at the end is the index.

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

One condition comes with the worker, and it fails silently: the worker keeps nothing it
was not written to keep, and no keep-list exists. `activate` deletes every cache on the
origin that is not `alfred-shell-<VERSION>` (`caches` is scoped to the origin, not the
worker). Inside that cache, every collection deletes every entry that neither install's
record nor the cached shell names, itself or through its stylesheet;
`src/test/sw.test.ts` pins that with "deletes an API response nothing should ever have
cached". A collection runs at activate, which is whenever `sw.js`'s bytes change, phase
5's own change included, and after every refresh that publishes a shell, which is most
online navigations. So a cache of phase 5's own — notification media, a send outbox —
goes through code written a phase earlier, with nothing in the failure to point at it:
at the next activate if it has a name of its own, and at the next collection, usually
the next online navigation's, if it lives inside `alfred-shell-<VERSION>`. IndexedDB is
outside both.

**Acceptance:** phase 5 builds the four surfaces from the handoff's copy as it stands,
and either stores nothing in Cache Storage, or adds a keep-list — to `purge()` for its
cache's name, and to `collect()` for its entries if it shares the shell cache — with a
test in `src/test/sw.test.ts` that its cache survives an activate and a refresh.

## 2. The foreground update check has no floor, and downloads all of `sw.js` each time

`installServiceWorker` (`web/src/lib/sw.ts:225-234`) calls `registration.update()` on
every return to the foreground, with no minimum interval. An app switched to and from ten
times a minute checks ten times. Each check is a full download. `sw.js` is 71 119 bytes
at `0972685` (24 898 gzipped, and nothing in the house compresses it — a reverse proxy
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

## 5. `refresh()` does not follow a new stylesheet to its fonts

`refresh()` stores what the new shell names and then the shell, so a deploy that changes
a font's hash (an `@fontsource` bump) publishes a stylesheet naming fonts `refresh()`
never fetched. The online page covers most of that gap: the asset branch stores any 200
that is not HTML (`isAsset`), so every face the page renders online after the deploy is
cached at runtime, and every collection keeps it for as long as the cached shell's
stylesheet names it. Offline, the only faces lost are those no online page has rendered
since the deploy — another subset, or another weight. They fall back to the system stack
— legible, and wrong — and only until the next install records them. Measured in
Chromium after a deploy that renamed all eight `.woff2`: the page fetched the two faces
it rendered, the next deploy's collection kept both, and with the server down and the
HTTP cache emptied both loaded offline; the other six were never fetched.

This entry used to name a second way, and it is closed. A font install declined is
fetched later by the page through the asset branch, which caches it, and no record names
it; the next activate used to delete it, costing a re-download after each activate. Since
the final fix wave (ruling R27) every collection reads each stylesheet the cached shell
names back out of the cache and keeps the fonts it names, so that font stays for as long
as the stylesheet names it.

**Acceptance:** `refresh()` follows the stylesheet's `.woff2` URLs as install does,
declining rather than failing on a font, with a test in `src/test/sw.test.ts`. It is a
change to `sw.js`, so batch it (see the top of this file).

## 6. No `apple-touch-startup-image`, and nobody has looked at what iOS shows

Decision 11. What iOS builds its launch screen from, and why the manifests have no part in
it, is `docs/web-frontend.md`, "The manifest pair". Shipping `apple-touch-startup-image`
means a per-device media-query matrix: a dozen checked-in PNGs that go stale with every
new iPhone size. So the first step is an observation, not a fix: install the app in each
theme and write down what the launch screen is — the icon on a plain ground, a white
flash, a stretched screenshot. The phase-4 device checklist has the step. Only then is the
matrix worth pricing against a launch screen nobody minds. The two manifests are
unaffected either way: they are Chromium's, and `background_color` is what Android
composes from.

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

A page loaded while the kill switch was deployed holds a registration that no longer
exists, so its foreground `registration.update()` rejects with `InvalidStateError`, and
it takes the restored worker only at its next launch — and an installed PWA can stay open
for days. What it logs meanwhile is `docs/web-frontend.md`, "The kill switch".

The fix is for `lib/sw.ts` to register again when the foreground `update()` rejects with
`InvalidStateError`, so an open page takes the restored worker at its next foreground. It
has a cost: while the switch is still deployed, every foreground would install, run and
unregister a fresh copy of it. That is bounded and does not loop, but it is a new path into
install logic that took several review rounds to get right.

**Acceptance:** the re-registration, with tests in `src/lib/sw.test.ts`, and its own
Chromium and device measurements of both cases: the switch still deployed, and the switch
reverted.

## 13. A proxy's 403 still raises the denied gate on every route but the status read

`api()` (`web/src/lib/api.ts`) announces `denied` for every 403, and has since phase 1
(`a630156`). Rulings R17 and R18 exempted one route, the status read, because no 403 or 401
there is ever the house's. The Room's reads are no different: none of them sits behind the
network gate either. So mid-session, a Cloudflare challenge served as a 403 on every
`/api/*` raises `DeniedGate` over the Room. Its "not from here" is wrong: the house refused
nothing, and the phone's network is not the problem.

Measured in Chromium (headless shell 153) on the task-12b rig, built from `13648ec`. With
the Room up, every `/api/*` was made to answer a challenge-shaped 403: the header
`cf-mitigated: challenge`, and a "Just a moment..." page. A return to the foreground then
raised `DeniedGate` over the Room 5 ms later. It came from the Room's own reads: the
overview, the four stream reads and the pending actions. The status read met the same 403
in that refetch, and R17 exempts it.

In `core/` the house sends a 403 from two places, and only two:

- **The network gate.** `require_trusted_network` (`core/channels/web_server.py:241-267`)
  answers `{"detail": "Access restricted to trusted networks"}` for a peer that is not an
  IP address. For an untrusted IP address the detail is
  `Access restricted to trusted networks: <ip> is not trusted.`, and an authenticated
  caller gets a sentence of guidance after it. The gate guards seven routes:
  - Five through `_CREDENTIAL_GATES` (`web_server.py:441`, the network gate before the
    session gate):
    - `PUT` and `DELETE /api/integrations/{name}/credentials` (`:660-662`, `:680-682`);
    - `POST /api/voice/enroll` (`:896-898`);
    - `POST` and `DELETE /api/devices/register` (`:915-917`, `:935-937`).
  - Two through `_registration_gate` (`core/identity/auth_routes.py:420-445`), when no
    pairing code is presented: `POST /api/auth/register/begin` (`:453-457`) and
    `POST /api/auth/register/complete` (`:497-500`).
- **The pairing check.** `_registration_gate` itself answers
  `{"detail": "Invalid or expired pairing code"}` (`_PAIRING_INVALID_DETAIL`,
  `auth_routes.py:67`, raised at `:440` and `:442`) for a malformed or wrong
  `X-Pairing-Code`. That refusal is the house's own, but it says nothing about the
  network. The PWA sends no `X-Pairing-Code` today, so this 403 cannot arrive yet.

**Acceptance:** `api()` announces `denied` only for the network gate's own 403, decided in
one of two ways:

- **By the body.** The 403's `detail` starts with
  `Access restricted to trusted networks`. That is the house's own sentence, so the
  client keeps one string in step with `require_trusted_network`, and a route that gains
  or loses the gate needs no change. A test on each side pins the prefix. The client's
  test reads `core/channels/web_server.py`, as `sw-server-routes.test.ts` does, and
  checks the prefix and both `NETWORK_GATE_DETAIL*` fixtures (`web/src/test/fixtures.ts`)
  against `require_trusted_network`'s sentences.
- **By the route.** An allowlist of the seven routes above, by method and path. There is
  nothing to parse. But the list has to follow every route that gains or loses the gate,
  and a proxy's 403 on one of those seven routes is still announced.

Either way, two things follow:

- `SetupGate` returns without a word on any 403 (`web/src/gates/SetupGate.tsx:144-146`,
  `:162`, `:194`), on the promise that the denied gate has already said it. It must
  swallow only the 403s `api()` announced, or a proxy's 403 there says nothing at all.
- The pairing-code 403 gets a decision on what it shows, before any client sends the
  header.

Tests: `api.test.ts` pins both halves — the network gate's 403 announced, and a
challenge's 403 on a Room read not announced. `AuthGate.test.tsx` shows that a background
Room read meeting a challenge raises no denied gate. The mid-session challenge above is
re-measured in Chromium, and shows the Room with no denied gate.

## 14. `Try again` cannot get past a Cloudflare challenge

**Unmeasured.** This is reasoned from the code and from Cloudflare's documentation. No
device has met a challenge. The task-12b rig's challenge reproduces only the response's
shape, not a challenge's script or the clearance cookie a solved challenge leaves.

Cloudflare documents that a challenge page carries `cf-mitigated: challenge`, whatever its
type. It also documents that the mechanism "fails when the browser expects a non-HTML
response, such as an AJAX or XHR (fetch) request": a challenge is solved by a page the
browser renders. Every way the unanswered gate asks again is a `fetch()` of
`/api/auth/status`: its `Try again`, its re-ask on each return to the foreground, and
react-query's own refetches. Each one meets the same challenge, so the gate stays up for as
long as the challenge stands. Its button keeps working and changes nothing.

Only a navigation shows the challenge. The worker's `navigate()`
(`web/public/sw.js:1073-1121`) passes any answer through, and serves the cached shell only
when the fetch rejects or runs out of time. So the challenge page reaches the screen, and
once it is solved it reloads the app with the clearance cookie set. On iOS, in the
installed app, that navigation is a cold launch: coming back through the app switcher
resumes the page where it was, and a home-screen app has no reload control. Mid-session,
the same challenge raises `DeniedGate` over the Room (§13). Dismissing it leaves the Room,
whose next read raises it again.

**Acceptance:** first, the measurement. Force a challenge on the app's hostname, behind the
real proxy (a WAF custom rule with the Managed Challenge action, say). Then record on the
device what `Try again` does, what a return to the foreground does, and what a cold launch
shows, as a step in the phase-4 device checklist. If `Try again` cannot pass the challenge:

- `ApiError` keeps the response's `cf-mitigated` header.
- When the status read's failure carries `cf-mitigated: challenge`, the unanswered gate's
  `Try again` reloads the page (`location.reload()`) instead of asking again. The browser
  then renders the challenge, and returns to the app cleared.
- Tests in `AuthGate.test.tsx` pin the reload, and a Chromium measurement shows it.

Any copy that names the challenge is the owner's to sign off.

## 15. Four places still dim by `opacity`

`docs/web-frontend.md`, "The rules this phase's reviews kept enforcing", makes **dim by
token, never by `opacity`** house style, and the phase-4 plan's decision 1 inherits it.
Its reason is that a composited opacity is invisible to `src/test/contrast.ts`. Four
places in `web/src` break it, all of them older than the rule, and `contrast.test.ts`
measures none of them. Handoff paths are under
`docs/design/2026-09-04-pwa-client-handoff/`.

- **The Door's offline track** (`web/src/door/SlideToConfirm.tsx:149`):
  `opacity: disabled ? 0.5 : 1`, phase 1 (`a630156`). The handoff names the state and
  gives no value: "disabled/greyed" (`README.md:172`,
  `Alfred Design System.dc.html:159`). The `0.5` is the phase-1b plan's own
  (`docs/superpowers/plans/2026-09-07-pwa-phase1b-room-and-door.md:8757`).
- **A tombstone** (`web/src/room/rows/Tombstone.tsx:13`): `opacity-80`, phase 1. The
  handoff specifies it: "opacity .8" (`README.md:142`, `Alfred.dc.html:89`).
- **An unsent bubble** (`web/src/room/rows/YouBubble.tsx:15`):
  `opacity: unsent ? 0.6 : 1`, over the bubble's text and its
  `not sent · will retry when connected` line, phase 1. The handoff specifies it:
  "Unsent (offline): opacity .6" (`README.md:139`; `Alfred.dc.html:728`, `:756`).
- **`All streams`** (`web/src/workshop/ActivityBench.tsx:250`):
  `opacity: activity.solo === null ? 0.4 : 1`, phase 2. The handoff specifies it:
  "opacity .4 when no filter" (`README.md:182`). Already filed, with the button's
  `disabled`, as `docs/backlog/low/pwa-phase3-followups.md` §21, which called it the one
  place in the tree that breaks the rule.

Every other `opacity` in `web/src` is motion or a reset, which the rule does not cover:
`index.css`'s `fade`, `fade-out` and `breathe` keyframes, the slide hint's travel fade,
and the `opacity: 1` reset in `index.css`. All four above are inside the rule's scope,
which is anything a reader must read to trust the screen: a tombstone's struck title and
its `expired … · not done` meta are state words, the unsent bubble's line says what
became of a message, and the track and `All streams` are controls.

The fixes do not cost the same. The track's `0.5` is an implementer's value for
"greyed", so dimming it by token needs no deviation. The other three are the handoff's
own values, so dimming any of them by token is a deviation from the handoff, with its
own deviation row and the owner's sign-off, as phase 3's spent one-shot was for the
handoff's `.55`.

**Acceptance:** the track dims by token, with its pair restated in `src/test/contrast.ts`
and measured in `contrast.test.ts`. For the tombstone, the unsent bubble and
`All streams`, the owner rules on each, and each one dimmed by token gets a deviation row
and a measured pair. `All streams`' `disabled` stays §21's.

## 16. A code-split build would lose its lazy chunks offline

A collection keeps what install's record names, what the cached shell names, and the
fonts that shell's stylesheet names, and deletes every other runtime entry on the premise
that it can be fetched again. That holds only online. A chunk the app loads with a
dynamic `import()` is named by none of the three — the shell's HTML names the entry chunk
and the stylesheet, and the record holds what install fetched from it — so the asset
branch would cache it on first use and the next collection, usually the next online
navigation's, would delete it; offline, the view behind it would then fail to load. Vite
emits no lazy chunk today: the build is one entry chunk and one stylesheet, and nothing
in `web/src` calls `import()`.

**Acceptance:** before the first dynamic `import()` lands, the worker learns the lazy
chunks a build emits, and install, `refresh()` and the keep-set treat them as they treat
the stylesheet's fonts, with a test in `src/test/sw.test.ts` that a lazy chunk survives a
collection. It is a change to `sw.js`, so batch it (see the top of this file).

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
4. **Item 5's "one re-download per deploy".** It was per activate, which is per change to
   `sw.js`, and since the final fix wave it is once: a declined font is fetched on first
   use and kept for as long as the cached shell's stylesheet names it. `refresh()` still
   misses the fonts a newer stylesheet names until the page asks for them (§5).
5. **Item 8's captive portal on `SignInGate`.** Replaced by the unanswered gate (§9).
6. **The docs step's captive portal as the reason for `isShell`.** Over https a portal
   cannot intercept, and a portal's page is 200 `text/html`, which `isShell` accepts. It is
   refused because it names no hashed asset. `isShell` stops an error page carrying the
   app's HTML, and a 200 that is not HTML.
7. **The docs step's keep-set "the sentinel".** It is the record **and** what the cached
   shell names, or a refresh's newer shell would lose its assets — and since the final
   fix wave the fonts that shell's stylesheet names as well.
8. **The kill switch's "rebuild, deploy" and "restore `sw.js`".** It is fired by committing
   the copy alone through a normal pull request, and undone by reverting that commit.
9. **The manifest pair's "on iOS it is `background_color` this really fixes".** Corrected
   in task 2 already: iOS's launch screen comes from `apple-touch-startup-image`, and the
   swap may be inert there.
10. **The checklist's update step.** A deploy with a visible change shows no note; the
    tester has to change `sw.js`'s bytes to see it.
11. **The checklist's deep link "from a previous notification".** iOS opens no link into a
    home-screen app, so the step runs in a Safari tab.
12. **Decision 6 and the docs step: a phase-5 cache kept by living inside
    `alfred-shell-<VERSION>`.** Every collection deletes every entry there that neither
    the record nor the cached shell names, so living there keeps nothing. The loss comes
    at the next activate for a cache with a name of its own, and since the final fix wave
    at the next collection for an entry inside the shell cache: usually the next online
    navigation's (§1).
