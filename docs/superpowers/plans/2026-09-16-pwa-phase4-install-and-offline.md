# PWA Phase 4 — Install and Offline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 4 of spec §8 — *PWA: manifest, icons, service worker, offline states*. After this phase the client installs to the iOS home screen with a correct icon, launches with no network at all, says something honest while it has read nothing, and can take a new build without being reinstalled. Gate: *Installs and behaves as native*.

**Architecture:** One hand-written service worker at `web/public/sw.js`, ~110 lines, which precaches the app shell (`index.html` plus every `/assets/…` the HTML names plus the `woff2` its stylesheet names) into a versioned cache, purges every other cache on activate, serves navigations network-first and `/assets/*` cache-first, and **never touches `/api/*`, `/health` or the sockets at all**. Its page-side half is `web/src/lib/sw.ts`: registers in production, unregisters in dev, re-checks for an update on every return to the foreground, and exposes the switch that takes a waiting build. A second, checked-in worker (`web/public/sw.kill.js`) is the kill switch. Under all of it, two small pieces of honesty: the manifest now follows the theme through a link swap, and `AuthGate` grows a gate for the one state it could not previously express — *the app is up, the phone has no network, and nothing has been read*.

**Tech Stack:** Unchanged. Vite 8 · React 19 · TypeScript strict · Tailwind v4 · TanStack Query 5 · react-router 7 · Vitest 4 + Testing Library + jsdom · ESLint 10. **No new npm dependencies. No backend changes.** Icon PNGs are generated once by `rsvg-convert` (or ImageMagick) and checked in; neither tool is needed by CI or by the container build.

**Spec:** `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md` — §3.4 (asset cache headers, already landed), §4.1/§4.2/§4.3/§4.9/§4.9b/§4.10/§4.12 (the iOS constraints this phase owns), §6.4 (the six-step install→permission rail, and why only its foundations land here), §7 ("Service worker policy"), §8 (the phase-4 gate).
**Design handoff:** `docs/design/2026-09-04-pwa-client-handoff/` — `Alfred.dc.html:530` (the home-screen tile: accent `A` on `#25221F`), `Alfred.dc.html:669`/`:670` (the install and standalone gates, **phase 5**), `README.md:12` ("All copy in the prototypes is final microcopy and should be used verbatim"). The handoff contains **no** service worker, manifest, icon-asset, splash, cold-offline or update copy at all — verified by grep across all six files. Every string this phase invents is listed in the deviations table below.
**Predecessors:** `…-pwa-phase1a-shell-and-gates.md`, `…-pwa-phase1b-room-and-door.md`, `…-pwa-phase2-workshop-and-activity.md`, `…-pwa-phase3-memory-triggers-system.md`. Everything they export is used here exactly as spelled there.

---

## Product decisions already taken (do not reopen)

1. **Phases 1 to 3's decisions all still hold** — public repo (`alfred.example.com`, `192.168.1.x`, never a real address), 401/403 as gates rather than redirects, the closed status vocabulary, colours from tokens only, `aria-disabled` over `disabled`, dim by token and never by `opacity`, no virtualisation, no new dependencies.

2. **Hand-rolled service worker. Not `vite-plugin-pwa`, not Workbox.** Spec §7 proposes `vite-plugin-pwa`; this plan deviates, deliberately, and this is the decision most worth reading before anything else.

   - **The precache manifest already exists and it is called `index.html`.** Vite content-hashes every asset and writes the entry chunk and the stylesheet into the HTML as `/assets/index-<hash>.js` and `/assets/index-<hash>.css` (verified against a real `npm run build`: `dist/index.html` carries exactly those two `/assets/` references). A worker that fetches `/index.html` at install time and reads those two URLs out of it has the same manifest Workbox would have injected, without a build step, a plugin, or a generated file in the tree.
   - **Constraint: the worker must be readable.** This plan's non-negotiable requirements are a versioned cache with an explicit activate-time purge, a user-triggerable update, and a documented kill switch. All three are properties a reviewer has to be able to *read* in the shipped bytes. Workbox's output is a generated bundle with an injected manifest array; `cleanupOutdatedCaches()` is a call, not a policy you can point at in review.
   - **Constraint: every behaviour is pinned by a test.** The house has no component library, no icon package, no toast library and no markdown renderer, by policy — and 1 503 tests over 70 files. A generated worker can only be tested by testing Workbox. A 110-line file can be evaluated inside a fake `ServiceWorkerGlobalScope` and driven event by event, which is what task 3 builds.
   - **Constraint 2 below is a *negative* requirement**, and negative requirements are exactly what a routing library makes hard to prove. "This worker never calls `respondWith` for `/api/*`" is one assertion against a hand-written file and an argument against a plugin's route table.
   - The cost is real and stated: no automatic precache-on-rebuild, no runtime-caching recipes, no Workbox background sync. None of them is wanted here (see decisions 3 and 4).

3. **The worker caches the app shell and nothing else. `/api/*`, `/health` and the event stream are never given to `respondWith()` at all.** This is stronger than "network-only": the worker does not see those responses, so it cannot store one by accident, and no future edit to a caching branch can reach them. The reason is the client's whole design — *it never states a server fact it has not just read*. **A cached API response is that failure with a longer half-life:** a stale `alive`, a stale `dnd: false`, a stale trigger state, served with no stamp and no way for the reader to know. `/ws` and `/ws/telemetry` are named in the guard even though a WebSocket upgrade never reaches a `fetch` handler in the first place — named so nobody later removes a guard they think is dead, and so nobody adds a *caching* branch they think is needed.

4. **No query-cache persistence.** `persistQueryClient` would make a cold offline launch show the last-known Room, and it is rejected for decision 3's reason: it is an API cache with a longer half-life, wearing react-query's clothes. Phase 4's offline story is the **app shell** — the app opening at all — not the data. What the client shows before any data exists is one honest sentence (task 12), not a reconstruction.

5. **Offline at the query layer is already solved and is not reopened here.** react-query's default `networkMode: "online"` *pauses* rather than fails, the benches derive `read` from `dataUpdatedAt !== 0`, `OfflineNote` and `StatusLine` carry `last true HH:MM`, and the Door refuses to confirm offline. All of that is phases 1 to 3 and stays exactly as it is. Phase 4 adds the layer *below* it: the shell boots from cache, and `AuthGate` — which today shows the presence field and no copy, **for ever**, when the auth read is paused — says which of the three meanings its silence has.

6. **The §6.4 install rail is phase 5's, not this phase's.** Spec §8 lists phase 5's content as "Web Push: VAPID, adapter, registration, service-worker handlers, **install-and-enable sequence**" — the six-step rail is named there by the spec itself, and phase 4's line is "manifest, icons, service worker, offline states". Every step of that rail exists to reach step 6, *Allow in iOS*; shipping steps 1 to 4 with nothing behind them is a control that pretends, which is the defect phase 2's placeholder tabs were and phase 3's absent Reach card avoided. So: **no `install` gate, no `standalone` gate, no Reach prompt row and no System › Reach card in this phase.** What phase 4 owes phase 5 is that the gesture the rail teaches actually produces a correct app — a manifest with a real icon set, `display: standalone`, a `background_color` that matches the theme at install *(corrected in task 2: this said "a splash that matches the theme" — see decision 11; the launch screen on iOS is `apple-touch-startup-image`'s, which this phase does not ship)*, and a worker that phase 5 adds `push` and `notificationclick` handlers to. That list is written down in task 13 so phase 5 can start from it. **One condition task 5 attached to the last item, and it is the kind that fails silently:** `activate` deletes every cache on this origin that is not `alfred-shell-<VERSION>`, and `caches` is scoped to the **origin**, not to the worker. So (a) the purge assumes this origin serves Alfred and nothing else — a second app or a second worker scope behind the same hostname would have its caches deleted on every activate — and (b) any cache phase 5 wants for itself (notification media, a send-outbox) must either live *inside* `alfred-shell-<VERSION>` or be named in a keep-list in `purge()`. Otherwise it is dropped between deploys, by a line written a phase earlier, with nothing in the failure to point at it.

7. **A new worker waits; the user takes it.** `install` never calls `skipWaiting()`. A build that swaps itself under a running conversation loses whatever was on screen. The waiting worker is announced in the Room, and one tap posts `SKIP_WAITING`, which is what makes an update possible **without reinstalling the app** — the requirement that is otherwise very hard to satisfy on iOS. `registration.update()` runs on every return to the foreground (`onVisible`, already in `lib/lifecycle.ts`), because an installed PWA is *opened*, not reloaded, and could otherwise run for weeks on the build it was installed with.

8. **Navigations are network-first.** The cached shell is served on exactly one path: a phone with no network. Anything online gets the deploy that is live right now. This is the single most important brick mitigation, because it means a poisoned cache is invisible to an online phone *and* that the browser's own update check on `/sw.js` keeps running — which is what lets the kill switch land.

9. **The kill switch is a file, not a paragraph** (task 11). `web/public/sw.kill.js` is checked in, tested, and shipped by copying its bytes over `web/public/sw.js`. **Deleting `sw.js` does not work and must never be tried:** `mount_spa` (`core/channels/spa.py:42-49`) answers any unknown path with `index.html` and a 200, so the browser's update check would fetch a page of HTML, reject it on MIME type, and leave the old worker installed and still serving its cache.

10. **Icons are checked-in bytes, not a build step.** Four files (`icon.svg`, `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`) generated once from one source SVG by a command written down in `web/README.md`. No `sharp`, no `svgo`, no CI dependency on `rsvg-convert`, and the container's `webbuild` stage is unchanged. A test reads the PNG bytes directly and asserts the dimensions and the absence of an alpha channel, which is what iOS actually cares about (§4.2). *(Corrected in task 1: a header read alone proved decorative under mutation — it passes on a PNG rendered from any drawing at all — so the shipped test decodes the pixels with `node:zlib` and checks the colours, the census, the mark's reach and its shape. Still no new dependency.)*

11. **No `apple-touch-startup-image`.** The per-device media-query matrix is a dozen more checked-in PNGs that go stale with every new iPhone size, and that is the whole of the reason. *(Corrected in task 2: this decision originally rested on "iOS 15+ composes a launch screen from the manifest's `name`, `background_color` and icon instead", and that is backwards. `apple-touch-startup-image` is the only launch-screen mechanism iOS has — which is why it exists — and iOS ignores the manifest's icons outright, as `web/index.html:22-23` already said in this tree. So the refusal stands on the maintenance cost alone, and task 2's `background_color` work is **not** what gives iOS a themed launch screen. Nobody here has measured what an iPhone does with either; the backlog entry says measure first, then decide.)*

### Deviations from the spec and the handoff (deliberate; flag them in review)

| # | Where | Source says | This plan ships | Why |
|---|---|---|---|---|
| 1 | Service worker | spec §7: "Add `vite-plugin-pwa` (Workbox)" | a 110-line hand-written `public/sw.js` | Decision 2. |
| 2 | §6.4 install rail | six-step rail, `install` + `standalone` gates, copy final at `Alfred.dc.html:669`/`:670` | **not built here** | Decision 6 — spec §8 puts "install-and-enable sequence" in phase 5, and four of six steps with nothing behind them is a control that pretends. |
| 3 | Update prompt | **nothing** — the handoff has no update, reload or new-version copy at all, and no `updatefound` scene | invented: `A newer Alfred is waiting. Nothing is lost; I pick up where we left off.` + a `Reload` text button | An update path the user can trigger without reinstalling is a hard requirement. The second clause is the handoff's own, verbatim from the install gate's foot (`Alfred.dc.html:669`). |
| 4 | Cold offline launch | **nothing** — the handoff's offline scenes are all *a running app that lost its socket*, never a launch with no network | invented: kicker `offline · nothing read yet`, title `No way to the house.`, body `This phone has no connection, so I cannot ask the house who you are. Nothing is lost; I pick up where we left off as soon as there is a signal.` | Phase 3's rule: never say the house is empty before the server has answered, and say *which* of the three meanings the silence has. Modelled line for line on the 403 gate (`DeniedGate.tsx`), whose shape the handoff did fix. |
| 5 | Cold offline gate | — | **no retry button** | With `navigator.onLine === false`, react-query pauses; a `Try again` would queue a paused fetch and show nothing. The gate clears itself when the `online` event fires, which is the same event react-query resumes on. A button that pretends is worse than no button. |
| 6 | `manifest.theme_color` | one static file, currently the dark token in both themes — a phase-1 defect deferred here, noted in `web/README.md` | **two manifests**, `/manifest.json` (dark) and `/manifest-light.json` (light); `applyTheme()` swaps the `<link rel="manifest">` alongside the `theme-color` meta it already rewrites | A manifest is static and the theme is the hour's, so the link points at whichever is true now. *(Corrected in task 2, which found the original claim — "iOS reads the manifest at Add to Home Screen time … it is `background_color`, the launch screen, that this actually fixes" — to be wrong twice over. **A manifest is consumed at install time**, so the most this can ever do is align one install with the theme in force at that moment: it does not follow the theme afterwards, and an install at 22:00 keeps its launch screen at noon for ever. **And on iOS it may do nothing at all** — WebKit fetches at most one manifest per document load and has no observer for a later `href` change, while `main.tsx` is a deferred module, so the swap lands after parsing; the manifest iOS reads may always be the dark one `index.html` ships. iOS's launch screen comes from `apple-touch-startup-image` regardless (decision 11). What is left standing is Chromium, which does observe the link — and the one measured-by-reasoning cost below.)* |
| 6b | — | — | **Chrome may reinstall the WebAPK about once a day** | A consequence of shipping two manifests, not a choice, and new in task 2. Chrome re-reads an observed manifest and its WebAPK update check fingerprints `name`, `short_name`, `start_url`, `scope`, `display`, `orientation`, `theme_color` and `background_color` — and the last two are exactly what differs between the pair. Crossing 07:00 or 19:00 therefore presents a manifest that disagrees with the installed app, which is the condition that schedules an update. Throttled, that is roughly one silent reinstall a day. **Reasoned from Chrome's documented fingerprint, not measured** — there is no Android device in this project and no Android line on any checklist. `id: "/"` is identical in both files, so nothing is orphaned either way. |
| 7 | Icon mark | `Alfred.dc.html:530`: letter `A`, `oklch(0.78 0.12 45)`, `500 30px 'DM Sans'`, on `#25221F` | the same mark drawn as two `<path>` strokes, not `<text>` | A `<text>` icon renders with whatever font the *rasteriser* has. `@fontsource-variable/dm-sans` ships `woff2` only, which neither `rsvg-convert` nor `resvg` can load, and the current `icon.svg` asks for Georgia — so today's icon is already whatever serif the machine had. Geometry renders identically everywhere. |
| 8 | Icon shape | current `icon.svg` has `rx="64"` | full-bleed square, no radius | iOS applies its own squircle mask to `apple-touch-icon`; anything outside the rounded rect is transparent, and iOS composites transparency as **black**, so `rx="64"` puts black slivers in the home-screen tile's corners. §4.2's "opaque, no alpha" is the same requirement said another way. |
| 9 | Maskable icon | spec §4.2: "plus 192/512 maskable for the manifest" | one 512 PNG marked `purpose: "any maskable"`, not a second file | Measured on the geometry in task 1: the glyph's furthest point is 170.5 px from the centre of a 512 canvas *(corrected in task 1: this said **190 px**, which is no corner of the drawing — see the correction under task 1 step 3)* and the maskable safe circle's radius is 204.8 px, so the same art is safe under a mask and correct unmasked. A separate maskable file would be the same bytes with a different name. |
| 10 | Precache scope | Workbox precaches every build output | `index.html` + the `/assets/` URLs the HTML names + the `/assets/*.woff2` the stylesheet names | The build emits 18 font files because `@fontsource/geist-mono` ships `.woff` fallbacks beside its `.woff2`. No browser that can run a service worker asks for a `.woff`. Skipping them saves 80 944 of the 220 380 font bytes; anything missed is still caught cache-first on first use. *(Corrected in task 4, against a real build: this said "20 font files (~800 kB)" and "halves the precache". Measured: 8 `.woff2` = 139 436 B, 10 `.woff` = 80 944 B, so the saving is 37% of the fonts and ~13% of a ~620 kB precache whose largest single item is the 414 kB entry chunk. Of the 14 `@font-face` rules, **6** have no `.woff2` file — Vite inlined those — and **2** have no file at all; "four" is right only for faces with a `.woff` file but no `.woff2`. The stylesheet carries six `url(data:font/woff2;…)` and two `url(data:font/woff;…)` rules.)* *(Corrected again after review: the first correction said "Four subsets have no `.woff2` file at all", which is wrong twice in one clause — wrong count and wrong predicate — in a row whose whole purpose was replacing wrong arithmetic. Measured off `dist/assets/index-*.css`.)* |
| 11 | `favicon.svg`, `icons.svg` | untouched since phase 1a (its decision 3) | **deleted** | `favicon.svg` is the Vite template's purple bolt and `icons.svg` is the outgoing SPA's Bluesky/social sprite sheet. Nothing in `web/src`, `index.html`, `core/` or the manifest references either; both are shipped to every visitor by `mount_spa`. |

---

## Before you start

1. **Worktree and branch.** This plan runs in `~/code/.worktrees/alfred/pwa-phase4-install` on `feat/pwa-phase4-install`, branched from `origin/master` at `3cdbf1c`. Never commit from `~/code/alfred-deploy/alfred` — a merge to `master` deploys to the live box.

```bash
cd ~/code/.worktrees/alfred/pwa-phase4-install
git status --short
git log --oneline -1
```

Expected: no output from `status`, and `3cdbf1c feat(web): PWA client phase 3 — Memory, Triggers and System benches (#244)` — or this plan's own `docs(web): plan PWA phase 4 …` commit above it.

2. **Confirm the client is green before adding to it.** `node_modules` is per-worktree; run `npm ci` first if `eslint: command not found`.

```bash
cd ~/code/.worktrees/alfred/pwa-phase4-install/web
npm run lint && npx vitest run && npm run build
```

Expected: eslint prints nothing, `Test Files  70 passed (70)`, `Tests  1503 passed (1503)`, vite prints `✓ built in …`. A red baseline is master's problem, not yours. **`npx vitest run` from `web/`, never from the repo root.**

3. **Confirm the two backend facts this plan rests on.** Both are on `origin/master` today and neither is changed by this phase.

```bash
cd ~/code/.worktrees/alfred/pwa-phase4-install
sed -n '40,50p' core/channels/spa.py      # the SPA fallback: any unknown path → index.html, 200
sed -n '78,89p' core/channels/spa.py      # SpaCacheMiddleware's three tiers
```

Expected: `mount_spa`'s catch-all returns `dist/{full_path}` if it exists and `index.html` otherwise (this is why decision 9's kill switch must be a file, and why the worker refuses to cache a `text/html` body under an asset URL); and `SpaCacheMiddleware` stamps `/assets/*` immutable, `text/html` `no-store`, and **everything else** `no-cache, must-revalidate`. `/sw.js`, `/manifest.json`, `/manifest-light.json` and the icons all land in that third tier, which is exactly right for a service worker script — **no backend change is needed in this phase, and none may be made.**

4. **Check the icon tooling once, before task 1.** If neither is present, the ImageMagick line in task 1 step 3 is the fallback; if neither exists at all, generate the PNGs on a machine that has one — they are checked-in bytes, not a build step.

```bash
rsvg-convert --version || magick -version
```

5. **Tools you will not have.** This box is **headless** — no compositor, no display. There is no browser to open, no screenshot and no Playwright run. Everything in this plan is verified by Vitest, by reading `web/dist/`, or by a line on the device checklist in task 13.

6. **Test-file and test counts as you go.** Master is at 70 files / 1 503 tests. Each task states the counts it should reach. A different number means a test was skipped or duplicated — find it before moving on.

7. **Secret hygiene before any push** — run the checks from the operator's exposure runbook (`~/code/alfred-deploy/PWA-EXPOSURE-RUNBOOK.md`, §"Secret hygiene", outside this repo). They must print nothing. They are deliberately not quoted here: the patterns contain the literals they hunt for, so writing them into a public tree would commit exactly what they exist to catch.

---

## Conventions

Identical to phases 1 to 3, repeated because they are load-bearing:

- **TDD, always.** Failing test → run it and read the failure → implement → run it green → commit.
- **Conventional commits**, one per task minimum.
- **Imports use the `@/` alias** everywhere except inside `src/lib/`, where siblings are relative (`./format`, `./types`).
- **Named exports** everywhere except `App.tsx`.
- **Colours come from CSS custom properties** — `style={{ color: "var(--accent-text)" }}`. Never a Tailwind palette colour. This phase introduces **no new token and no new colour pair**: `UpdateNote` is `--accent-text` on `--surface`, already measured in `src/test/contrast.test.ts:118`, and its dot is `--accent` on `--surface`, decorative beside text that carries the same fact — exactly as `OfflineNote`'s `--muted` dot is. If a task finds itself reaching for a colour that is not already in `TOKENS`, it has gone wrong.
- **Type comes from the `.t-*` classes.** `UpdateNote` and `OfflineGate` both say something a reader must take on its own to trust the screen, so meta there is `.t-meta-strong`, never `.t-meta`.
- **Safe areas via `env()`, never a literal — and never a keyboard inset.** Measured on the user's own iPhone in standalone: `window.innerHeight === visualViewport.height === 894`, **unchanged when the keyboard opens**; `env(safe-area-inset-top)` is **0**; `env(safe-area-inset-bottom)` is 34. iOS slides the WKWebView itself and that shift is invisible to JS. Do not re-derive any of this, do not subtract one viewport from the other (that was the phase-2 bug), and **any top padding this phase writes must be `calc(env(safe-area-inset-top, 0px) + Npx)`**. Nothing in this phase should need one: `UpdateNote` sits inside the Room's existing header and `OfflineGate` reuses `Gate`, which has no top padding.
- **Every tappable element is ≥44 px tall**; smaller visuals grow their hit area with negative insets (`after:-inset-y-3`).
- **Tests live next to their source.** The two workers live in `web/public/`, which has no `src`-side neighbour, so their tests live in `web/src/test/` beside `index-html.test.ts`, which already tests a file outside `src/` for the same reason.
- **The status vocabulary is closed.** This phase adds no word to it. `offline` is already the handoff's own (`Offline · will send when connected`, `Alfred.dc.html:798`).
- **`self.` everywhere inside a worker.** Every global a worker touches — `self.caches`, `self.fetch`, `self.clients`, `self.registration`, `self.skipWaiting()` — is reached through `self`. It is valid in a real worker and it is the single seam the test harness hangs a fake scope on. A bare `caches` reference is a test that cannot be written.
- **No placeholders, no dead code.**

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `web/public/icon.svg` | The app mark: accent `A` on `#25221F`, drawn as paths, full-bleed 512 square. Source for every PNG | 1 |
| `web/public/apple-touch-icon.png` | 180×180, opaque, no alpha — the iOS home-screen tile (§4.2) | 1 |
| `web/public/icon-192.png`, `web/public/icon-512.png` | manifest raster icons; the 512 doubles as the maskable | 1 |
| `web/public/favicon.svg`, `web/public/icons.svg` | **deleted** — dead since the phase-1 rewrite | 1 |
| `web/src/test/icons.test.ts` | Decodes the PNGs: dimensions, colour type, no transparency, the two token colours, the mark's reach and its shape | 1 |
| `web/public/manifest.json`, `web/public/manifest-light.json` | The two manifests, identical but for the two colours | 2 |
| `web/src/lib/theme.ts` | `applyTheme` swaps the manifest link *(corrected in task 2: this said "Export `THEME_COLOR`"; nothing is exported — see the contract block)* | 2 |
| `web/src/test/contrast.ts` | Gains `srgbHex()`, so a file that cannot hold an `oklch()` restates no literal | 1 |
| `web/src/test/manifest.test.ts` | Both manifests parsed, compared, held to `--bg` through `contrast.ts`, and checked against the files actually in `public/` *(corrected in task 2: "held to `THEME_COLOR`")* | 2 |
| `web/index.html` | `apple-touch-icon` link; the manifest link, which `applyTheme` finds by `rel` *(corrected in task 2: this said the link "keeps its `id`" — it has never had one, and the swap selects on `link[rel="manifest"]`, so task 2 changes no markup at all)* | 1 |
| `web/tsconfig.app.json`, `web/tsconfig.node.json` | `icons.test.ts` reads bytes, so it is checked by the Node project, not the browser one — `sw-server-routes.test.ts` joins it in task 6 | 1, 6 |
| `web/eslint.config.js` | A block for `public/*.js` with the service-worker globals | 3 |
| `web/src/test/sw-harness.ts` | Fake `ServiceWorkerGlobalScope`, fake `CacheStorage`, event dispatch; from task 6 also `fetchRequest`, a settable scope, and `inner` for a file-local function | 3, 5, 6 |
| `web/public/sw.js` | The worker: precache, purge, route, update handshake | 3–8 |
| `web/src/test/sw.test.ts` | Every branch of the worker, through dispatched events — and, from task 6, `isRefused` called directly, which is the only way to assert what it must *not* refuse | 3–8 |
| `web/src/test/sw-server-routes.test.ts` | *(added in task 6's review round)* The worker's refusal list against the routes FastAPI and `mount_spa` really serve; red if `create_app` moves `docs_url` out from under it. Node project, like `icons.test.ts` | 6 |
| `web/public/sw.kill.js` | The kill switch: unregister and drop every cache | 11 |
| `web/src/test/sw-kill.test.ts` | The kill switch, through the same harness | 11 |
| `web/src/lib/sw.ts` | Register, dev-unregister, update-on-visible, `onUpdateReady`, `applyUpdate` | 9 |
| `web/src/main.tsx` | Call `installServiceWorker()` after the render | 9 |
| `web/src/shell/UpdateNote.tsx` | The Room's "a newer Alfred is waiting" note | 10 |
| `web/src/shell/useUpdateReady.ts` | The one-line subscription `UpdateNote` is driven from | 10 |
| `web/src/room/Room.tsx` | Mount `UpdateNote` under `OfflineNote` | 10 |
| `web/src/shell/useOnline.ts` | `navigator.onLine` plus its two events | 12 |
| `web/src/gates/OfflineGate.tsx` | The cold-launch gate | 12 |
| `web/src/gates/AuthGate.tsx` | Raise it when the auth read is paused rather than pending | 12 |
| `web/README.md`, `docs/web-frontend.md`, `docs/backlog/low/pwa-phase4-followups.md`, `docs/superpowers/qa/2026-09-16-pwa-phase4-ios-checklist.md` | Docs, backlog, device QA *(corrected in task 7: `web/README.md` is not a task-13 file. Its "The service worker" section has been rewritten in tasks 4, 5, 6 and 7, each in that task's own commit, because each one falsifies sentences of it — task 13 is the last pass over it, not the only one.)* | 4–7, 13 |

### The contract between tasks (names used exactly as spelled)

```ts
// public/sw.js — not a module; these are file-local and never imported. Listed
// so tasks 4–8 extend the same names task 3 introduces.
const VERSION = "v1";
const CACHE = `alfred-shell-${VERSION}`;   // every cache this origin holds starts "alfred-"
const SHELL = "/index.html";               // the one navigation response, cached under one key
const MANIFEST = "/__shell-manifest";      // install's record of what it cached, for activate to prune against
function scopeOrigin(): string;                  // new URL(self.registration.scope).origin
function isAssetUrl(url: string, base?: string): boolean;  // same-origin AND under /assets/, no %2f
function fetchFresh(url: string): Promise<Response>;      // cache:"reload" + a TIMEOUT_MS AbortSignal
function assetsInHtml(html: string): string[];   // extracts wide, refuses through isAssetUrl
function fontsInCss(css: string, base: string): string[];  // base = the stylesheet's own URL
function isShell(response: Response): boolean;   // ok && content-type is text/html
function isAsset(response: Response): boolean;   // ok && content-type is NOT text/html
function isRefused(request): boolean;            // added in task 6 — the whole of rule 1, in one predicate
async function precache(): Promise<void>;        // install
async function purge(): Promise<void>;           // activate
async function navigate(event): Promise<Response>;   // the EVENT, not the request — it needs waitUntil (corrected in task 7)
async function asset(request, pathname: string): Promise<Response>;
async function refresh(response): Promise<void>;  // added in task 7: publishes a shell only with its assets
async function openCache(): Promise<Cache | undefined>;  // added in task 7: undefined = no usable CacheStorage
async function cached(key): Promise<Response | undefined>;  // added in task 7: a miss and a read failure are one thing
function timed(input, init): Promise<Response>;   // added in task 7: fetchFresh is this plus cache:"reload"

// src/test/sw-harness.ts
export interface FetchRequest { url: string; method: string; mode: string; headers: Headers }  // task 6
export interface RequestSpec { path: string; mode?; method?; origin?; headers? }               // task 6
export function fetchRequest(spec: RequestSpec): FetchRequest;                                 // task 6
export const EXPOSABLE = ["isRefused"] as const;                                                // task 6
export type Exposable = (typeof EXPOSABLE)[number];                                            // task 6
export interface LoadOptions { expose?: readonly Exposable[]; scope?: string }                  // task 6
export interface Harness {
  /** cache name → (absolute URL → Response). The whole of the fake CacheStorage. */
  store: Map<string, Map<string, Response>>;
  /** The cache the loaded worker names, read out of its own VERSION. */
  cache: string;
  /** Program `self.fetch`: exact pathname → the response to answer with. */
  respond(pathname: string, response: Response): void;
  /** Every pathname `self.fetch` was called with, in order. */
  fetched: string[];
  /** Of those, the ones asked for with `{ cache: "reload" }`. */
  reloaded: string[];
  /** Accept the connection and never answer; only the worker's own signal ends it. */
  hang(pathname: string): void;
  /** Make a delete reject: a cache NAME for caches.delete, a pathname for Cache.delete.
      Added in task 5 — the only way a test reaches a worker's per-delete catch. */
  refuseDelete(key: string): void;
  /** The delay of every deadline the worker armed, and how many it cleared. */
  deadlines: number[];
  deadlinesCleared: number;
  /** The worker's own file-local functions, by name. Added in task 6 — a classic
      worker exports nothing, and a refusal's must-NOT-refuse half is invisible
      through a dispatched event, where "refused" and "unhandled" are both undefined. */
  inner: Partial<Record<Exposable, (request: FetchRequest) => boolean>>;   // typed in task 6's review round; the `as` casts went with it
  install(): Promise<void>;
  activate(): Promise<void>;
  /** `undefined` means the worker never called respondWith — i.e. left it to the network. */
  request(spec: RequestSpec): Promise<Response | undefined>;
  message(data: unknown): Promise<void>;
  skipWaitingCalls: number;
  claimCalls: number;
  unregisterCalls: number;
  navigated: string[];
  /** Everything currently in CACHE, as pathnames, sorted. */
  keys(name?: string): string[];
}
export const ORIGIN = "https://alfred.example.com";
export function loadWorker(source: string, options?: LoadOptions): Harness;  // `options` added in task 6
export function html(body: string): Response;   // 200, content-type text/html
export function js(body: string): Response;     // 200, content-type text/javascript
export function css(body: string): Response;    // 200, content-type text/css
export function font(): Response;               // 200, content-type font/woff2

// src/lib/sw.ts
export const SW_URL = "/sw.js";
export interface SwOptions {
  /** Defaults to `import.meta.env.PROD`. A test passes it rather than stubbing env. */
  enabled?: boolean;
  /** Injected: jsdom does not implement navigation, so a test cannot let this run. */
  reload?: () => void;
}
/** Returns the uninstaller for the foreground update check. */
export function installServiceWorker(options?: SwOptions): Promise<() => void>;
export function onUpdateReady(fn: (ready: boolean) => void): () => void;
export function applyUpdate(): void;
export function unregisterAll(): Promise<void>;

// src/shell/useUpdateReady.ts
export function useUpdateReady(): boolean;

// src/shell/useOnline.ts
export function useOnline(): boolean;

// src/shell/UpdateNote.tsx
export interface UpdateNoteProps { ready: boolean; onReload: () => void }
export function UpdateNote(props: UpdateNoteProps): JSX.Element;

// src/gates/OfflineGate.tsx
export function OfflineGate(): JSX.Element;

// src/lib/theme.ts — nothing new is exported.
// (Corrected in task 2: this promised `export const THEME_COLOR: Record<Theme, string>`.
// Exporting it would have made the manifests' only anchor a record that is itself
// anchored to nothing — a retune of `--bg` in index.css would leave the manifests, the
// record and Safari's chrome all on the old colour with the suite green, which was
// demonstrated. `src/test/manifest.test.ts` pins the files to `token(theme, "bg")`
// instead, which `contrast.test.ts` already holds to the stylesheet, so `THEME_COLOR`
// stays private and no module surface grows.)
```

---

## Task 1: The icon set

Today `web/public/` holds four files: `manifest.json`, `icon.svg` (a Georgia `A` in a `rx="64"` rounded rect), `favicon.svg` (the Vite template's purple bolt) and `icons.svg` (the outgoing SPA's Bluesky sprite sheet). Nothing references the last two. `index.html` has no `apple-touch-icon` link at all, so iOS currently screenshots the page for the home-screen tile.

The mark is the handoff's, from the home-screen mockup (`Alfred.dc.html:530`): a capital `A` in `oklch(0.78 0.12 45)` on `#25221F`. In sRGB that accent is **`#F79E75`** — computed from the token in `src/index.css:38`, and written as hex because `rsvg-convert` 2.62 predates `oklch()`.

**Files:**
- Create: `web/src/test/icons.test.ts`
- Modify: `web/src/test/contrast.ts` *(added in task 1: `srgbHex()`, so the icon's hex is converted from `--accent` rather than restated)*
- Modify: `web/tsconfig.app.json`, `web/tsconfig.node.json` *(added in task 1: `icons.test.ts` reads bytes, so the Node project checks it)*
- Modify: `web/public/icon.svg`
- Create: `web/public/apple-touch-icon.png`, `web/public/icon-192.png`, `web/public/icon-512.png`
- Delete: `web/public/favicon.svg`, `web/public/icons.svg`
- Modify: `web/index.html`, `web/src/test/index-html.test.ts`
- Modify: `web/README.md` *(added in task 1: decision 10's regeneration command, which nothing else in the repo carried)*, `core/channels/spa.py` *(a docstring naming the deleted `favicon.svg`; no behaviour)*, `docs/web-frontend.md` *(the unhashed-file list)*

- [ ] **Step 1: Write the failing test**

`web/src/test/icons.test.ts`. A PNG's header is fixed-layout, so the dimensions and the colour type can be read with no dependency at all: bytes 0–7 are the signature, 16–19 the width, 20–23 the height, 24 the bit depth and **25 the colour type**, where `2` is truecolour and `6` is truecolour *with* an alpha channel. §4.2's "opaque, no alpha — iOS does not composite transparency" is byte 25.

```ts
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function png(name: string) {
  const path = fileURLToPath(new URL(`../../public/${name}`, import.meta.url));
  const bytes = readFileSync(path);
  return {
    signature: bytes.subarray(0, 8),
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
    colourType: bytes.readUInt8(25),
  };
}

describe("the icon PNGs", () => {
  it.each([
    ["apple-touch-icon.png", 180],
    ["icon-192.png", 192],
    ["icon-512.png", 512],
  ])("%s is a %ipx square PNG", (name, size) => {
    const image = png(name);
    expect(image.signature).toEqual(PNG_SIGNATURE);
    expect(image.width).toBe(size);
    expect(image.height).toBe(size);
  });

  it.each(["apple-touch-icon.png", "icon-192.png", "icon-512.png"])(
    "%s carries no alpha channel",
    (name) => {
      // Colour type 2 is truecolour; 6 is truecolour + alpha. iOS does not
      // composite transparency on a home-screen tile — it fills it black — so
      // an alpha channel here is a black corner on the phone (spec §4.2).
      expect(png(name).colourType).toBe(2);
    },
  );
});

describe("icon.svg", () => {
  const svg = readFileSync(
    fileURLToPath(new URL("../../public/icon.svg", import.meta.url)),
    "utf8",
  );

  it("is a full-bleed square with no rounded corners", () => {
    // iOS masks the tile itself. Anything outside a rounded rect is transparent,
    // and transparent composites to black, so a radius here is black slivers in
    // the corners of the home-screen icon.
    expect(svg).toContain('viewBox="0 0 512 512"');
    expect(svg).toContain('<rect width="512" height="512" fill="#25221F"/>');
    expect(svg).not.toContain("rx=");
  });

  it("draws the mark as geometry, not as text", () => {
    // A <text> icon renders with whatever font the rasteriser happens to have.
    // DM Sans ships woff2 only, which no SVG rasteriser on this box can load.
    expect(svg).not.toContain("<text");
    expect(svg).not.toContain("font-family");
  });

  it("uses the dark theme's accent, as sRGB hex", () => {
    // oklch(0.78 0.12 45) — src/index.css:38 — converted, because librsvg 2.62
    // does not parse oklch().
    expect(svg).toContain('stroke="#F79E75"');
  });
});
```

> **Corrected in task 1 — the block above is not what shipped; read
> `web/src/test/icons.test.ts` instead.** Three things about it did not survive contact:
>
> 1. **`new URL("…", import.meta.url)` is Vite's asset syntax, not Node's.** The plugin
>    rewrites the literal form into a `data:` URL of the file's current bytes and the
>    template form into an `import.meta.glob` lookup that resolves to `src/test/undefined`.
>    The first throws `TypeError: The URL must be of scheme file`; the second would have
>    gone on throwing `ENOENT` after the PNGs existed, so the test could never have gone
>    green. Resolve the directory once — `resolve(dirname(fileURLToPath(import.meta.url)), "../../public")`
>    — and join onto it.
> 2. **`node:fs` does not type-check under `tsconfig.app.json`**, whose `types` is
>    `["vite/client"]` alone, so `npm run build` fails with four `TS2591`s. The file moved
>    to `tsconfig.node.json` (which already carries `@types/node` for `vite.config.ts`)
>    rather than widen the app project — adding `node` there would put `process`, `Buffer`
>    and Node's `setTimeout` overloads in front of every browser component in `src`.
> 3. **A header read is decorative.** Bytes 0–25 say nothing about what was drawn, and
>    mutation testing proved it: PNGs re-rendered from a magenta square with a Georgia `B`
>    passed, as did a 34-byte truncation, a `tRNS` chunk making the ground transparent, and
>    a glyph stretched to 345 px from the centre. The shipped test decodes the pixels with
>    `node:zlib` (no new dependency) and asserts the two colours and their census, that
>    every other pixel is a blend of them, the mark's reach against the maskable safe
>    circle, a vertical scan that reads apex-counter-crossbar-feet, and opaque corners —
>    and it takes both colours from the tokens through `contrast.ts`'s new `srgbHex()`, so
>    retuning `--accent` fails the icon rather than silently leaving it peach.

- [ ] **Step 2: Run it and read the failure**

```bash
cd ~/code/.worktrees/alfred/pwa-phase4-install/web
npx vitest run src/test/icons.test.ts
```

Expected: FAIL — `ENOENT` on `apple-touch-icon.png`, and `icon.svg` failing all three of its assertions.

- [ ] **Step 3: Write `web/public/icon.svg`**

The geometry, stated once so it is reviewable. The canvas is 512. The glyph's apex is `(256, 152)` and its feet are `(152, 382)` and `(360, 382)`; the strokes are 44 wide with round joins, and the crossbar sits at `y = 316`, its ends tucked just inside the legs. Furthest point from the centre `(256, 256)`: the left foot's outer corner at `(131.9, 372.9)`, **170.5 px** — inside the 204.8 px radius of a maskable icon's safe circle (40% of the canvas), which is why decision 9 ships one file for both purposes. *(Corrected in task 1: this read `(132, 391)`, **190 px**. That pair mixes the x of the butt cap's outer corner with the y of its inner one — the two corners are `(131.9, 372.9)` at 170.5 px and `(172.1, 391.1)` at 159.0 px — and 190 px is neither. Measured on the shipped render, the furthest inked pixel is `(131, 372)`, 170.5 px. The conclusion is unchanged and has 34 px more margin than claimed.)*

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <!-- The handoff's home-screen mark (Alfred.dc.html:530): a capital A in the
       dark theme's accent on the dark theme's ground. Drawn as two strokes
       rather than as a text element, so it rasterises identically everywhere —
       see the plan's deviation 7. #F79E75 is oklch(0.78 0.12 45),
       src/index.css:38.

       Full-bleed and square on purpose: iOS masks the tile, and anything
       outside a rounded rect would composite to black (spec §4.2).

       The glyph sits inside the maskable safe circle (radius 204.8 on this
       canvas; furthest rendered pixel measured at (132, 372), 170 from the
       centre), so one file serves "any" and "maskable". -->
  <rect width="512" height="512" fill="#25221F"/>
  <g fill="none" stroke="#F79E75" stroke-width="44" stroke-linecap="butt" stroke-linejoin="round">
    <path d="M152 382 L256 152 L360 382"/>
    <path d="M188 316 H324"/>
  </g>
</svg>
```

*(Corrected in task 1: the comment above read "rather than `<text>`" — the very literal
step 1's test forbids **in this file** — so the plan as written could not go green. The
shipped test strips comments before searching the markup, which fixes it at the root;
the wording here is what shipped.)*

- [ ] **Step 4: Generate the three PNGs and delete the two dead SVGs**

```bash
cd ~/code/.worktrees/alfred/pwa-phase4-install/web
rsvg-convert -w 180 -h 180 -b '#25221F' public/icon.svg -o public/apple-touch-icon.png
rsvg-convert -w 192 -h 192 -b '#25221F' public/icon.svg -o public/icon-192.png
rsvg-convert -w 512 -h 512 -b '#25221F' public/icon.svg -o public/icon-512.png
git rm public/favicon.svg public/icons.svg
```

`-b` forces an opaque background, which is what drops the alpha channel the test checks for. If `rsvg-convert` is not installed, ImageMagick produces the same bytes:

```bash
magick -background '#25221F' -density 1024 public/icon.svg -resize 180x180 -alpha remove -alpha off public/apple-touch-icon.png
magick -background '#25221F' -density 1024 public/icon.svg -resize 192x192 -alpha remove -alpha off public/icon-192.png
magick -background '#25221F' -density 1024 public/icon.svg -resize 512x512 -alpha remove -alpha off public/icon-512.png
```

If either tool writes colour type 6 anyway (some builds keep the channel even when it is fully opaque), strip it with `magick public/icon-192.png -alpha off PNG24:public/icon-192.png` and re-run the test.

- [ ] **Step 5: Add the `apple-touch-icon` link to `web/index.html`**

Put it directly above the existing `<link rel="icon">`:

```html
    <!-- iOS ignores SVG for the home-screen tile and ignores the manifest's
         icons entirely (spec §4.2). This link is the only thing that stops it
         screenshotting the page. Opaque and square: iOS masks it itself and
         fills transparency with black. -->
    <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
    <link rel="icon" href="/icon.svg" />
```

- [ ] **Step 6: Extend `web/src/test/index-html.test.ts`**

Add one test beside the existing metas:

```ts
  it("gives iOS a raster home-screen icon, which is the only kind it takes", () => {
    expect(html).toContain('<link rel="apple-touch-icon" href="/apple-touch-icon.png" />');
  });
```

- [ ] **Step 7: Run the tests green**

```bash
npx vitest run src/test/icons.test.ts src/test/index-html.test.ts
```

Expected: PASS. *(Corrected in task 1: 34 tests across 2 files — 25 in `icons.test.ts` once it decodes pixels rather than headers, and 9 in `index-html.test.ts`, whose 7 pre-existing tests this line forgot.)*

- [ ] **Step 8: Commit**

```bash
git add web/public web/index.html web/src/test/icons.test.ts web/src/test/index-html.test.ts
git commit -m "feat(web): the app's icon set, drawn as geometry and opaque for iOS"
```

Counts after this task: **71 files / 1 530 tests** *(corrected in task 1: this said ~1 513, which was right for the header-read test; the pixel-reading one it shipped with carries 16 more, and `index-html.test.ts` gained a second guard)*.

---

## Task 2: Two manifests, and a theme that reaches the splash

`web/public/manifest.json` today names one SVG icon and carries `#25221F` for both colours. `web/README.md` calls the second half a defect and defers it here: *"a manifest cannot follow the theme the way `applyTheme` rewrites the `theme-color` meta, so a light-hour install still gets a dark splash until phase 4 decides otherwise."* Decision: the link points at the manifest that is true now.

*(Corrected in task 2, and it changes what this task is for. A manifest is consumed at **install** time, so a link swap can at most align one install with the theme in force at Add to Home Screen — it is not a splash that follows the theme, and an install at 22:00 keeps its colours at noon for ever. On iOS it may be inert entirely: WebKit fetches at most one manifest per document load, `main.tsx` is a deferred module, and there is no observer for a later `href` change, so the manifest iOS reads may always be the dark one `index.html` ships; iOS's launch screen comes from `apple-touch-startup-image` anyway (decision 11). The swap is still worth shipping — Chromium observes the link, the code is three lines, and being wrong in the same direction as the theme is strictly better than being wrong in both — but every claim this task makes in prose has to be hedged to that, and deviation 6b is the Android cost. What is **not** hedged is the rest: the icon members, `id`, `scope`, `display`, and the two files agreeing.)*

**Files:**
- Modify: `web/public/manifest.json`
- Create: `web/public/manifest-light.json`
- Modify: `web/src/lib/theme.ts`, `web/src/lib/theme.test.ts`
- Create: `web/src/test/manifest.test.ts`
- Modify: `web/src/test/index-html.test.ts` *(corrected in task 2: `web/index.html` was listed here and needs no edit — the manifest link is already what the swap looks for. Only its test changes.)*
- Modify: `web/README.md`, `docs/web-frontend.md`, `docs/superpowers/qa/2026-09-07-pwa-phase1-ios-checklist.md` *(added in task 2. Four passages are falsified by this commit and fixed here: `web/README.md`'s "until phase 4's manifest task lands", `docs/web-frontend.md`'s tier-3 file list and its "what phases 1 to 3 do not cover" bullet, and the phase-1 checklist's §4.2 line, which still said the manifest ships SVG only and no `apple-touch-icon` exists — task 13 writes a **new** checklist and would never have revisited that one. `docs/web-frontend.md`'s overview line and directory map were corrected too, both left disagreeing with the tier-3 list about whether the icons had landed. The `ThemeProvider` paragraph at `docs/web-frontend.md:200` is **not** touched — this commit does not falsify it, and it is task 13 step 1.8's material. The rest of the docs pass is still task 13's.)*

- [ ] **Step 1: Write the failing tests for the two files**

`web/src/test/manifest.test.ts`. *(Corrected in task 2, four times over — the plan's snippet is superseded by what shipped, below.* ***One****: the subjects are read through Vite's `import.meta.glob("…/public/*.json", { query: "?raw" })` and not `node:fs` + `fileURLToPath`. The file imports from `@/lib/theme`, which touches `document`, so it belongs to the browser TS project — where `types` is `vite/client` alone and `node:fs` is not typed — while the Node project task 1 moved `icons.test.ts` into has neither the `@/` alias nor the DOM lib. The glob rather than two `?raw` imports because the URLs `applyTheme` hands the browser must be checked against the **directory**: renaming `manifest-light.json` and fixing the one import that named it left 1 538 tests green while the app asked for a URL the SPA fallback answers with `index.html` as 200 `text/html`.* ***Two****: the colours are `srgbHex(token(theme, "bg"))`, not `THEME_COLOR` — see the contract block for why that export was dropped — and the last test pins the `theme-color` meta to the same token, which is the only thing holding `lib/theme.ts`'s private restatement to the stylesheet.* ***Three****: `toMatchObject` is blind to extra members, and an extra member in a manifest is not inert, so a test pins the key set; a `display_override` added to **both** files is caught by nothing else, which was checked by mutating them. The `description` member in the first assertion is also an addition to the plan's snippet.* ***Four****: `strip` deletes rather than rest-destructuring into `_t`/`_b` — this repo's eslint fails the build on those two as unused.)*

```ts
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { applyTheme, type Theme } from "@/lib/theme";
import { srgbHex, token } from "./contrast";

/**
 * Every JSON file that is really in `web/public/`, keyed by the URL `mount_spa`
 * serves it at. A glob rather than two `?raw` imports, because the URLs
 * `applyTheme` hands the browser have to be checked against the directory and
 * not against a copy of themselves: renaming `manifest-light.json` and fixing
 * the one import that named it left the suite green while the installed app
 * asked for a file the SPA fallback answers with `index.html` as 200
 * `text/html` — a manifest that fails to parse, with a console warning nobody
 * reads. Missing here throws at import, so the whole file goes red at once.
 */
const PUBLIC = new Map(
  Object.entries(
    import.meta.glob("../../public/*.json", { eager: true, query: "?raw", import: "default" }),
  ).map(([path, source]) => [
    `/${path.split("/").pop()}`,
    JSON.parse(source as string) as Record<string, unknown>,
  ]),
);

function served(url: string): Record<string, unknown> {
  const parsed = PUBLIC.get(url);
  if (parsed === undefined) {
    throw new Error(
      `web/public/ serves no JSON at ${url} — it has ${[...PUBLIC.keys()].join(", ")}`,
    );
  }
  return parsed;
}

/**
 * The ground colour, taken from `index.css` through `contrast.ts`'s tokens
 * rather than restated. `contrast.test.ts` holds `TOKENS` to the stylesheet
 * verbatim, so retuning `--bg` the sanctioned way fails here until the manifests
 * are re-coloured too — `icons.test.ts:32` set the idiom one commit ago. The
 * literals are the private `THEME_COLOR` record in `lib/theme.ts`, which is
 * nothing but this pair spelled again for the `theme-color` meta; the last test
 * below is what keeps the two spellings from parting.
 */
const ground = (theme: Theme) => srgbHex(token(theme, "bg"));

const dark = served("/manifest.json");
const light = served("/manifest-light.json");

describe("the manifest", () => {
  it("is an installable standalone app with a stable identity", () => {
    // `id` is what a browser keys the install on. Without it the id defaults to
    // start_url, and any later change to start_url orphans every install.
    expect(dark).toMatchObject({
      id: "/",
      name: "Alfred",
      short_name: "Alfred",
      description: "Your personal butler",
      start_url: "/",
      scope: "/",
      display: "standalone",
      orientation: "portrait",
    });
  });

  it("carries those members and no others", () => {
    // toMatchObject above is blind to anything extra, and an extra member in a
    // manifest is not inert: `display_override`, `related_applications` or a
    // `launch_handler` would each change how the installed app opens.
    expect(Object.keys(dark).sort()).toEqual([
      "background_color",
      "description",
      "display",
      "icons",
      "id",
      "name",
      "orientation",
      "scope",
      "short_name",
      "start_url",
      "theme_color",
    ]);
  });

  it("ships the raster icons iOS and Chrome each need", () => {
    expect(dark.icons).toEqual([
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any maskable" },
    ]);
  });

  it("carries each theme's own ground, as index.css declares it", () => {
    expect(dark.theme_color).toBe(ground("dark"));
    expect(dark.background_color).toBe(ground("dark"));
    expect(light.theme_color).toBe(ground("light"));
    expect(light.background_color).toBe(ground("light"));
  });

  it("differs between the two files in nothing but those two colours", () => {
    // Two files is the price of a static manifest and an hourly theme. The
    // price is only worth paying if they cannot drift apart in anything else.
    // Deleted rather than rest-destructured into `_t`/`_b`: this repo's eslint
    // reports those as unused, and the two spellings say the same thing.
    const strip = (m: Record<string, unknown>) => {
      const rest = { ...m };
      delete rest.theme_color;
      delete rest.background_color;
      return rest;
    };
    expect(strip(light)).toEqual(strip(dark));
  });
});

describe("the URL applyTheme points the link at", () => {
  beforeEach(() => {
    document.head.innerHTML =
      '<meta name="theme-color" content="#25221F">' +
      '<link rel="manifest" href="/manifest.json">';
  });

  afterEach(() => {
    document.head.innerHTML = "";
    document.documentElement.removeAttribute("data-theme");
  });

  const themes: Theme[] = ["dark", "light"];

  it.each(themes)("in the %s theme names a file that exists and carries that ground", (theme) => {
    applyTheme(theme);
    const href = document.querySelector('link[rel="manifest"]')?.getAttribute("href") ?? "";
    expect(PUBLIC.has(href), `applyTheme("${theme}") points at ${href}, which is not in public/`).toBe(
      true,
    );
    expect(served(href).background_color).toBe(ground(theme));
    expect(served(href).theme_color).toBe(ground(theme));
    // The meta and the manifest are the same fact told to two readers, and
    // `lib/theme.ts` spells the colour a second time to tell it. This is the
    // only thing holding that second spelling to the stylesheet.
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute("content")).toBe(
      ground(theme),
    );
  });
});
```

- [ ] **Step 2: Write the failing test for the link swap**

Append to `web/src/lib/theme.test.ts`. Read the file first for its existing `beforeEach` — `applyTheme` tests there already build the `theme-color` meta by hand; this needs a `<link rel="manifest">` beside it.

```ts
describe("applyTheme and the manifest", () => {
  beforeEach(() => {
    document.head.innerHTML =
      '<meta name="theme-color" content="#25221F">' +
      '<link rel="manifest" href="/manifest.json">';
  });

  it("points the manifest at the light file in the light theme", () => {
    applyTheme("light");
    expect(document.querySelector('link[rel="manifest"]')?.getAttribute("href")).toBe(
      "/manifest-light.json",
    );
  });

  it("points it back at the dark file in the dark theme", () => {
    applyTheme("light");
    applyTheme("dark");
    expect(document.querySelector('link[rel="manifest"]')?.getAttribute("href")).toBe(
      "/manifest.json",
    );
  });

  it("survives a document with no manifest link at all", () => {
    // Every test that renders a component has a bare jsdom head. applyTheme
    // runs on the first render of ThemeProvider in all of them.
    document.head.innerHTML = '<meta name="theme-color" content="#25221F">';
    expect(() => applyTheme("light")).not.toThrow();
  });
});
```

- [ ] **Step 3: Run both and read the failures**

```bash
npx vitest run src/test/manifest.test.ts src/lib/theme.test.ts
```

Expected: FAIL — `manifest.test.ts` does not even load (`manifest-light.json` does not exist yet; `THEME_COLOR` is not exported either, but the missing file is what Vite reports first), and of the three `applyTheme` tests only *"points the manifest at the light file"* fails: the other two pass vacuously against an unswapped link and are guards rather than drivers. *(Corrected in task 2: the original line expected all three to fail.)*

- [ ] **Step 4: Write `web/public/manifest.json`**

```json
{
    "id": "/",
    "name": "Alfred",
    "short_name": "Alfred",
    "description": "Your personal butler",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#25221F",
    "theme_color": "#25221F",
    "icons": [
        {
            "src": "/icon.svg",
            "sizes": "any",
            "type": "image/svg+xml",
            "purpose": "any"
        },
        {
            "src": "/icon-192.png",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any"
        },
        {
            "src": "/icon-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any maskable"
        }
    ]
}
```

- [ ] **Step 5: Write `web/public/manifest-light.json`**

The same file with the two colours changed to `#F6F3EE` (light's `--bg`, `src/index.css:73`). Copy it rather than generating it — a build step for two colours is not worth a plugin, and `manifest.test.ts` is what keeps the pair honest:

```bash
cd ~/code/.worktrees/alfred/pwa-phase4-install/web
sed 's/#25221F/#F6F3EE/g' public/manifest.json > public/manifest-light.json
```

- [ ] **Step 6: Add `MANIFEST` and swap the link in `web/src/lib/theme.ts`** *(corrected in task 2: this step was "Export `THEME_COLOR`", and nothing is exported — the contract block above says why. `THEME_COLOR` stays private and keeps its literals; `src/test/manifest.test.ts` anchors it through the meta instead.)*

```ts
/**
 * `--bg` per theme, as index.css declares it. Safari's chrome reads it from the
 * meta, and the two manifests carry the same pair as `theme_color` and
 * `background_color`. Not exported: `src/test/manifest.test.ts` pins both the
 * files and this record's effect on the meta to `--bg` itself, through
 * `contrast.ts`'s tokens, so the stylesheet stays the one source and a retune
 * fails rather than drifts.
 */
const THEME_COLOR: Record<Theme, string> = { dark: "#25221F", light: "#F6F3EE" };

/** The manifest per theme. See `applyTheme` for why there are two. */
const MANIFEST: Record<Theme, string> = {
  dark: "/manifest.json",
  light: "/manifest-light.json",
};
```

and, inside `applyTheme`, after the `theme-color` line — the comment is the one place a reader meets the limits of this feature, so it states them rather than the claim the plan started with:

```ts
  // A manifest is a static file and the theme is the hour's, so there are two
  // of them and the link points at whichever is true now. Read this narrowly: a
  // manifest is consumed at *install* time, so at best this aligns the installed
  // app with the theme in force at Add to Home Screen. It never follows the
  // theme afterwards — whoever installs at 22:00 keeps a dark launch screen at
  // noon for ever — and on iOS it may do nothing at all: WebKit fetches at most
  // one manifest per document load, and this runs after parsing (main.tsx is a
  // deferred module), with no observer to re-read the href. Chromium does
  // observe it, which is where the value is, and where the cost is too: its
  // WebAPK update check fingerprints these two colours, so crossing 07:00 or
  // 19:00 can schedule a silent reinstall. Unverified on a device either way —
  // docs/superpowers/qa/ holds the checklist that would settle it.
  document.querySelector('link[rel="manifest"]')?.setAttribute("href", MANIFEST[theme]);
```

- [ ] **Step 7: Replace the stale `index.html` test**

`src/test/index-html.test.ts` currently asserts *"keeps the manifest link untouched for phase 4"*. Replace that test with the one that is true now:

```ts
  it("links the dark manifest, which applyTheme swaps for the light one", () => {
    // Dark to match the dark `theme-color` two lines up: both are the
    // first-paint values, and both are rewritten within a frame by applyTheme.
    expect(html).toContain('<link rel="manifest" href="/manifest.json" />');
  });
```

- [ ] **Step 8: Run the tests green**

```bash
npx vitest run src/test/manifest.test.ts src/lib/theme.test.ts src/test/index-html.test.ts
```

Expected: PASS. Then the whole suite, because `applyTheme` runs in every test that mounts `ThemeProvider`:

```bash
npx vitest run
```

Expected: `Test Files 72 passed (72)`, all green — `Tests 1540 passed (1540)`.

- [ ] **Step 9: Commit**

```bash
git add web/public/manifest.json web/public/manifest-light.json web/src/lib/theme.ts \
        web/src/lib/theme.test.ts web/src/test/manifest.test.ts \
        web/src/test/index-html.test.ts web/README.md docs/web-frontend.md \
        docs/superpowers/qa/2026-09-07-pwa-phase1-ios-checklist.md
git commit -m "feat(web): a manifest per theme, so a light-hour install gets a light splash"
```

Counts after this task: **72 files / 1 540 tests** *(corrected in task 2: this said ~1 524, carried over from the stale 1 513 baseline. The real arithmetic is task 1's 1 530 plus seven in `manifest.test.ts` — five on the files, and one per theme on the URL the swap produces — and three in `theme.test.ts`; `index-html.test.ts` replaces a test rather than adding one.)*.

---

## Task 3: The service-worker harness, and the first thing the worker does

A service worker has no DOM, so Testing Library is no help; and jsdom has no `ServiceWorkerGlobalScope`, so there is nothing to mount it in. The harness loads the **shipped bytes** — `public/sw.js` through Vite's `?raw`, exactly as `index-html.test.ts` loads the shipped `index.html` — into a hand-built scope with `new Function("self", source)(scope)`. That works only because every global the worker touches goes through `self.`, which is why that is a convention rather than a style note.

Two jsdom facts that shape the harness, both worth knowing before you write it:

- **Do not build real `Request` objects.** Node's undici throws `Cannot construct a Request with mode 'navigate'`, and `request.mode === "navigate"` is the whole of the navigation branch. The worker only ever reads `.url`, `.method` and `.mode`, so the harness passes a plain object and the real worker gets a real `Request`. The shapes agree on everything the worker uses.
- **`Response` is a Node 22 global** and survives the jsdom environment (jsdom implements no fetch, so vitest leaves Node's in place). If a future Node removes it, import it from `node:undici` in the harness only.

**Files:**
- Create: `web/src/test/sw-harness.ts`
- Create: `web/src/test/sw.test.ts`
- Create: `web/src/test/sw-harness.test.ts` *(added in task 3 — see the correction below)*
- Create: `web/public/sw.js`
- Modify: `web/eslint.config.js`, `web/README.md`

> **Corrected in task 3, after review. The three code fences below (steps 2, 3 and 5) are
> the pre-review draft; the files in the tree are what shipped and are authoritative.**
> Two of the changes are behaviour, not tests, and tasks 4 to 11 build on them:
>
> 1. **A 200 page of `text/html` that names no `/assets/` installed cleanly as the app
>    shell.** `isShell` was "200 + text/html" and nothing more, so when `assetsInHtml`
>    returned `[]` the validation loop was a no-op and `precache()` wrote that page as
>    `/index.html`. Two inputs that really answer that description: the reverse proxy's
>    default page (this origin is behind one) while a proxy host is edited or a DNS record
>    moves, and the **dev server's own `index.html`**, which names `/@vite/client` and
>    `/src/main.tsx` and nothing hashed — so once task 9 registers the worker, every
>    `npm run dev` would have installed a zero-asset shell. Nothing re-installs afterwards:
>    `install` only re-runs when `sw.js`'s own bytes change, so the recovery is task 11.
>    `precache()` now refuses `urls.length === 0`.
> 2. **`isAsset`'s comment was backwards for the only URL space it sees, and the half that
>    fires was untested.** `/assets` is a StaticFiles **mount** (`core/channels/spa.py:39-40`),
>    so `spa_fallback` never sees it: a removed hashed chunk comes back **404
>    `application/json`**, not the SPA's HTML — `tests/core/channels/test_spa.py:155-160`
>    already pins that 404. So `response.ok` is the guard that actually fires, and the
>    `text/html` branch cannot while the mount exists. Both halves stay (a proxy or captive
>    portal can answer 200 with a page at any time), the comment says which is which, and
>    there is now a test for each.
> 3. **One refusal instead of two places and one guard.** The extraction regex was the only
>    thing keeping `/api` out of the precache. It is now deliberately wide and a single
>    named guard, `isAssetUrl`, decides — same-origin **and** under `/assets/` — so
>    widening the extraction can no longer widen the cache. `assetsInHtml` normalises to
>    pathnames and deduplicates (a split build writes a `modulepreload` hint beside the tag
>    naming the same URL). Task 6's fetch-handler guard should read the origin from the
>    same `scopeOrigin()` rather than restating `new URL(self.registration.scope).origin`.
> 4. **`eslint.config.js` gained `no-restricted-globals`.** The block as planned did not
>    enforce the invariant its comment claimed: `globals.serviceworker` makes bare `caches`,
>    `fetch`, `clients`, `skipWaiting` and `registration` *defined*, so `no-undef` was
>    silent and rule 3 of the worker's header — the seam `new Function("self", source)`
>    depends on — was enforced by nothing. The harness seals the same names at run time by
>    passing them to `new Function` as parameters, so a bare `fetch` can no longer reach
>    Node's real network.
> 5. **`web/src/test/sw-harness.test.ts` is new, and is the reason to trust the other
>    eight tasks' tests.** The harness modelled `ExtendableEvent` loosely enough to pass
>    workers that cannot run on a phone: it kept only the last listener per type and only
>    the last `waitUntil`, `FakeCache.put` never looked at the body (so `await shell.text()`
>    for `await shell.clone().text()` was invisible here and fatal in a browser),
>    `clients.matchAll()` ignored its options and always answered one window client (task 11
>    would have passed without `includeUncontrolled` or a prior `claim()`), and fetch routes
>    keyed on pathname so cross-origin collapsed onto same-origin. All fixed, and each fix
>    is pinned by a test in that file.
> 6. **The harness reads the cache name from the worker's own `VERSION`** and exposes it as
>    `worker.cache`. Three copies of `alfred-shell-v1` across two files meant a `VERSION`
>    bump — the thing task 5's purge exists for — turned assertions vacuous or broke them
>    with `Cannot read properties of undefined`. The `alfred-` prefix stays restated in the
>    harness on purpose: that one *is* the invariant tasks 5 and 11 key on.

- [ ] **Step 1: Teach ESLint about `public/*.js`**

`web/eslint.config.js` currently matches `**/*.{ts,tsx}` only, so a worker in `public/` is unlinted. Add a second block after the existing one:

```js
  {
    // The two service workers. Not TypeScript — they are shipped verbatim by
    // Vite's public/ copy, are loaded as classic (non-module) workers, and are
    // evaluated in tests with `new Function("self", source)`, which refuses
    // import/export. `sourceType: 'script'` is what keeps all three honest.
    files: ['public/*.js'],
    extends: [js.configs.recommended],
    languageOptions: {
      globals: globals.serviceworker,
      ecmaVersion: 2023,
      sourceType: 'script',
    },
  },
```

- [ ] **Step 2: Write the harness**

`web/src/test/sw-harness.ts`. It is the only file in this phase that is infrastructure rather than behaviour, so it is written whole, once:

```ts
import { vi } from "vitest";

/** Every URL in these tests is absolute, because a real `fetch` event's is. */
export const ORIGIN = "https://alfred.example.com";

const absolute = (key: string | { url: string }): string =>
  new URL(typeof key === "string" ? key : key.url, ORIGIN).href;

/** Enough of `Cache` for this worker: open, match, put, keys, delete. */
class FakeCache {
  // Declared and assigned rather than a `readonly` constructor parameter:
  // tsconfig's `erasableSyntaxOnly` rejects parameter properties (TS1294), and
  // `tsc -b` is the only one of lint/vitest/build that says so.
  readonly entries: Map<string, Response>;
  constructor(entries: Map<string, Response>) {
    this.entries = entries;
  }
  match(key: string | { url: string }): Promise<Response | undefined> {
    return Promise.resolve(this.entries.get(absolute(key)));
  }
  put(key: string | { url: string }, response: Response): Promise<void> {
    this.entries.set(absolute(key), response);
    return Promise.resolve();
  }
  /** Real `Cache.keys()` answers Requests carrying absolute URLs. So does this. */
  keys(): Promise<{ url: string }[]> {
    return Promise.resolve([...this.entries.keys()].map((url) => ({ url })));
  }
  delete(key: string | { url: string }): Promise<boolean> {
    return Promise.resolve(this.entries.delete(absolute(key)));
  }
}

export interface Harness {
  store: Map<string, Map<string, Response>>;
  respond(pathname: string, response: Response): void;
  fetched: string[];
  reloaded: string[];
  install(): Promise<void>;
  activate(): Promise<void>;
  request(init: {
    path: string;
    mode?: string;
    method?: string;
    origin?: string;
  }): Promise<Response | undefined>;
  message(data: unknown): Promise<void>;
  skipWaitingCalls: number;
  claimCalls: number;
  unregisterCalls: number;
  navigated: string[];
  keys(name?: string): string[];
}

const CACHE = "alfred-shell-v1";

/**
 * Evaluate a service worker's shipped source against a fake global scope and
 * hand back the levers a test needs.
 *
 * `new Function("self", source)` is the whole trick: the worker reaches every
 * global through `self.`, so one object is the entire world it can see. Nothing
 * here is mocked at the module boundary — these tests run the bytes that ship.
 */
export function loadWorker(source: string): Harness {
  const store = new Map<string, Map<string, Response>>();
  const routes = new Map<string, Response>();
  const listeners = new Map<string, (event: unknown) => void>();
  const harness: Partial<Harness> & { fetched: string[]; reloaded: string[]; navigated: string[] } =
    { fetched: [], reloaded: [], navigated: [] };
  let skipWaitingCalls = 0;
  let claimCalls = 0;
  let unregisterCalls = 0;

  const scope = {
    addEventListener(type: string, fn: (event: unknown) => void) {
      listeners.set(type, fn);
    },
    registration: {
      scope: `${ORIGIN}/`,
      unregister: () => {
        unregisterCalls += 1;
        return Promise.resolve(true);
      },
    },
    skipWaiting: () => {
      skipWaitingCalls += 1;
    },
    clients: {
      claim: () => {
        claimCalls += 1;
        return Promise.resolve();
      },
      matchAll: () =>
        Promise.resolve([
          {
            url: `${ORIGIN}/`,
            navigate: (url: string) => {
              harness.navigated.push(url);
              return Promise.resolve();
            },
          },
        ]),
    },
    caches: {
      open: (name: string) => {
        if (!store.has(name)) store.set(name, new Map());
        return Promise.resolve(new FakeCache(store.get(name)!));
      },
      keys: () => Promise.resolve([...store.keys()]),
      delete: (name: string) => Promise.resolve(store.delete(name)),
    },
    fetch: vi.fn((input: string | { url: string }, init?: { cache?: string }) => {
      const path = new URL(typeof input === "string" ? input : input.url, ORIGIN).pathname;
      harness.fetched.push(path);
      // Recorded rather than asserted on the source: `cache: "reload"` is the
      // only thing between a worker installing seconds after a deploy and a
      // precache of the bytes that deploy just replaced.
      if (init?.cache === "reload") harness.reloaded.push(path);
      const response = routes.get(path);
      // No route programmed = no network. A real worker sees a TypeError here.
      if (!response) return Promise.reject(new TypeError(`no route for ${path}`));
      return Promise.resolve(response.clone());
    }),
  };

  new Function("self", source)(scope);

  const extendable = async (type: string) => {
    let waited: Promise<unknown> = Promise.resolve();
    listeners.get(type)?.({ waitUntil: (p: Promise<unknown>) => void (waited = p) });
    await waited;
  };

  return {
    store,
    fetched: harness.fetched,
    reloaded: harness.reloaded,
    navigated: harness.navigated,
    respond: (pathname, response) => void routes.set(pathname, response),
    install: () => extendable("install"),
    activate: () => extendable("activate"),
    async request({ path, mode = "no-cors", method = "GET", origin = ORIGIN }) {
      // A plain object, not a Request: undici refuses to construct one with
      // mode "navigate", and the worker reads only these three fields.
      const request = { url: new URL(path, origin).href, method, mode };
      let answered: Promise<Response> | undefined;
      listeners.get("fetch")?.({
        request,
        respondWith: (p: Promise<Response>) => void (answered = p),
      });
      // `undefined` is the meaningful answer: the worker declined to handle it
      // and the browser goes to the network untouched.
      return answered ? await answered : undefined;
    },
    async message(data) {
      let waited: Promise<unknown> = Promise.resolve();
      listeners.get("message")?.({ data, waitUntil: (p: Promise<unknown>) => void (waited = p) });
      await waited;
    },
    get skipWaitingCalls() {
      return skipWaitingCalls;
    },
    get claimCalls() {
      return claimCalls;
    },
    get unregisterCalls() {
      return unregisterCalls;
    },
    keys(name = CACHE) {
      return [...(store.get(name)?.keys() ?? [])].map((url) => new URL(url).pathname).sort();
    },
  };
}

const body = (type: string) => (text: string) =>
  new Response(text, { status: 200, headers: { "content-type": type } });

export const html = body("text/html; charset=utf-8");
export const js = body("text/javascript");
export const css = body("text/css");
export const font = () =>
  new Response("woff2", { status: 200, headers: { "content-type": "font/woff2" } });
```

*(Corrected in task 3: `FakeCache` was written `constructor(readonly entries: Map<string, Response>) {}`.
Both `web/tsconfig.app.json` and `web/tsconfig.node.json` set `erasableSyntaxOnly`, which rejects
parameter properties — TS1294. Worth knowing for every later task: **`npm run lint` and `npx vitest run`
both pass on it.** Vitest strips types without checking them and ESLint is not type-aware here, so
`tsc -b` inside `npm run build` is the only member of the gate that says so. Run the whole gate, not
two thirds of it.)*

- [ ] **Step 3: Write the failing test for install's precache**

`web/src/test/sw.test.ts`:

```ts
import { beforeEach, describe, expect, it } from "vitest";
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
    const listed = await worker.store.get("alfred-shell-v1")!.get(
      "https://alfred.example.com/__shell-manifest",
    )!.json();
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
```

*(Corrected in task 3, three ways; the block above is what shipped.*

*1. **The instruction that stood here is gone.** It read "Delete the third test's stray loop before you
commit … write it as:" and then restated the third test under the fourth's name. There is no loop in
the third test, and following it would have deleted the only assertion on `reloaded` — the one thing
standing between a worker that installs seconds after a deploy and a precache of the bytes that deploy
just replaced.*

*2. **`expect(worker.reloaded).toEqual(worker.fetched)` is not an assertion.** Two empty lists satisfy
it, so it passes on a worker that fetches nothing at all. Spelled out as a literal list instead.*

*3. **A sixth test, because `precache()`'s `isAsset` guard was pinned by nothing in the whole plan.**
Mutation in a scratch copy: `if (!isAsset(response)) continue;` and `isAsset` reduced to
`return response.ok;` both left the five tests above green. Task 4's "installs anyway when a font is
missing" exercises the **font** path, which is deliberately non-fatal, and task 7's "refuses to store
the SPA fallback" exercises `asset()`'s **runtime** branch — neither reaches the install-time one, and
that is the branch whose own comment says it is the difference between a deploy and a brick.
`expect(worker.keys()).toEqual([])` went onto both failure tests at the same time: they claimed "rather
than caching half a shell" and checked no part of it.)*

- [ ] **Step 4: Run it and read the failure**

```bash
npx vitest run src/test/sw.test.ts
```

Expected: FAIL — `Failed to resolve import "../../public/sw.js?raw"`.

- [ ] **Step 5: Write `web/public/sw.js`**

Only install, for now. The header comment is part of the deliverable — this file is the one place a reviewer can read the whole caching policy.

```js
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
```

- [ ] **Step 6: Run the tests green**

```bash
npx vitest run src/test/sw.test.ts
npm run lint
```

Expected: PASS, 14 tests in `sw.test.ts` and 12 in `sw-harness.test.ts` *(corrected in task 3: 5 as planned — see the correction block at the head of this task)*. ESLint silent — if it complains that `self` is undefined, the `public/*.js` block from step 1 is missing or misplaced. **Then run `npm run build` too**: it is the only part of the gate that type-checks the harness.

- [ ] **Step 7: Commit**

```bash
git add web/eslint.config.js web/public/sw.js web/README.md \
        web/src/test/sw-harness.ts web/src/test/sw-harness.test.ts web/src/test/sw.test.ts
git commit -m "feat(web): a service worker that precaches the shell index.html names"
```

Counts after this task: **74 files / 1 566 tests** *(corrected in task 3: this said ~1 529, on master's 1 503 baseline. Tasks 1 and 2 moved it to 72 files / 1 540; this task adds `sw.test.ts` (14) and `sw-harness.test.ts` (12). Every later task in this plan quotes a total on the stale baseline — read the delta each one adds, not the total.)*

---

## Task 4: The fonts, and the shell that is not a shell

One hole in task 3's precache *(corrected in task 4: this said "two", and the second — the shell fetch's own `isShell` guard — landed in task 3 along with the test for it, so the title's second clause is now history rather than work)*: `dist/assets/` holds **18 font files**, referenced from the stylesheet's `url()` rules and not from the HTML, so a cold offline launch currently renders in `-apple-system` rather than DM Sans.

`@fontsource/geist-mono` ships a `.woff` beside almost every `.woff2`. No browser that can run a service worker asks for a `.woff`, so precaching them would spend a third of the install on bytes nothing will ever request; anything missed is still picked up cache-first on first use (task 7).

**Files:**
- Modify: `web/public/sw.js`
- Modify: `web/src/test/sw.test.ts`

> **Corrected in task 4, against a real `npm run build` on 2026-09-16. The code
> fences below are the pre-implementation draft; the files in the tree are what
> shipped and are authoritative.**
>
> 1. **The font arithmetic was wrong in both directions.** This build emits **18**
>    font files, not 20, and **220 380 bytes** of them, not ~800 kB: 8 `.woff2`
>    (139 436 B) and 10 `.woff` (80 944 B). Skipping the `.woff` therefore saves
>    **37% of the fonts, not half the precache** — the precache is ~620 kB, of
>    which the entry chunk is 414 kB. Some faces have no `.woff2` file at all
>    because Vite inlined them under `assetsInlineLimit`: the stylesheet carries
>    six `url(data:font/woff2;base64,…)` and two `url(data:font/woff;base64,…)`,
>    so the data: refusal is not a hypothetical — it is eight rules in the real
>    file, and their bytes are already cached inside the stylesheet. Of the 14
>    `@font-face` rules, **6** have no `.woff2` file and **2** have no file at
>    all — "four" is the count of faces with a `.woff` but no `.woff2`, which is
>    a different question. Deviation #10's row is corrected to match. The fixture's
>    `dm-sans-latin-wght-normal-Bf69Tn_J.woff2` is a name from an older
>    `@fontsource-variable/dm-sans`; this build emits
>    `dm-sans-latin-standard-normal-Bf69Tn_J.woff2`, and the shipped fixture uses
>    the real spellings, real `format("woff2-variations")` and no space before
>    `format(`, so that it models the bytes rather than a memory of them.
> 2. **Step 1's `url(data:…)` test was vacuous as written.** `worker.fetched`
>    records `new URL(input, ORIGIN).pathname`, and a data: URL's pathname is
>    `font/woff2;base64,…` — it never starts with `"data:"`, so
>    `fetched.filter((path) => path.startsWith("data:"))` is `[]` on a worker that
>    fetches every data URL in the stylesheet. The shipped test looks for
>    `"base64"` in the pathname instead. It is also honest about which guard
>    fires: **both** do, so it dies only when `fontsInCss` is rewritten wide open,
>    which is the regression it exists to catch.
> 3. **The regex is wide and `isAssetUrl` is the refusal — as task 3 asked, which
>    the `/\/assets\//` anchor below would have prevented.** Anchored, nothing
>    reaching `.filter(isAssetUrl)` can ever fail it, so "one place decides what
>    may be precached" would have been a comment rather than a property. Shipped:
>    `url\(\s*["']?([^)"']+)` → `.filter(isAssetUrl)` → resolve to a pathname →
>    `.endsWith(".woff2")`. That order matters. `isAssetUrl` first, so a CDN's
>    `@font-face`, a `/fonts/` path and `url(/assets/../api/…)` are all turned
>    away by the same guard the assets use; the pathname next, so the manifest
>    entry matches the cache key task 5 prunes against; `.woff2` last, because it
>    is a question about *browsers*, not about what may be stored.
> 4. **`.trim()` is gone.** The WHATWG URL parser strips leading and trailing
>    whitespace from its input, so `url( /assets/x.woff2 )` normalises with or
>    without it: no test could tell the two apart, which made it dead.
> 5. **Four tests became thirteen**, and the planned skip-the-`.woff` test only
>    bites once the `.woff` is *routed* — otherwise it passes because a worker
>    that wanted it could not fetch it, which is task 3's lesson again.
>    The additions pin the things the planned four leave open: the fonts are in
>    the **manifest** (without it activate prunes them on the next boot), they are
>    fetched `cache: "reload"`, the bytes under a font's key are that font's, a
>    fetch that *rejects* is survived as well as one that answers wrongly, the
>    stylesheet is read and the 414 kB entry chunk is not, and a quoted or spaced
>    `url()` still lands under a normalised key.

> **Corrected in task 4 a second time, after two reviews.** A spec review found
> the task complete and every measured number reproducible; a quality review ran
> 44 mutants and found five real survivors and one robustness hole. All of it is
> fixed in the tree; the seven changes that are behaviour, not tests, are below,
> and tasks 5 to 11 build on them.
>
> 1. **Every fetch now has a deadline, and that is a different guarantee from the
>    `.catch()`.** `fetchFresh(url)` is the only way this file fetches anything:
>    `cache: "reload"` plus a `self.AbortController` armed for `TIMEOUT_MS`
>    (10 s). A catch covers a fetch that *rejects*; it does nothing at all for one
>    that never settles, which is precisely what a captive portal does — it
>    accepts the connection and answers nothing. `precache()` runs inside
>    `waitUntil()`, so one hung request hung the whole install until the browser
>    killed it, leaving the phone with **no worker at all**: the exact outcome the
>    font loop's catch is justified by avoiding, reached through the other door.
>    The asset and shell fetches had the same hole and are covered by the same
>    helper; there the deadline arrives as a rejection and fails the install
>    cleanly, which is the right answer for a chunk and the wrong one for a
>    typeface. The harness gained `hang(path)`, `deadlines` and
>    `deadlinesCleared` to pin all of it without a real wait.
> 2. **`/assets/` is an anchor, and nothing said so.** Two mutants survived the
>    whole 27-test suite: `startsWith` → `includes`, after which
>    `url(/api/admin/assets/overview.woff2)` was fetched and — the mount answers
>    `application/json`, which `isAsset` accepts — **stored in the shell cache as
>    a font**; and the trailing slash dropped, after which `/assetsphalt/x.woff2`
>    was cached. None of the three refusal fixtures had a mid-path `/assets/`.
>    Three rows were added, plus `ASSETS` as a named constant.
> 3. **`%2f` is refused.** `url(/assets/..%2fapi/admin/overview.woff2)` passed
>    `isAssetUrl` — the URL parser does not treat `%2f` as a separator, so the
>    pathname genuinely stays under `/assets/` — and became a traversal the moment
>    uvicorn decoded it. Inert against this server (`StaticFiles` 404s it, and
>    `isAsset` then declines), but the refusal should not rest on a server this
>    file cannot see. No name Vite emits contains one.
> 4. **The extraction is case-insensitive.** `url(` was matched case-sensitively
>    while CSS's `url()` token is not, so `URL(/assets/x.woff2)` and `.WOFF2` were
>    silently dropped — a *second, undocumented refusal standing beside
>    `isAssetUrl`*, which is exactly what correction 3 of the first round said
>    there would not be. The price of the old behaviour was a typeface that does
>    not arrive and says nothing.
> 5. **A `url()` inside a comment or a `content:` string is taken, deliberately,
>    and is now pinned as a cost.** The real stylesheet carries one comment
>    (Tailwind's licence banner). Bounded by `isAssetUrl` to same-origin
>    `/assets/`, so the price is one install request and some cache bytes nothing
>    asks for. The alternative is a CSS tokeniser inside a service worker, which
>    is a far worse trade. A test now asserts both halves: that it *is* taken, and
>    that a comment naming `/api` or a CDN still is not.
> 6. **Duplicates, and the base a `url()` resolves against.** `fontsInCss` did not
>    dedupe while `assetsInHtml` did, and
>    `src: url(x.woff2) format("woff2-variations"), url(x.woff2) format("woff2")`
>    is a shape @fontsource emits. One `new Set` now covers every stylesheet at
>    once, and a font the HTML *already* named (a `<link rel="preload" as="font">`)
>    is not fetched twice. Separately, a `url()` now resolves against the
>    **stylesheet**, as a browser resolves it, not against the document:
>    `url(./x.woff2)` in `/assets/index-*.css` is `/assets/x.woff2`, where the old
>    code made it `/x.woff2` and then refused it. Reachable the moment `base` is
>    not `/`. Query and fragment are still dropped by normalising to a pathname —
>    unreachable while Vite hashes into the filename, and stated in the comment.
> 7. **The manifest records fact, not intent.** `...cached.map(([url]) => url)` →
>    `...fonts` survived all 27 tests: both "installs anyway when a font…" tests
>    asserted only `keys()`, and the healthy fixture made the two arrays
>    identical. This is the property the first round's commit message claimed to
>    have mutation-checked, and had not.
>
> Two claims of the first round were also wrong and are corrected where they were
> made. **The evidence for "read the stylesheet, not the entry chunk" did not
> hold**: the chunk does contain the text `url(` twice, but both are a
> zero-argument method of that name, and the regex needs at least one character
> before the `)`, so `fontsInCss` over the real 414 kB chunk captures *nothing*.
> The rule stands on its own — a bundle is not a list of what the app needs
> offline, and any library that injects styles carries `@font-face` inside it —
> and the comment now says that instead. **And the vacuity ratio was backwards**;
> see step 2.
>
> **The `unicode-range` subsets are kept, and the reasoning is now explicit,**
> because the review read it as inconsistent with refusing the `.woff` and that
> was a fair reading of what was written. 56 732 of the 139 436 precached bytes
> are latin-ext and cyrillic, which a browser requests only when the text contains
> those codepoints. The two cases are not the same: **a `.woff` is dead always** —
> no browser that can run a service worker will ever choose one over its `.woff2`,
> whoever is reading and whatever they type — whereas **a subset is dead only
> until** a memory, a trigger name or a pasted line contains one of its
> codepoints, and the cost of having guessed wrong is tofu on a phone with no
> network. Dead-always is refused; dead-until is bought.

- [ ] **Step 1: Write the failing tests**

Add to `sw.test.ts`. Extend `SHELL_HTML`'s stylesheet with a real-shaped CSS body — the `data:` URL is in there because the built stylesheet really does carry inline base64 fonts beside the file ones, and the regex must not take them:

```ts
const SHELL_CSS = `@font-face{font-family:"DM Sans Variable";src:url(/assets/dm-sans-latin-wght-normal-Bf69Tn_J.woff2) format("woff2")}
@font-face{font-family:"Geist Mono";src:url(/assets/geist-mono-latin-400-normal-DTRLJnHl.woff2) format("woff2"),url(/assets/geist-mono-latin-400-normal-B2yvC1Cq.woff) format("woff")}
@font-face{font-family:"Geist Mono";src:url(data:font/woff2;base64,d09GMgABAAAA) format("woff2")}`;

describe("install, and the fonts", () => {
  function withFonts(): Harness {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-CY0Sh5Q2.js", js("console.log(1)"));
    worker.respond("/assets/index-nhBuGG1b.css", css(SHELL_CSS));
    worker.respond("/assets/dm-sans-latin-wght-normal-Bf69Tn_J.woff2", font());
    worker.respond("/assets/geist-mono-latin-400-normal-DTRLJnHl.woff2", font());
    return worker;
  }

  it("follows the stylesheet to the woff2 files the HTML never names", async () => {
    const worker = withFonts();
    await worker.install();
    expect(worker.keys()).toContain("/assets/dm-sans-latin-wght-normal-Bf69Tn_J.woff2");
    expect(worker.keys()).toContain("/assets/geist-mono-latin-400-normal-DTRLJnHl.woff2");
  });

  it("skips the .woff fallbacks, which no worker-capable browser asks for", async () => {
    const worker = withFonts();
    await worker.install();
    // fontsource ships one beside every woff2; taking them doubles the install.
    expect(worker.fetched).not.toContain("/assets/geist-mono-latin-400-normal-B2yvC1Cq.woff");
  });

  it("ignores url(data:…), which is already in the stylesheet it cached", async () => {
    const worker = withFonts();
    await worker.install();
    expect(worker.fetched.filter((path) => path.startsWith("data:"))).toEqual([]);
  });

  it("installs anyway when a font is missing", async () => {
    const worker = withFonts();
    // A font that 404s comes back as index.html with a 200 (mount_spa's
    // fallback). It must not be cached as a font, and it must not fail an
    // install over a typeface.
    worker.respond("/assets/geist-mono-latin-400-normal-DTRLJnHl.woff2", html("<!doctype html>"));
    await worker.install();
    expect(worker.keys()).not.toContain("/assets/geist-mono-latin-400-normal-DTRLJnHl.woff2");
    expect(worker.keys()).toContain("/index.html");
  });

});
```

*(Corrected in task 3: a fifth test stood here, `refuses a shell that is not HTML`, with
`worker.respond("/index.html", js("not a page"))`. It has moved into task 3, where the code
it tests lives, and its fixture had to change on the way. As written it passed on a worker
with **no `isShell` check at all**: `"not a page"` names no assets, so task 3's zero-asset
refusal rejects it first, and the two assets it would otherwise have fetched had no routes
programmed — so the install failed for want of a network rather than for the reason the
test claimed. Task 3's version serves the real shell HTML under a `text/javascript`
content type and routes every asset it names, which leaves `isShell` as the only thing that
can refuse it. Both mutants — `isShell` → `response.ok` and `isShell` → `true` — survived
the fixture as written and die against the new one.)*

- [ ] **Step 2: Run them and read the failures**

```bash
npx vitest run src/test/sw.test.ts -t "fonts"
```

Expected: FAIL on the first three (no font is fetched or cached at all); the fourth already passes, because `isAsset` landed in task 3 — leave it in, it is the regression guard for the **font** path, which is non-fatal on purpose and is a different branch from task 3's fatal one. *(Corrected in task 3: this said "the last two", when `isShell`'s test moved into task 3 — see the note under step 1.)* *(Corrected in task 4: of the thirteen shipped, **six** fail here and seven pass — and those seven pass **vacuously**, because a worker with no font pass at all fetches no font, right or wrong. That is not a green to trust: all thirteen were re-checked by mutation after step 3, against sixteen mutants of `sw.js`. Two findings came out of it — the normalise-to-a-pathname step survived deletion until the quoted/spaced test also asserted the **manifest**, and the `url(data:…)` test survives either guard being removed on its own, which its comment now says out loud.)* *(Corrected again after review, and the ratio above was backwards: it is **seven fail, six pass**, not six and seven. The final shape, measured by checking out `1d92ae8`'s `sw.js` under the finished test file: `sw.test.ts` holds **42** tests, 14 of them task 3's; of the 28 this task added, **17 fail** against the pre-task-4 worker, **10 pass vacuously** — the `.woff` skip, the `url(data:…)` refusal, the seven `refuses … font` rows and the read-the-stylesheet-not-the-chunk rule, every one of which a worker that fetches no font satisfies by doing nothing — and **1** is a regression guard for task 3's own `isShell`, which passes because the code it guards already exists. A vacuous pass is not a failure of the test; it is a reason the mutation pass is the only green worth reading.)*

- [ ] **Step 3: Add `fontsInCss` and the second pass to `precache()`**

In `web/public/sw.js`, beside `assetsInHtml`:

```js
/* The stylesheet is the fonts' manifest, the way index.html is the assets'.
   `.woff2` only: @fontsource ships a `.woff` beside each one for browsers that
   predate it, and nothing that can run a service worker asks for a `.woff`. The
   `/assets/` anchor is also what skips `url(data:font/woff2;base64,…)`, which is
   already inside the stylesheet this worker has cached. */
function fontsInCss(css) {
  return [...css.matchAll(/url\(\s*["']?(\/assets\/[^)"']+\.woff2)/g)].map((match) => match[1]);
}
```

*(Corrected in task 3: route the result through `isAssetUrl` the way `assetsInHtml` now
does — `.filter(isAssetUrl)` — so there stays exactly **one** place in this worker that
decides what may be precached. The `/assets/` text in this regex is then a narrowing for
`url(data:…)`, not the guard.)*

and, in `precache()`, between the asset loop and the `cache.put` loop:

```js
  const fonts = [];
  for (let i = 0; i < urls.length; i += 1) {
    if (!urls[i].endsWith(".css")) continue;
    fonts.push(...fontsInCss(await responses[i].clone().text()));
  }

  /* A missing font does not fail the install. The app is legible in the system
     stack, and an install aborted over a typeface leaves the phone with no
     worker at all — which is the worse of the two. Anything skipped here is
     still picked up cache-first the first time it is asked for. */
  const cached = [];
  for (const url of fonts) {
    const response = await self.fetch(url, { cache: "reload" }).catch(() => null);
    if (response && isAsset(response)) {
      cached.push([url, response]);
    }
  }
```

then extend the writes and the manifest:

```js
  await cache.put(SHELL, shell);
  for (let i = 0; i < urls.length; i += 1) await cache.put(urls[i], responses[i]);
  for (const [url, response] of cached) await cache.put(url, response);
  await cache.put(
    MANIFEST,
    new Response(JSON.stringify([SHELL, ...urls, ...cached.map(([url]) => url)])),
  );
```

- [ ] **Step 4: Run green, including task 3's tests**

```bash
npx vitest run src/test/sw.test.ts
```

Expected: PASS, 27 tests *(corrected in task 3: 10 as planned. Task 3 shipped 14 rather than 5 — see its own counts — and this task's `refuses a shell that is not HTML` moved into it, so this block adds four, not five)* *(corrected in task 4: 18 on that arithmetic; this block shipped thirteen tests rather than four, so 14 + 13 = 27)* *(corrected again after review: **42**. The review round added fifteen more — the `/assets/` anchor, `%2f`, casing, the comment/string cost, the two dedupes, the stylesheet base, the manifest-is-fact assertion, four on the deadline, and a regression guard for task 3's `isShell`. `sw-harness.test.ts` stays at 12.)*. Task 3's `records what it cached` still expects three entries, because that fixture's stylesheet has no `url()` at all — check it did not silently change.

- [ ] **Step 5: Commit**

```bash
git add web/public/sw.js web/src/test/sw.test.ts
git commit -m "feat(web): precache the woff2 the stylesheet names, and only those"
```

Counts after this task: **74 files / 1 579 tests** *(corrected in task 3: this said ~1 534, on the same stale baseline. Every later task's absolute count in this plan is low by the same arithmetic — read the delta each task adds, not the total.)* *(corrected in task 4: measured, not projected. The file count does not move — both files this task touches already existed — and the delta is +13 on task 3's 1 566.)* *(corrected again after review: **74 files / 1 594 tests**. The review round added fifteen tests and touched `sw-harness.ts` and `eslint.config.js`, neither of which is a new file. Delta +28 on task 3's 1 566.)*

---

## Task 5: Activate — the versioned purge

The requirement: *a versioned cache with an explicit activate-time purge of old versions*. Two purges, because there are two ways stale bytes accumulate. **Between versions:** every cache whose name is not `CACHE` goes, which is what `VERSION` is for — bump it and the next activate takes the whole previous world with it. **Within a version:** every entry that is not in the manifest install just wrote goes, which is what keeps a cache from growing by one build's assets on every deploy. Both matter on iOS, where an origin over its storage budget is evicted wholesale rather than trimmed.

One wrinkle, stated in a comment rather than engineered around: the new worker installs into the same cache the old one is still serving from, so for the seconds between install and activate the cached shell is the *new* HTML. Its assets are already cached by then, and the only reader of the cached shell is an offline navigation, so the window is both correct and nearly unreachable.

**Files:**
- Modify: `web/public/sw.js`
- Modify: `web/src/test/sw.test.ts`
- Modify: `web/src/test/sw-harness.ts`, `web/src/test/sw-harness.test.ts` *(added in task 5 — see correction 1 below)*

> **Corrected in task 5, after mutating the shipped worker against seventeen
> mutants. The two code fences below (steps 1 and 3) are the pre-implementation
> draft; the files in the tree are what shipped and are authoritative.**
>
> 1. **Two more places the harness passed a worker that cannot run on a phone**,
>    found by writing this task's tests against it. Both are `FakeCache`, both
>    are pinned in `sw-harness.test.ts`, and the second is what makes two of
>    this task's mutants killable at all.
>    - **`match()` handed back the stored `Response` object.** A real
>      `Cache.match` constructs a new one per call, so reading a body never
>      spends the cache's copy. `purge()` reads the manifest with `.json()`, so
>      under the old fake one activate consumed the record the next one prunes
>      against: a second activate threw where a browser would not, and every
>      assertion about the manifest *after* an activate was reading a corpse. It
>      now answers `?.clone()`.
>    - **`delete()` resolved in a microtask, which made a missing `await`
>      invisible.** Microtasks drain before an `await`ed event settles, so
>      `await self.caches.delete(name)` and `self.caches.delete(name)` were
>      indistinguishable here and very different on a phone — an unawaited delete
>      is outside `waitUntil`, and the browser may stop the worker with the cache
>      still there. Both deletes now settle on a macrotask, and the two
>      unawaited-delete mutants die.
> 2. **The keep-set is absolute URLs, not pathnames.** Step 3's draft compared
>    `new URL(request.url).pathname` against a set of the manifest's pathnames,
>    which keeps any **cross-origin** entry that merely shares a pathname with
>    something precached — a CDN copy of `/assets/index-<hash>.js` survives every
>    purge for ever. Nothing this worker writes can look like that (`isAssetUrl`
>    refuses another origin), which is exactly why the purge is the only thing
>    that would ever remove one: `VERSION` is bumped by hand, so for every deploy
>    that does not bump it the entry purge is all that reaches what the *previous
>    build of this same file* cached. The record still holds pathnames — one
>    spelling across the manifest, the cache and task 7's lookup — and `purge()`
>    resolves them through `scopeOrigin()` before comparing, the same notion of
>    "us" `isAssetUrl` uses.
> 3. **Eleven tests, not five.** The five below all shipped (with `worker.cache`
>    in place of the literal `"alfred-shell-v1"`, per task 3's correction 6). The
>    six added are the ones that bite: *keeps every byte this install wrote,
>    fonts and all* (the only thing that catches a keep-set one entry short —
>    the fonts are what install appends last); *leaves the activate after it
>    something to prune against* (a purge that rewrites the record with what it
>    happened to keep passes everything else, then empties the cache one activate
>    later); *claims the open pages only once the purge has finished* (see 4);
>    *keeps a cache it has no record for rather than emptying it* (the plan's
>    empty-cache test states that intention against an **empty** fixture, where
>    dropping everything and keeping everything look identical); *deletes an API
>    response nothing should ever have cached*; and *deletes a cross-origin entry
>    whose path matches one it keeps*. The last two are decision 3's recovery
>    path, and each is the sole killer of a mutant: an `/assets/`-only prune, and
>    the draft's pathname comparison.
> 4. **The ordering of `purge().then(() => claim())` had to be tested, not
>    assumed.** A real `ExtendableEvent` waits on every promise and the harness
>    was fixed in task 3 to match — which is precisely why `waitUntil(purge());
>    claim()` and `waitUntil(Promise.all([purge(), claim()]))` pass a
>    `claimCalls === 1` assertion while handing the open pages a worker whose
>    cache is still being emptied underneath them. The shipped test records
>    `worker.claimCalls` at the moment of each deletion (by wrapping the store
>    Maps' own `delete`, which is all public harness surface) and expects
>    `[0, 0]` — a list rather than an `.every()`, which no deletion at all would
>    satisfy.
> 5. **`withFonts()` moved to module scope** in `sw.test.ts`, unchanged, so the
>    activate tests can prove the purge keeps a *full* cache rather than the two
>    assets `shell()` has. No test of task 4's changed.
>
> **Corrected again after review** (two reviews: one against this plan, one on
> quality). The seventeen mutants above were all confirmed killed independently,
> and both sole-killer claims held — but the reviews found four ways the purge
> could still fail as a recovery path, three of them unreachable from any test:
>
> 6. **`if (!listed) return` covered one of the three ways a record can be
>    untrustworthy.** Absent was handled; **wrong shape** and **unreadable** were
>    not, and both are worse than absent.
>    - A record that parses but is not an array **silently empties the cache**:
>      `[MANIFEST, ...record]` spreads a JSON *string* into single characters,
>      none of which resolve to a real key, so the shell, the entry chunk and the
>      stylesheet are all deleted, nothing rejects, and the first anyone hears of
>      it is a blank screen on a phone with no network. Not live today —
>      `precache()` is the only writer and always writes an array — but it goes
>      live the moment the record's shape changes, and `{version, files}` is the
>      obvious next edit.
>    - A record that is not readable as JSON **rejects `purge()`**, so `claim()`
>      never runs and `waitUntil` gets a rejected promise — *after* the version
>      purge has deleted the old cache and *before* the entry purge runs. Old
>      world gone, stale entries kept, pages left on the previous worker. HTML is
>      the live shape here: `/__shell-manifest` is not a real endpoint, so
>      `mount_spa` answers it with `index.html` and a 200.
>
>    All three now read the same way — **keep everything, and let the next
>    install rewrite the record** — because deleting on a record you cannot read
>    is guessing, and the thing it guesses with is the only offline copy of the
>    app. One `try`/`catch` and one `Array.isArray`, and a test for each shape.
> 7. **Neither purge loop isolated a failure.** Both were bare sequential
>    `await`s, so one rejecting `delete` aborted every cache after it, skipped
>    the entry purge and skipped the claim: one transient error and the recovery
>    path does nothing at all. Now caught per delete. This was *unreachable from
>    the suite* — `settled()` could not reject — so `sw-harness.ts` gained
>    `refuseDelete(key)` (a cache name for `caches.delete`, a pathname for
>    `Cache.delete`, told apart by the slash) and `settled()` learned to reject
>    rather than throw inside a timer callback. Two tests, two sole-killed
>    mutants.
> 8. **The entry purge was never given more than one stale entry**, so
>    `await cache.delete(request); break;` survived the whole suite while a real
>    deploy leaves at least two (the old chunk *and* the old stylesheet), which
>    under that mutant accumulate one build per activate for ever. The fixture
>    now plants both, plus an `index-<hash>.js.map` sidecar whose URL *extends* a
>    kept one — which kills a keep-set matched by `startsWith` instead of `has`.
> 9. **Two narrower things, fixed and documented rather than engineered
>    around.** The entry keys are now snapshotted *before* the record is read:
>    a worker installing concurrently writes its entries first and its record
>    last, so reading the record first meant its entries could be deleted by this
>    activate while its install had already resolved and would never refetch
>    them. The swap makes the losing window a strict subset of what it was; it
>    does not close it, and closing it needs a lock two workers do not have. And
>    the entry purge also reaches whatever task 7 runtime-caches — intended (the
>    record is what this build needs, a runtime entry is re-fetchable, and the
>    alternative is a cache with no ceiling), but a decision task 7 inherits
>    rather than makes, so `purge()` says so.
> 10. **Three documentation defects this task created or inherited.** The
>    harness's `cacheName()` comment still said the `alfred-` prefix "is the
>    invariant task 5's purge and task 11's kill switch both key on" — this is
>    the commit that stopped the purge keying on it, and the claim was wrong
>    about the mechanism besides. `web/README.md` restated the literal
>    `alfred-shell-v1` in prose, where the tests deliberately read it from
>    `VERSION`. And **eight** tests read the stored manifest with a bare
>    `!.json()`, which leaves the cache's own copy consumed — harmless until
>    correction 1's `match()` clone, which *throws* on a disturbed body, so the
>    next test that reads a body and then activates would get a synchronous error
>    no browser produces. All eight now `.clone()`.

- [ ] **Step 1: Write the failing tests**

```ts
describe("activate", () => {
  it("deletes every cache this worker does not own", async () => {
    const worker = shell();
    worker.store.set("alfred-shell-v0", new Map());
    worker.store.set("workbox-precache-v2", new Map());
    await worker.install();
    await worker.activate();
    expect([...worker.store.keys()]).toEqual(["alfred-shell-v1"]);
  });

  it("deletes the assets of a build this worker did not precache", async () => {
    const worker = shell();
    await worker.install();
    // The previous deploy's entry chunk, left in the cache by the previous
    // worker. Nothing will ever ask for it again: its URL is content-hashed.
    worker.store
      .get("alfred-shell-v1")!
      .set("https://alfred.example.com/assets/index-OLD.js", new Response("old"));
    await worker.activate();
    expect(worker.keys()).not.toContain("/assets/index-OLD.js");
    expect(worker.keys()).toContain("/assets/index-CY0Sh5Q2.js");
  });

  it("keeps its own record of what it cached", async () => {
    const worker = shell();
    await worker.install();
    await worker.activate();
    // Pruning the manifest would leave the next activate with nothing to prune
    // against, and it would drop the whole cache.
    expect(worker.keys()).toContain("/__shell-manifest");
  });

  it("takes control of the pages already open", async () => {
    const worker = shell();
    await worker.install();
    await worker.activate();
    expect(worker.claimCalls).toBe(1);
  });

  it("survives activating with an empty cache", async () => {
    // The kill switch has run, or a user cleared site data, or this is the
    // first activate after an install that failed. Nothing to prune against,
    // and dropping everything would be the wrong reading of that.
    const worker = shell();
    await expect(worker.activate()).resolves.toBeUndefined();
    expect([...worker.store.keys()]).toEqual(["alfred-shell-v1"]);
  });
});
```

- [ ] **Step 2: Run them and read the failure**

```bash
npx vitest run src/test/sw.test.ts -t "activate"
```

Expected: FAIL — there is no `activate` listener, so nothing is deleted and `claimCalls` is 0. *(Corrected in task 5: of the eleven shipped, **nine fail** here and **two pass vacuously** — `keeps its own record of what it cached` and `keeps a cache it has no record for rather than emptying it`, both of which a worker that deletes nothing at all satisfies by doing nothing. Note the filter catches two of task 3's install tests as well, whose names contain the word "activate"; 13 run, not 11.)*

- [ ] **Step 3: Add `purge()` and the listener**

```js
async function purge() {
  /* Between versions: bump VERSION and the next activate takes the whole
     previous world with it. The `alfred-` prefix is not filtered on — anything
     this origin holds that is not the current cache is not ours to keep, and
     that includes whatever a previous experiment (or a Workbox trial) left. */
  for (const name of await self.caches.keys()) {
    if (name !== CACHE) await self.caches.delete(name);
  }

  /* Within a version: every entry that is not in the manifest install just
     wrote. Asset URLs are content-hashed, so a deploy's worth of them is dead
     the moment a new worker activates, and on iOS an origin over its storage
     budget is evicted whole rather than trimmed.

     Note the window this leaves: the new worker precaches into the cache the
     old one is still serving from, so between install and activate the cached
     shell is the new HTML. Its assets are already cached by then, and the only
     reader of a cached shell is an offline navigation. */
  const cache = await self.caches.open(CACHE);
  const listed = await cache.match(MANIFEST);
  if (!listed) return;
  const keep = new Set(await listed.json());
  keep.add(MANIFEST);
  for (const request of await cache.keys()) {
    if (!keep.has(new URL(request.url).pathname)) await cache.delete(request);
  }
}

self.addEventListener("activate", (event) => {
  event.waitUntil(purge().then(() => self.clients.claim()));
});
```

- [ ] **Step 4: Run green**

```bash
npx vitest run src/test/sw.test.ts
```

Expected: PASS, 23 tests *(corrected in task 3: 15 as planned; +8 from task 3's extra tests)* *(corrected in task 5: **53** in `sw.test.ts` — task 4's 42 plus this block's 11 — and `sw-harness.test.ts` moves 12 → 14. Then mutate: a green here is worth reading only after the mutants in step 4a have died.)* *(corrected again after review: **57** in `sw.test.ts` and **15** in `sw-harness.test.ts` — the review round added four activate tests and one harness test, listed in corrections 6 to 8.)*

- [ ] **Step 4a: Mutate the worker, and keep only the tests that bite** *(added in task 5)*

Every task in this phase has had surviving mutants found in review. Copy `public/sw.js` to a scratch directory outside the repo, mutate one thing at a time, copy it back over `public/sw.js`, `rm -rf node_modules/.vite` (the tests import the worker through `?raw`, which Vite caches), run `npx vitest run src/test/sw.test.ts`, and restore. The **twenty-six** that were run, all killed: no version purge; an `alfred-` prefix filter on it; an unawaited `caches.delete`; an uncaught one; deleting its own cache too; dropping everything when there is no record; `MANIFEST` missing from the keep-set; an empty keep-set; a keep-set one entry short; the draft's pathname comparison; a keep-set matched by `startsWith`; no entry purge; an unawaited `cache.delete`; an uncaught one; a `break` after the first entry deleted; an `/assets/`-only prune; the record rewritten empty after the prune; a truthiness test in place of `Array.isArray`; no `try`/`catch` around the record read; a `catch` that drops everything; a `catch` that continues with an empty keep-set; `claim()` beside `purge()`; `Promise.all([purge(), claim()])`; no `claim()`; and the whole thing outside `waitUntil()`.

Two are **equivalent** mutants, recorded so the next reader does not chase them: reverting the keys-before-record snapshot (correction 9 — the race it narrows is inexpressible with one worker per store), and dropping the `listed &&` guard (an absent record then reaches `undefined.json()`, which the `catch` reads as untrustworthy and answers identically; the explicit form stays because "absent" should be said, not inferred from a `TypeError`).

- [ ] **Step 5: Commit**

```bash
git add web/public/sw.js web/src/test/sw.test.ts web/src/test/sw-harness.ts web/src/test/sw-harness.test.ts
git commit -m "feat(web): purge every other cache and every stale entry on activate"
```

Counts after this task: **73 files / ~1 539 tests** *(corrected in task 5: measured, **74 files / 1 607 tests**. No new file — all four this task touches already existed — and the delta is +13 on task 4's 1 594: 11 in `sw.test.ts` and 2 in `sw-harness.test.ts`.)* *(corrected again after review: **74 files / 1 612 tests**, delta +18. The review round added five tests — four activate, one harness.)*

---

## Task 6: What the worker refuses to touch

This is constraint 3 of the header comment and decision 3 of the plan, and it is the task most worth over-testing. **The worker never calls `respondWith()` for `/api/*`, `/health`, `/ws*`, a cross-origin URL, or any method but GET.** Not "network-only" — never seen, so nothing downstream of this branch can cache one.

`/ws` and `/ws/telemetry` are in the guard although a WebSocket upgrade never reaches a `fetch` handler. They are named so nobody removes a guard they believe is dead, and — more usefully — so nobody adds a *caching* branch they believe is needed.

**Files:**
- Modify: `web/public/sw.js`
- Modify: `web/src/test/sw.test.ts`
- Modify: `web/src/test/sw-harness.ts`, `web/src/test/sw-harness.test.ts` *(added in task 6 — see correction 2)*
- Create: `web/src/test/sw-server-routes.test.ts` *(added in task 6's review round — see correction 10)*
- Modify: `web/README.md` *(added in task 6 — the worker's section describes the refusal)*
- Modify: `web/tsconfig.app.json`, `web/tsconfig.node.json` *(added in task 6's review round — the new test reads files off disk, so it belongs to the Node project, as `icons.test.ts` does)*

> **Corrected in task 6, after attacking the draft guard with thirty mutants
> across two subjects. The code fences below (steps 1 and 3) are the
> pre-implementation draft; the files in the tree are what shipped and are
> authoritative.**
>
> 1. **The refusal is a named function, `isRefused(request)`, not a chain of
>    early returns inside the listener.** The draft's shape cannot be tested at
>    all in this commit, and the plan knew it — step 2 says so and defers to task
>    7. But deferring loses the half that matters. Through a dispatched event,
>    "refused" and "not handled" are **both** `undefined`, so the paths the guard
>    must NOT refuse are unassertable *even after* task 7 lands: `/manifest.json`
>    is not refused and is not answered either, and no black-box test can tell
>    that from a guard that has quietly swallowed it. A named function can be
>    called, and both directions can be said. It is also what lets the next
>    branch be added *after* the refusal rather than beside it.
> 2. **Three additions to the harness**, each forced by a fixture the draft had
>    no way to write.
>    - `loadWorker(source, { expose: ["isRefused"] })` → `Harness.inner`. The tail
>      `;return { isRefused };` is appended to the shipped bytes and evaluated in
>      the same `new Function` call, so the source that registered the listeners
>      is still the source that ships. A name the file does not declare is a
>      `ReferenceError` at load — the same red-when-renamed reach `cacheName()`
>      already makes into `VERSION`.
>    - `LoadOptions.scope`, which moves the whole instance's origin. Without it
>      the harness serves exactly one origin and it is https, so **`url.protocol
>      !== "https:"` survives every possible test** — a worker that refused its
>      own origin on `http://localhost` (which is `npm run preview`, and this
>      box's own container port; a worker is a secure context, and localhost is
>      the one http origin that qualifies). With it, that mutant dies.
>    - `fetchRequest(spec)` / `FetchRequest` / `RequestSpec`, exported, plus
>      `headers` on `request()`. Real `Headers`, so `get("range")` is as
>      case-insensitive as a browser's, and the same builder feeds both the
>      dispatched event and the direct call so the two cannot drift.
> 3. **Three refusals the draft did not have.** Each closes a hole that was
>    measured, not imagined.
>    - **Protocol.** `new URL("blob:https://alfred.example.com/<uuid>").origin`
>      is `https://alfred.example.com` — **equal to ours**. The draft's origin
>      comparison reads as if it settled the question and does not. Unreachable
>      today (a worker is only ever dispatched for http(s)), and named for the
>      same reason `/ws` is: the comparison under it looks sufficient.
>    - **`Range`.** A ranged GET asks for part of a body; task 7's cache-first
>      branch answers with a whole one and a 200 — the canonical way a worker
>      stalls a media element in Safari. Nothing here sends one (no `<audio>` or
>      `<video>` in `src/`; a spoken reply arrives over `/ws` as base64 and is
>      decoded through an `AudioContext`), so this is the second deliberately
>      dead guard in the file, and the only one this task adds by choice.
>    - **A percent-encoded separator.** `/assets/..%2fapi/x` is a path under
>      `/assets/` to the URL parser and a traversal once uvicorn has decoded it,
>      and **neither of the two `%2F` fixtures contains `/api/` at all**, so no
>      path clause reaches them. `isAssetUrl` already refuses this shape at
>      install time; this is that refusal at the other door, and it is what lets
>      task 7 keep `startsWith(ASSETS)` rather than needing `isAssetUrl` (see
>      correction 6). Dot segments need no rule — `new URL` has already resolved
>      `/assets/../api/x` into `/api/admin/overview`, which the tests assert
>      rather than assume.
>      *(Corrected in task 6's review round — see correction 9. This rule was one
>      escape out of many and the whole path list was defeatable without it:
>      `/%68ealth` returns the real health JSON, measured. It is gone, replaced
>      by decoding the pathname the way uvicorn does before the list is applied
>      at all, plus a dot-segment rule that covers `%2f` traversal as an
>      instance. The last sentence was also wrong in the other direction — a dot
>      segment DOES survive `new URL` when its separator is encoded.)*
> 4. **`/ws` is anchored (`=== "/ws" || startsWith("/ws/")`), not a `/ws`
>    prefix** — the same lesson task 4's review drew about `/assets/`, and the
>    same reason: `<Route path="*">` makes every unknown path a navigation the
>    SPA answers, so a guard that widens does not cache less, it hands a phone
>    with no network a browser error page instead of the app.
>    *(Corrected in task 6's review round: that reason is true of `/api/`,
>    `/health` and `/docs`, and **false of `/ws`**. `mount_spa`'s
>    `_NON_SPA_PREFIXES = ("api/", "ws")` makes every `/ws*` path a 404 — measured:
>    `/ws-telemetry`, `/wsx` and `/ws` are all 404 `application/json` — so
>    widening this one costs nothing a user could see. It stays anchored anyway,
>    for the reason below it rather than the one written here: the server's
>    prefix and the worker's are two lists that must not drift apart silently,
>    and `sw-server-routes.test.ts` is what now says so. The fixture that carries
>    the mutant is honest about this too.)*
> 5. **The first fixture written for that was wrong, and only the mutation run
>    said so.** `/workshop` was chosen as the path a `/ws` prefix would swallow.
>    It does not begin `/ws` — w, o, r — so it killed nothing, and the prefix
>    mutant survived a suite that had been written specifically to catch it. The
>    fixture is now `/ws-telemetry` (a plausible mistyping of the real socket),
>    `/workshop` stays beside it as the looser-guard case,
>    and one test states both directions explicitly — the two sockets refused,
>    three near-misses not — because each direction is invisible on its own.
>    *(Corrected in task 6's review round: measured, `/workshop` is 200
>    `text/html` — the app — and `/ws-telemetry` is a 404, so it is `/workshop`
>    that carries the real cost of a widened guard and `/ws-telemetry` that
>    carries only the mutant. Both are kept and the test now says which is
>    which.)*
> 6. **What task 7 inherits from this, and it is now two things.** Task 5 wrote
>    down that the entry purge also reaches whatever task 7 runtime-caches; this
>    adds that **task 7's `startsWith(ASSETS)` is safe only because `isRefused`
>    runs first**. It is what turns away the cross-origin URL, the ranged GET and
>    the dot-segment path before the pathname is ever trusted as a cache key. If
>    the refusal is ever moved after a branch, that branch needs `isAssetUrl`.
>    *(Corrected in task 6's review round: this said "the `%2f` path". That rule
>    is gone — correction 9 — and the one that covers `/assets/..%2fapi/x` now is
>    the dot-segment rule, which is what task 7 actually inherits.)*
> 7. **Step 2's "run them again after task 7" was done here, not deferred.**
>    Task 7's two handlers were transcribed into a scratch copy of the worker
>    (outside the repo) and the whole suite re-run against it. So the black-box
>    half is a live regression guard from the moment task 7 lands, which is what
>    the plan claimed and had no way to check.
>    *(Re-measured in task 6's review round against the rewritten predicate: 162
>    pass under the overlay, and of eleven mutants run there, **eight** fail at
>    least one test in the black-box block. The three that do not are the ones
>    only a direct call can see — `no-dot-rule`, `decode-fail-permitted` and
>    `api-prefix-no-slash` — and the last of those is the asymmetry itself: a
>    mutant that only ever refuses MORE makes more requests fall through
>    untouched, which is what every black-box row asserts. Numbers in step 4a.)*
> 8. **The "AS SHIPPED TODAY" paragraph is gone, replaced by one line** ("Three
>    rules, and everything in the file is under them"), because deleting it
>    outright would also have deleted the sentence that frames the three rules as
>    *policy* rather than as a description of the code. Rule 1 lost its "once
>    there is a fetch handler" clause and now names `isRefused` as where it is
>    decided.
>
> **Corrections 9-14 are from the review round, which measured against the live
> app on :8081 rather than reasoning about it. Six of the eight findings were
> things this task had asserted and not checked.**
>
> 9. **One percent-escape defeated the entire path list, and the `%2f` rule was
>    one escape out of many.** Measured: `/%68ealth` and `/healt%68` both return
>    the real health JSON, `/%61pi/admin/overview` and `/ap%69/admin/overview`
>    both return the real API's 401. uvicorn `unquote()`s the whole path before
>    Starlette routes it, so the string the worker reads and the string the
>    server routes on are not the same string. **The list is now applied to the
>    decoded pathname**, a pathname that cannot be decoded is refused rather than
>    guessed at (`decodeURIComponent` throws where Python's `unquote` does not —
>    `%zz`, a lone `%`, non-UTF-8 bytes — and a path the two read differently is
>    exactly one not to answer from a cache), and a decoded form with a `.` or
>    `..` segment is refused. The `%2f` special case is **deleted**, not kept: it
>    is an instance of the dot-segment rule, and it also refused `/%2Fhealth`,
>    which decodes to `//health` and is the app.
> 10. **`/docs`, `/docs/oauth2-redirect`, `/redoc` and `/openapi.json` were
>    missing, and three of them are `text/html` on this origin.** FastAPI serves
>    them by default and `create_app` overrides none of the four kwargs, so a
>    navigation to `/docs` would have passed `isShell()` and overwritten the
>    cached shell — every offline launch afterwards rendering Swagger UI until
>    the next online navigation. There is now a test for that exact sequence, and
>    a new cross-language file, **`sw-server-routes.test.ts`**, which reads
>    `web_server.py` and `spa.py` off disk and fails if `create_app` ever moves
>    one of those URLs out from under the worker's list. It is in the **Node**
>    project (`tsconfig.node.json`) for the reason `icons.test.ts` is: `?raw`
>    cannot reach outside `web/`, which is Vite's `server.fs` boundary.
>    They are **exact paths, not a `/docs/` prefix** — measured, `/docsx`,
>    `/docs/foo`, `/redocx` and `/openapi.json/x` are all 200 `text/html`, the app.
> 11. **Ten of the fourteen black-box rows could not fail.** `RequestSpec`
>    defaulted `mode` to `"no-cors"`, so the `navigate` branch task 7 will add
>    could never have claimed them and the row would have passed against any
>    guard at all. Every row now carries the mode the browser really sends — API
>    calls are `fetch`, so `cors`; `/health`, `/docs` and the sockets are reached
>    by a human in the address bar, so `navigate` — and **the same table is
>    driven through `isRefused` directly** in a block that is live today. The
>    commit message claimed the black-box block was the regression guard; it is
>    the direct block that is, and the plan says so here because the commit
>    message is immutable.
>    **Every path in that table is now a route that exists**, checked against the
>    live app rather than invented: a real route answers 401 (or 200 for
>    `/api/auth/status`) and a made-up one answers 404, which is how the check is
>    made. The draft fence in step 1 called `/api/admin/streams/events` an event
>    stream; there is no SSE anywhere in this repo — the Workshop polls
>    `GET /api/admin/streams/{name}?count=50` (`core/channels/admin_api.py:394`)
>    and the two sockets are real upgrades that never reach a `fetch` handler.
>    The fixture is `user_requests`, which is what `src/App.test.tsx` mocks;
>    `chat`, tried first, is not in `STREAM_CATALOG` and would have been the same
>    mistake again.
> 12. **Three smaller ones.** `harness.request()` built its URL against the
>    module-level `ORIGIN` and not the instance's scope, so a scoped worker's
>    bare path silently addressed the wrong origin; it now defaults to the
>    instance origin. `expose` is fenced by an `EXPOSABLE` allowlist, so the seam
>    reaches one named internal rather than any identifier in the file, and
>    `Harness.inner` is typed, which removed the `as` casts at the call sites.
>    The refusal literals are named `NEVER` / `NEVER_UNDER` and read by the
>    cross-language test; note that the same list exists in
>    `core/channels/spa.py` and in `web/vite.config.ts`'s dev proxy and the three
>    **disagree** — deliberately not unified here, because the dev proxy's copy
>    is a different question from the worker's.
> 13. **Two claims in this plan and one in the README were false and are
>    corrected above.** `/ws-telemetry` is not "an ordinary path" (correction 5),
>    the `/ws` anchoring reason does not hold for `/ws` (correction 4), and
>    `/%2e%2e%2fapi/x` does **not** reach the real API — measured, it is 200
>    `text/html`, because Starlette does not normalise dot segments. The
>    dot-segment rule is kept on the honest reason instead: the raw pathname of
>    `/assets/..%2fapi/x` still begins `/assets/`, the prefix task 7 reads as a
>    cache key, so a server that ever did normalise would have it cache a 401 as
>    a hashed chunk.
> 14. **The re-run found three survivors, and one of them was a redundant
>    rule.** They are worth reading as three different failures. (a)
>    `no-raw-term` — applying the list to the decoded form *and* the raw one:
>    **no fixture can kill this, because none exists.** Every entry in both lists
>    is escape-free and decoding rewrites nothing but escapes, so a raw pathname
>    the list matches decodes to one it still matches; the raw term could refuse
>    nothing the decoded one does not. It is deleted, and a test pins the reason
>    so it is not added back. (b) `dot-only-dotdot` — the lone `.` arm had no
>    fixture; `/%2e%2fapi/admin/overview` is now one. (c) `origin-prefix` —
>    `url.origin.startsWith(scopeOrigin())` survived every origin row here,
>    because a host that has ours as a *prefix* was not among them;
>    `https://alfred.example.com.evil.invalid/` is now a fixture. Each is the
>    thing this task was briefed to avoid — an anchor that happens to hold — and
>    only mutation found them.

- [ ] **Step 1: Write the failing tests**

```ts
describe("what the worker refuses to touch", () => {
  it.each([
    "/api/admin/overview",
    "/api/auth/status",
    "/api/auth/login/begin",
    "/api/admin/streams/events",
    "/api/actions/abc/confirm",
    "/health",
    "/ws",
    "/ws/telemetry",
  ])("leaves %s entirely to the network", async (path) => {
    const worker = shell();
    await worker.install();
    // `undefined` means respondWith was never called. This is the assertion the
    // whole caching policy rests on: a cached API response is the client
    // stating a server fact it has not just read, with a half-life.
    await expect(worker.request({ path })).resolves.toBeUndefined();
  });

  it("caches nothing when an API call goes past it", async () => {
    const worker = shell();
    await worker.install();
    const before = worker.keys();
    await worker.request({ path: "/api/admin/overview" });
    expect(worker.keys()).toEqual(before);
  });

  it("leaves a navigation-mode request to an API path alone too", async () => {
    // /api/* is checked before the navigation branch, deliberately: a hand-typed
    // API URL in the address bar arrives with mode "navigate".
    const worker = shell();
    await worker.install();
    await expect(
      worker.request({ path: "/api/admin/overview", mode: "navigate" }),
    ).resolves.toBeUndefined();
  });

  it("leaves every non-GET alone", async () => {
    const worker = shell();
    await worker.install();
    for (const method of ["POST", "PUT", "DELETE"]) {
      await expect(worker.request({ path: "/", method, mode: "navigate" })).resolves.toBeUndefined();
    }
  });

  it("leaves another origin alone", async () => {
    const worker = shell();
    await worker.install();
    await expect(
      worker.request({ path: "/assets/index-CY0Sh5Q2.js", origin: "https://example.invalid" }),
    ).resolves.toBeUndefined();
  });

  it("leaves the unhashed public files alone", async () => {
    // /manifest.json, /icon.svg, /apple-touch-icon.png. All three are read
    // while the phone is online (at install time, and by the browser chrome),
    // all three are revalidated by SpaCacheMiddleware's third tier, and a
    // stale manifest is the one thing a reinstall cannot fix.
    const worker = shell();
    await worker.install();
    for (const path of ["/manifest.json", "/manifest-light.json", "/icon.svg", "/apple-touch-icon.png"]) {
      await expect(worker.request({ path })).resolves.toBeUndefined();
    }
  });
});
```

- [ ] **Step 2: Run them and read the failure**

```bash
npx vitest run src/test/sw.test.ts -t "refuses"
```

Expected: FAIL — there is no `fetch` listener yet, so `request()` resolves `undefined` for *everything* and these tests pass vacuously. **That is not good enough:** run them again after task 7 and confirm they still pass with the handler in place. Note this in the commit message so a reviewer knows the order.

*(Corrected in task 6: the shipped block fails for a better reason first —
`ReferenceError: isRefused is not defined`, out of `loadWorker`, because the
predicate block calls the function directly. Only the second block, the one
dispatched through `request()`, is vacuous, and correction 7 above is how that
was checked rather than deferred.)*

- [ ] **Step 3: Add the fetch listener with the refusals and nothing else**

*(Corrected in task 3: two edits to the block below. It read `new URL(self.registration.scope).origin`
inline — use task 3's `scopeOrigin()`, which is that expression and is already what
`isAssetUrl` reads, so this file keeps one notion of "us". And **delete the "AS SHIPPED
TODAY" paragraph from the top of `sw.js`** in this commit: it says the file answers no
request and has no fetch handler, which stops being true here.)*

```js
self.addEventListener("fetch", (event) => {
  const request = event.request;
  /* A POST is never a shell request, and the Cache API cannot store one anyway. */
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  /* Nothing in this client loads a cross-origin URL, and if one ever appears it
     is not the shell and must not land in the shell's cache. */
  if (url.origin !== scopeOrigin()) return;

  /* Rule 1 of the header. Checked before anything else, including the
     navigation branch — a hand-typed API URL arrives with mode "navigate".

     /ws and /ws/telemetry cannot reach a fetch handler at all: a WebSocket
     upgrade is not a fetch. They are named so nobody deletes a guard they think
     is dead, and so nobody adds a caching branch they think is missing. */
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname === "/health" ||
    url.pathname.startsWith("/ws")
  ) {
    return;
  }
});
```

- [ ] **Step 4: Run green**

```bash
npx vitest run src/test/sw.test.ts
npm run lint
```

Expected: PASS, 36 tests *(corrected in task 3: 28 as planned; +8)* *(corrected in task 6: **108** in `sw.test.ts` — task 5's 57 plus this block's 51 — and `sw-harness.test.ts` moves 15 → 19. Then mutate: a green here is worth reading only after the mutants in step 4a have died.)* *(corrected again after review: **134** in `sw.test.ts` — task 5's 57 plus this block's 77 — `sw-harness.test.ts` 15 → 21, and a third file, `sw-server-routes.test.ts`, with 7. The review round added 26 in `sw.test.ts`, 2 in the harness's and all 7 of the new file's.)*. ESLint silent.

- [ ] **Step 4a: Mutate the guard, both on its own and under task 7** *(added in task 6)*

Same loop as task 5's step 4a — scratch copy outside the repo, one mutation at a
time, `rm -rf node_modules/.vite` between runs (the tests import the worker
through `?raw`, which Vite caches), restore afterwards.

**Twenty-four against the shipped worker, twenty-two killed:** no method check; only
POST refused; no `Range` check; no protocol check; **the protocol allowlist
narrowed to `https:`** (the sole killer is the `http://localhost` scope fixture,
and it was written *because* this one survived first time); no origin check; the
origin compared on the href instead (sole killer: *does not refuse credentials
on our own host*); no encoded-separator check; that check made case-sensitive;
that check read off the whole href rather than the pathname (sole killer: *does
not refuse an encoded separator that is only in the query*); `/api` in place of
`/api/`; `/api/` matched with `includes`; no `/api` clause; `/health` as a
prefix; no `/health` clause; **`/ws` as a prefix — the plan's own draft** (sole
killer: `/ws-telemetry`, and see correction 5 for the fixture that did not kill
it); no `/ws` exact clause; no `/ws/` prefix clause; the whole path list dropped;
refuses nothing; refuses everything; and the listener inverted.

> **Corrected in task 6's review round.** Two things above do not survive a
> re-run. The `%2f` mutants are gone with the rule (correction 9), and one
> recorded result **did not reproduce even before that**: `no-%2f-check` kills 4
> tests and **none of them are on the black-box block**, which has no
> encoded-separator fixture at all. The other five overlay results were as
> claimed. The re-run below replaces this table; it is against the rewritten
> predicate, so the two are not comparable mutant for mutant.

**Twenty-six against the rewritten predicate, all twenty-six killed.** No
survivors, but only after three of them were fixed — see correction 14, which is
the finding. The list, with the sole killer where there is one:

| mutant | tests failing |
|---|---|
| `judge-raw-instead` (list applied to `url.pathname`, not `decoded`) | 8 |
| `list-never-consulted` (`return false`) | 24 |
| `no-dot-rule` | 3 |
| `dot-only-dotdot` (drops the lone `.`) | 1 — *the fixture added by correction 14* |
| `dot-only-dot` (drops `..`) | 3 |
| `decode-fail-permitted` (falls back instead of refusing) | 3 |
| `no-try-catch` (lets `decodeURIComponent` throw) | 3 |
| `never-drop-health` / `-ws` / `-docs` / `-oauth2` / `-redoc` / `-openapi` | 4, 4, 3, 2, 2, 2 |
| `docs-as-prefix` (`/docs/` in `NEVER_UNDER`) | 3 — incl. *does not refuse /docs/foo* |
| `api-prefix-no-slash`, `ws-prefix-no-slash` | 4, 4 |
| `startsWith-to-includes` | 1 — *does not refuse /assets/vendor/api/x.js* |
| `never-exact-to-prefix` (`NEVER` matched as prefixes) | 5 |
| `no-method-check`, `method-post-only` | 5, 4 |
| `no-range-check` | 1 |
| `no-protocol-check` | 1 — *refuses a blob: URL* |
| `https-only` | 1 — *the `http://localhost` scope fixture* |
| `no-origin-check` | 4 |
| `origin-via-href` | 2 |
| `origin-prefix` (`origin.startsWith(scopeOrigin())`) | 1 — *the fixture added by correction 14* |

Three of the six FastAPI-route mutants are killed partly by
`sw-server-routes.test.ts` rather than by a fixture, which is the point of that
file: deleting `/docs` from the list fails *"still gets docs\_url from FastAPI's
default"* as well as the fixture, so moving `docs_url` in `create_app` and
deleting it here are the same failure.

**Eleven re-run against task 7's shape**, transcribed into the scratch copy (see
correction 7): all eleven killed overall, **eight of them failing at least one
test on the black-box block** — `judge-raw-instead` (1: *leaves a percent-escaped
API path alone, in navigation mode*), `never-drop-health` (1), `never-drop-docs`
(2, including *does not write Swagger UI into the shell cache*), `never-drop-redoc`
(1), `never-drop-openapi` (1), `no-method-check` (1), `no-range-check` (1) and
`list-never-consulted` (10). The three that the black-box block does not see are
recorded in correction 7 with the reason.

Two are **equivalent** and are recorded so the next reader does not chase them:
reading the header as `"Range"` rather than `"range"` (`Headers.get` is
case-insensitive, which is the point of using a real one), and moving the
protocol check below the origin check (a blob: URL's origin *is* ours, so it
reaches the protocol check either way).

One **survives by construction and is expected to** against the shipped worker: a
listener that never consults `isRefused`. Nothing else is in the handler yet, so
both of its branches are externally identical — which is the whole reason the
predicate is called directly above. Under the task 7 overlay it dies on ten
tests (`list-never-consulted` above).

Restore the pristine file afterwards — note that a mutation script which copies
its own "pristine" back will happily restore the *overlay* if that is what it was
last pointed at.

- [ ] **Step 5: Commit**

```bash
git add web/public/sw.js web/src/test/sw.test.ts
git commit -m "feat(web): the worker never sees /api, /health or the sockets

These tests pass vacuously until task 7 adds the branches that could have
broken them; they are the regression guard from that point on."
```

*(Corrected in task 6: `web/src/test/sw-harness.ts` and
`web/src/test/sw-harness.test.ts` are in the commit too — see correction 2 — and
only the second `describe` is vacuous, so the message says which.)*

Counts after this task: **73 files / ~1 552 tests** *(corrected in task 6:
measured, **74 files / 1 667 tests**. No new file — all four this task touches
already existed — and the delta is +55 on task 5's 1 612: 51 in `sw.test.ts` and
4 in `sw-harness.test.ts`.)* *(corrected again after review: measured, **75 files
/ 1 702 tests**. There IS a new file after all — `sw-server-routes.test.ts`, see
correction 10 — and the delta on task 5's 1 612 is +90: 77 in `sw.test.ts`, 6 in
`sw-harness.test.ts`, 7 in the new one.)*

---

## Task 7: Navigation and assets

Two branches. A navigation goes to the network first and falls back to the cached shell — decision 8, and the reason a bad cache can only ever be seen by an offline phone. An `/assets/*` URL is content-hashed and served `immutable` by `SpaCacheMiddleware`, so it is cache-first, with the network filling a miss.

*(Corrected in task 7, from the branch that implemented it. Nine things, and the
first two are the ones that change what gets written.)*

1. **The `Files` list is one file short: `web/README.md`.** Every worker task
   since task 3 has updated its "The service worker" section in the same commit,
   and this task falsifies five sentences of it — "a fetch handler that so far
   only refuses", "the navigation and asset branches are a later slice", "every
   request still reaches the network untouched", "once it has a caching branch —
   it has none yet", and rule 2's "Navigations **will be** network-first". The
   line counts in that section go stale too: 156 lines of code / 427 of comment
   after this task, where the file says 128 / 323 (the comment half was already
   wrong at task 6 — measured 352 there).
2. **Step 1's block ships as 23 tests, not 9.** Seven of the extra fourteen are
   mutants step 5 would otherwise have let live (listed in 9 below); the six
   others are the positive half the task-6 correction below already asked for —
   an offline navigation to `/workshop`, `/ws-telemetry`, `/apifoo`, `/healthz`,
   `/docsx` and `/docs/foo` gets the cached shell. Those six are the first
   assertions in this file that can distinguish a wrongly-refused path from a
   correctly-unhandled one; until a branch existed, both were `undefined`.
3. **Two of the nine as written let a mutant live, and both for the same
   reason.** `refreshes the cached shell from that answer` and `fetches and
   stores an asset the precache missed` never read the response the worker
   *served*, only the copy it stored — so a handler that did `cache.put(SHELL,
   response)` instead of `response.clone()` passes both. In a browser that is a
   navigation which fails with the cache correctly written: `put` spends the body
   the page is about to read. Fixed by reading the served response FIRST and the
   stored copy second, which leaves a spent object in the store and makes
   `.clone()` on it throw.
4. **The block spells out `"alfred-shell-v1"` and
   `"https://alfred.example.com/index.html"`,** which are exactly the two things
   `worker.cache` and `ORIGIN` exist so that no test restates. As shipped it
   reads the store through a `shellInCache(worker)` helper built from both.
5. **Step 2's expected failure is right for fifteen of the twenty-three and
   wrong for the rest.** Measured by running the shipped block against task 6's
   `sw.js`: 21 fail — **15** on `Cannot read properties of undefined (reading
   'text')` (the offline table's six are among them, not beside them, which is
   how the first count of this came to say "10 and 6" and still total 21), 3 on
   the same for `'status'`, 2 on `promise resolved "undefined" instead of
   rejecting`, and 1 on `expected '<!doctype html>…' to contain 'deployed'` from
   `refreshes the shell from an online deep link` — and **2 pass**, both
   negative: `does not cache a page that is not HTML` and `leaves a path that
   merely contains /assets/ alone`. A worker with no branch caches nothing and
   answers nothing, so those two are vacuous until this task lands, in the same
   way task 6's dispatched half was.
6. **Step 3's listener snippet is written against a `request` the file does not
   bind.** The listener reads `event.request` throughout (task 6 left it that
   way), so the two branches are `event.request.mode` and
   `navigate(event.request)` / `asset(event.request, url.pathname)`.
7. ~~**`navigate()` has no deadline, and that is a decision rather than an
   omission.**~~ *(Reversed in the fix round. The argument was that the one
   request whose freshness the user is watching must not be handed a stale shell
   at ten seconds. Both reviewers landed on the same objection and it is right: a
   request that is accepted and never answered is **indistinguishable from being
   offline**, which is the judgement `fetchFresh` already makes at install, and
   the real defect was the inconsistency. An installed PWA is the worst place for
   a hang — a standalone window has no browser chrome to escape through — and
   `asset()` had no deadline either, with no argument written down anywhere.
   Both handlers now go through `timed()`; a navigation timeout is treated as
   offline and falls back to the cached shell, and for an asset it only fails
   faster. `TIMEOUT_MS` is unchanged.)*
8. **Comments in `sw.js` and `sw.test.ts` that pointed forward at "task 7" are
   now history** and were moved to the present tense (`isAssetUrl`'s cache-key
   note, `precache`'s declined-font note, `purge`'s record note, and the
   dot-segment note). *(Corrected in the fix round: this said "in the same
   commit" and was false of half of it — the four in `sw.js` were moved, the six
   in `sw.test.ts` were not, and the commit's diff to that file was 263
   insertions and 0 deletions, which is the tell. All ten are present tense
   now.)*
9. **22 mutants of the two handlers and the branch dispatch, plus one moving
   `isRefused` after both branches, plus `git show HEAD:web/public/sw.js` as a
   mutant of the whole task.** *(Corrected in the fix round, twice over. The
   claim "no survivors" was true only of that list: run against a list of ~50 it
   is 17 survivors, and the ones that lived were the ones that mattered — see
   the fix-round block below. And two attributions here were inverted:
   `isShell` → `response.ok` is killed by `does not cache a page that is not
   HTML` and by `answers a navigation straight to an asset URL once`, NOT by the
   502 test, which passes either way because that fixture named no hashed asset;
   `isAsset` → `response.ok` is killed by `refuses to store the SPA fallback
   under an asset URL`, not by the 404 test. The list also said "the eight
   tests" and named seven, which is where correction 2's arithmetic came from.)*
   The seven: the navigation with nothing cached (`if (cached) return cached;
   throw` → `return cached`), the navigation straight to an asset URL (the
   `return` between the branches — the harness throws on a second
   `respondWith`), the query'd read and the pathname-keyed round trip
   (`cache.match(pathname)` → `match(request)`, `cache.put(pathname, …)` →
   `put(request, …)`, and `url.pathname` → `url.href` at the call site), the
   `/assets` mount's real JSON 404 (the status half of the store guard), the 502
   page, and `/img/assets/logo.png` (`startsWith(ASSETS)` → `includes`).

*(**Fix round**, after two reviews that converged. Measured against the deployed
container on :8081 and against the real `dist`; the tests and the worker both
moved, so read this block as the specification of what shipped.)*

**The storage axis was the spine of it.** `cache.put` and `caches.open` can both
fail on a phone — iOS evicts an installed PWA's origin whole rather than trimming
it — and all three handlers treated every one of those failures as though the
network had failed.

1. **A failed cache write served the stale shell to an ONLINE phone.** The put sat
   inside the try whose catch is the offline fallback. Measured with a put raising
   `QuotaExceededError` on a working network: with a shell cached the navigation
   answered the CACHED html and discarded the live deploy; with nothing cached it
   threw `offline, and no shell cached` after a 200. Decision 8 exactly inverted,
   and it falsified rule 2 of the file header, `navigate()`'s own header, the
   catch comment, and the README. The sting the reviewer added is the reason the
   test came first: the one-line fix (`.catch(() => {})`, the way `precache()` and
   `purge()` already do it) **passed all 157 tests** — the suite could not tell
   the fix from the bug.
2. **A failed cache write turned a successful asset fetch into a network error.**
   No guard at all on `asset()`'s put: `QuotaExceededError` rejected the handler
   and the page got a failed request for bytes the network had returned 200 for.
   With no worker installed that chunk loads; with that one it did not.
3. **`caches.open()` was outside the try in both handlers.** A CacheStorage that
   will not open rejected before any fetch was attempted, so an online phone got
   the browser's network-error page for a navigation and a hard module-load
   failure for an asset. Both handlers now open through `openCache()`, which
   answers `undefined`, and every read goes through `cached()`, which catches.
   The standard, written into the file: **a worker that cannot use its cache must
   behave like no worker at all.**
4. **The asset branch was never tested at the mode the real app uses.** Measured
   in a browser against the real `dist`: every `/assets/*` request is
   `mode: "cors"` — chunk `destination: "script"`, stylesheet `"style"`, all eight
   woff2 `"font"` — because `index.html` marks both tags `crossorigin`. Every test
   in the `assets` block used the harness default `"no-cors"`, so mutating the
   dispatch to `mode !== "no-cors"` passed all 157 tests while, in a browser,
   routing every chunk, stylesheet and typeface through `navigate()` — an offline
   launch served the HTML shell as a JS module, the exact brick the file's
   comments describe. The shipped code was right and the suite was blind.
   `RequestSpec` now carries `destination` (a browser sets the two together), and
   the asset fixtures send what the page really sends.
5. **The shell refresh had no counterpart to `precache()`'s `urls.length === 0`
   refusal.** The exact page install refuses — 200 `text/html` from this origin
   naming no hashed asset, i.e. the reverse proxy's default page while a host is
   being edited — was written straight in by the first navigation and rendered on
   every offline launch afterwards. Mirrored now. The justification beside
   `isShell` was wrong too and is rewritten: a **captive portal** cannot intercept
   https at all, and what it does answer is 200 `text/html`, which `isShell`
   accepts. Against `response.ok` the content-type half buys exactly one thing —
   a 200 that is not HTML — and the status half buys the error page (nginx with
   `proxy_cache_use_stale` answers 502 with the app's own HTML).
6. **The shell was refreshed independently of the assets it names.** `sw.js` is
   byte-identical across most deploys, so a deploy runs no new install and no new
   activate: this refresh is the only thing that ever updates the offline copy.
   Writing the new HTML alone left the cache holding a shell naming hashes it did
   not have, and `asset()` propagates a network failure by design — so losing the
   network before the page finished fetching (a tunnel, a backgrounded app, a
   rollout mid-flight) made every offline cold launch a white screen. **Ruling:
   publish a shell only once the assets it names are cached; if any of it fails,
   keep the shell you have.** A self-consistent offline copy one deploy old beats
   a newer one that cannot boot. It runs in `event.waitUntil()` so the page is
   never blocked on it — **task 8's handshake builds on this**: what a waiting
   worker takes over is a cache whose shell and assets already agree.
7. **A 206 failed the asset outright.** `response.ok` is 200–299 and the real
   `Cache.put` rejects a 206 with a TypeError nothing caught. The harness now
   models both the 206 rejection and a programmable storage failure, and what that
   exposed is fixed at the source: `isStored(response)` is `status === 200`, which
   also declines a 204 that would have cached an empty chunk.
8. **Two comments this task shipped were false.** "The only way this file fetches
   anything" and "Every network call here gets a deadline" were both falsified by
   the bare `self.fetch` the two handlers added. True again now: `timed(input,
   init)` is that one way, and `fetchFresh` is `timed` plus `cache: "reload"` —
   which the runtime handlers deliberately do not ask for, since a hashed asset
   cannot have changed and a navigation is the page's own request.
9. **The contract at the head of this plan says `async function navigate(request)`.**
   It is `navigate(event)`: the handler needs `event.waitUntil` for 6, and reads
   `event.request` for the fetch.
10. **The harness grew five seams, all of them modelling a real failure:**
    `refusePut` (QuotaExceededError), `refuseMatch`, `refuseOpen`, a `put` that
    rejects a 206 and that lands on a macrotask like `delete` already did (so a
    dropped `await` is visible), and `waitUntil` on fetch events — **drained in a
    loop**, because a handler arms it after an await, when the listener has long
    returned and `respondWith`'s promise is what still holds the event open.
    `handed` records what `self.fetch` was given, so re-fetching `request.url`
    instead of forwarding the Request (which silently turns `redirect: "manual"`
    into `"follow"`) is visible.
11. **Mutants: 63, no survivors, and each one run against a verified-green
    baseline first** — the review's own first two runs were invalid, one on a
    reporter vitest 4 does not have and one against a tree a crash had left
    mutated, and both reported everything killed. The set covers `timed`,
    `isStored`, `openCache`, `cached`, `refresh`, `navigate`, `asset` and the
    dispatch, plus three whole-file mutants: the refusal moved after both
    branches, task 6's `sw.js` (7c7343e), and **this task's own first commit
    (8917bf3)**, which the new tests kill — the fixes above are covered, not
    merely written. Two fixtures had to change to kill anything at all: the
    error-page and wrong-content-type fixtures now NAME hashed assets (otherwise
    the `urls.length === 0` refusal stops them and the `isShell` mutants live),
    and the escaped-cache-key fixture uses `%25` rather than `%20`, because a
    decoded space is re-encoded when the key is parsed back into a URL — both
    spellings land on the same entry, so a decoding worker passed. `%25`, `%23`
    and `%2F` do not round-trip; the last is the traversal `isAssetUrl` refuses.
12. **Counts after the fix round: 75 files / 1 763 tests** — `sw.test.ts` 157 →
    **186**, `sw-harness.test.ts` 21 → **30**, `sw-server-routes.test.ts` 7.
    `sw.js` is 188 lines of code to 523 of comment.

**Files:**
- Modify: `web/public/sw.js`
- Modify: `web/src/test/sw.test.ts`
- Modify: `web/README.md` *(corrected in task 7: absent from this list — see 1 above)*
- Modify: `web/src/test/sw-harness.ts`, `web/src/test/sw-harness.test.ts` *(added in the fix round: five new seams, item 10 above)*

- [ ] **Step 1: Write the failing tests**

```ts
describe("navigations", () => {
  it("serves the live page when there is a network", async () => {
    const worker = shell();
    await worker.install();
    worker.respond("/", html("<!doctype html><title>live</title>"));
    const response = await worker.request({ path: "/", mode: "navigate" });
    await expect(response!.text()).resolves.toContain("live");
  });

  it("refreshes the cached shell from that answer", async () => {
    const worker = shell();
    await worker.install();
    worker.respond("/", html("<!doctype html><title>live</title>"));
    await worker.request({ path: "/", mode: "navigate" });
    const cached = worker.store.get("alfred-shell-v1")!.get("https://alfred.example.com/index.html");
    await expect(cached!.clone().text()).resolves.toContain("live");
  });

  it("serves the cached shell when there is no network", async () => {
    const worker = shell();
    await worker.install();
    // No route for "/" — the harness rejects, exactly as a real fetch does.
    const response = await worker.request({ path: "/", mode: "navigate" });
    await expect(response!.text()).resolves.toContain('<div id="root">');
  });

  it("serves the cached shell for a deep link too", async () => {
    // /actions/{id} is a real cold-launch path: a notification tap lands there.
    const worker = shell();
    await worker.install();
    const response = await worker.request({ path: "/actions/abc123", mode: "navigate" });
    await expect(response!.text()).resolves.toContain('<div id="root">');
  });

  it("does not cache a page that is not HTML", async () => {
    const worker = shell();
    await worker.install();
    // A captive portal answering with its own content type, an error page from
    // a proxy: whatever it is, the next offline launch must not show it.
    worker.respond("/", js("captive portal"));
    await worker.request({ path: "/", mode: "navigate" });
    const cached = worker.store.get("alfred-shell-v1")!.get("https://alfred.example.com/index.html");
    await expect(cached!.clone().text()).resolves.toContain('<div id="root">');
  });
});

describe("assets", () => {
  it("serves a hashed asset from the cache without touching the network", async () => {
    const worker = shell();
    await worker.install();
    const before = worker.fetched.length;
    const response = await worker.request({ path: "/assets/index-CY0Sh5Q2.js" });
    await expect(response!.text()).resolves.toBe("console.log(1)");
    expect(worker.fetched.length).toBe(before);
  });

  it("fetches and stores an asset the precache missed", async () => {
    const worker = shell();
    await worker.install();
    worker.respond("/assets/geist-mono-latin-500-normal-YINYabwD.woff2", font());
    await worker.request({ path: "/assets/geist-mono-latin-500-normal-YINYabwD.woff2" });
    expect(worker.keys()).toContain("/assets/geist-mono-latin-500-normal-YINYabwD.woff2");
  });

  it("refuses to store the SPA fallback under an asset URL", async () => {
    const worker = shell();
    await worker.install();
    // mount_spa answers a removed hashed file with index.html and a 200. Caching
    // that as JavaScript is how a PWA bricks itself: every launch afterwards
    // parses a page of HTML as a module and dies before the first paint.
    worker.respond("/assets/index-GONE.js", html("<!doctype html>"));
    const response = await worker.request({ path: "/assets/index-GONE.js" });
    expect(response!.status).toBe(200);
    expect(worker.keys()).not.toContain("/assets/index-GONE.js");
  });

  it("fails an uncached asset with no network, rather than inventing one", async () => {
    const worker = shell();
    await worker.install();
    await expect(worker.request({ path: "/assets/index-NEW.js" })).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run them and read the failure**

```bash
npx vitest run src/test/sw.test.ts -t "navigations"
```

Expected: FAIL — every one, on `Cannot read properties of undefined (reading 'text')`, because `request()` still resolves `undefined`.

- [ ] **Step 3: Add the two handlers**

Above the `fetch` listener:

```js
async function navigate(request) {
  const cache = await self.caches.open(CACHE);
  try {
    const response = await self.fetch(request);
    /* Refresh the shell from every online navigation, so the offline copy is
       never older than the last time this phone had a network. isShell, not
       response.ok: a captive portal answers 200 with its own page. */
    if (isShell(response)) await cache.put(SHELL, response.clone());
    return response;
  } catch {
    /* The only path on which a cached page is ever served. Everything online
       gets the deploy that is live right now — which is decision 8, and the
       reason a poisoned cache cannot reach an online phone. */
    const cached = await cache.match(SHELL);
    if (cached) return cached;
    throw new Error("offline, and no shell cached");
  }
}

async function asset(request, pathname) {
  const cache = await self.caches.open(CACHE);
  /* Cache-first is safe here and nowhere else: the filename carries a hash of
     the bytes, so the bytes behind a URL never change (SpaCacheMiddleware
     serves these `immutable` for the same reason). */
  const cached = await cache.match(pathname);
  if (cached) return cached;
  const response = await self.fetch(request);
  if (isAsset(response)) await cache.put(pathname, response.clone());
  /* A network failure propagates. There is nothing honest to serve for an
     asset that was never cached and cannot be fetched. */
  return response;
}
```

and, at the end of the `fetch` listener, after the refusals:

*(Corrected in task 6: two things this inherits. The listener's refusals are now
one call, `if (isRefused(event.request)) return;`, and the `url` the two branches
below need is parsed after it — so the block below opens with `const url = new
URL(event.request.url);`. And `startsWith("/assets/")` should be
`startsWith(ASSETS)`, the constant task 3 named; it is safe as a bare prefix test
**only because `isRefused` ran first** — that is what turns away a cross-origin
URL, a ranged GET, and a pathname whose decoded form holds a dot segment, before
the pathname is trusted as a cache key. That last one is the reason it is not
merely tidy: `/assets/..%2fapi/x` begins `/assets/` to `startsWith` and is a
traversal once decoded. Move the refusal after either branch and this needs `isAssetUrl` instead.
One more thing task 7 owes: the paths task 6 pins as **not** refused become
assertable here for the first time, so this task adds the positive side —
`/ws-telemetry` and `/workshop` get the cached shell offline, `/manifest.json`
still falls through unanswered.)*

```js
  if (request.mode === "navigate") {
    event.respondWith(navigate(request));
    return;
  }
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(asset(request, url.pathname));
  }
  /* Everything else — /manifest.json, /icon.svg, /apple-touch-icon.png — falls
     out of here unhandled. They are small, they are read while online, and
     SpaCacheMiddleware revalidates them on every load. */
```

- [ ] **Step 4: Run green, whole file**

```bash
npx vitest run src/test/sw.test.ts
```

Expected: PASS, 45 tests *(corrected in task 3: 37 as planned; +8)* — including all thirteen of task 6's, which now have branches that could have broken them. *(Corrected in task 6: 51 of task 6's, of which the 13 in the second `describe` are the ones that were vacuous. All of them were already run green against a scratch transcription of this task's two handlers — see task 6's correction 7 — so a failure here is this task's code, not that block's.)* *(Corrected in task 7: measured, **157** in `sw.test.ts` — task 6's 134 plus this block's 23. `sw-harness.test.ts` (21) and `sw-server-routes.test.ts` (7) are untouched. Mutate before believing the green: the list is correction 9 at the head of this task.)* *(Corrected in the fix round: **186** in `sw.test.ts` and **30** in `sw-harness.test.ts`.)*

- [ ] **Step 5: Commit**

```bash
git add web/public/sw.js web/src/test/sw.test.ts
git commit -m "feat(web): network-first navigations, cache-first hashed assets"
```

Counts after this task: **73 files / ~1 561 tests** *(corrected in task 7: measured, **75 files / 1 725 tests**. No new file — all three this task touches already existed — and the delta is +23 on task 6's 1 702, all of them in `sw.test.ts`.)* *(corrected again in the fix round: **75 files / 1 763 tests**, +61 on task 6.)*.

---

## Task 8: The update handshake

Decision 7: a new worker waits, and the user takes it. The worker's half is two lines and one absence — `install` must never call `skipWaiting()`, and the test for an absence is worth writing explicitly, because the line is one an implementer adds by reflex.

**Files:**
- Modify: `web/public/sw.js`
- Modify: `web/src/test/sw.test.ts`

- [ ] **Step 1: Write the failing tests**

```ts
describe("the update handshake", () => {
  it("does not take over on install", async () => {
    const worker = shell();
    await worker.install();
    // A build that swaps itself under a running conversation loses whatever was
    // on screen. The new worker waits until the user asks for it.
    expect(worker.skipWaitingCalls).toBe(0);
  });

  it("takes over when the page asks", async () => {
    const worker = shell();
    await worker.install();
    await worker.message({ type: "SKIP_WAITING" });
    expect(worker.skipWaitingCalls).toBe(1);
  });

  it("ignores every other message", async () => {
    const worker = shell();
    await worker.install();
    await worker.message({ type: "PING" });
    await worker.message("SKIP_WAITING");
    await worker.message(null);
    expect(worker.skipWaitingCalls).toBe(0);
  });
});
```

- [ ] **Step 2: Run them and read the failure**

```bash
npx vitest run src/test/sw.test.ts -t "handshake"
```

Expected: the first passes (there is no `skipWaiting` call yet), the second fails on `expected 0 to be 1`.

- [ ] **Step 3: Add the message listener**

```js
self.addEventListener("message", (event) => {
  /* The only message this worker takes, and the only way it ever activates
     early. Its counterpart is applyUpdate() in src/lib/sw.ts; between them they
     are what makes a new build reachable on an installed iOS PWA without
     deleting the icon and adding it again. */
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});
```

- [ ] **Step 4: Run green**

```bash
npx vitest run src/test/sw.test.ts
npm run lint && npm run build
```

Expected: PASS, 48 tests *(corrected in task 3: 40 as planned; +8)*. ESLint silent, build clean, and `web/dist/sw.js` now exists:

```bash
ls -l dist/sw.js dist/manifest.json dist/manifest-light.json dist/apple-touch-icon.png
```

Expected: all four present, and **no `dist/favicon.svg` or `dist/icons.svg`**. If `dist/` still holds them, it is a stale build directory — `rm -rf dist && npm run build`.

- [ ] **Step 5: Commit**

```bash
git add web/public/sw.js web/src/test/sw.test.ts
git commit -m "feat(web): a waiting worker takes over only when the page asks"
```

Counts after this task: **73 files / ~1 564 tests**.

---

## Task 9: Registering it, and noticing a new one

The page's half. Four jobs: register in production and actively *unregister* in dev; notice a worker that has installed and is waiting; re-check on every return to the foreground; reload once when the new worker takes control.

Two testability decisions, both in the signature rather than in a mock. `enabled` defaults to `import.meta.env.PROD` but is a parameter, so tests never stub env. `reload` is injected, because jsdom does not implement navigation and `window.location.reload()` throws there.

*(Carried forward from task 7, measured on the deployed container: `GET /sw.js` returns
**200 `text/html`**, 1385 bytes of `index.html`, because that image predates the worker and
`mount_spa` answers any unknown path with the shell. Two consequences for this task. The
registration should treat a `text/html` `/sw.js` as a **hard error** rather than a silent
no-op — `navigator.serviceWorker.register()` fails on the MIME type and the rejection is the
only signal that the deploy did not ship the file. And an installed worker cannot be
withdrawn by deleting `sw.js`: the update check gets HTML, fails, and the old worker stays
in charge, which is what task 11's kill switch is for.)*

**Files:**
- Create: `web/src/lib/sw.ts`, `web/src/lib/sw.test.ts`
- Modify: `web/src/main.tsx`, `web/src/main.test.ts`

- [ ] **Step 1: Write the failing tests**

`web/src/lib/sw.test.ts`. jsdom has no `navigator.serviceWorker`, so each test defines one and deletes it afterwards — `vi.stubGlobal` would have to replace the whole `navigator`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyUpdate, installServiceWorker, onUpdateReady, SW_URL } from "./sw";

/** A ServiceWorker: enough of one to change state and receive a message. */
function worker(state: string) {
  const listeners = new Set<() => void>();
  return {
    state,
    postMessage: vi.fn(),
    addEventListener: (_type: string, fn: () => void) => void listeners.add(fn),
    settle(next: string) {
      this.state = next;
      for (const fn of [...listeners]) fn();
    },
  };
}

function registry(options: { waiting?: ReturnType<typeof worker>; controller?: object } = {}) {
  const registration = {
    waiting: options.waiting ?? null,
    installing: null as ReturnType<typeof worker> | null,
    update: vi.fn(() => Promise.resolve()),
    listeners: new Map<string, () => void>(),
    addEventListener(type: string, fn: () => void) {
      this.listeners.set(type, fn);
    },
    unregister: vi.fn(() => Promise.resolve(true)),
  };
  const container = {
    controller: options.controller ?? null,
    register: vi.fn(() => Promise.resolve(registration)),
    getRegistrations: vi.fn(() => Promise.resolve([registration])),
    listeners: new Map<string, () => void>(),
    addEventListener(type: string, fn: () => void) {
      this.listeners.set(type, fn);
    },
  };
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: container });
  return { registration, container };
}

beforeEach(() => {
  Object.defineProperty(globalThis, "caches", {
    configurable: true,
    value: { keys: vi.fn(() => Promise.resolve(["alfred-shell-v1"])), delete: vi.fn() },
  });
});

afterEach(() => {
  Reflect.deleteProperty(navigator, "serviceWorker");
  Reflect.deleteProperty(globalThis, "caches");
});

describe("installServiceWorker", () => {
  it("registers the worker at the root, so its scope is the whole app", async () => {
    const { container } = registry();
    await installServiceWorker({ enabled: true });
    expect(container.register).toHaveBeenCalledWith(SW_URL);
  });

  it("registers nothing in dev, and clears what a previous preview left", async () => {
    // A worker registered by `npm run preview` outsurvives the preview server,
    // and `npm run dev` on the same origin then serves through a stale cache
    // and fights HMR. Dev actively cleans up rather than merely abstaining.
    const { container, registration } = registry();
    await installServiceWorker({ enabled: false });
    expect(container.register).not.toHaveBeenCalled();
    expect(registration.unregister).toHaveBeenCalled();
    expect(caches.delete).toHaveBeenCalledWith("alfred-shell-v1");
  });

  it("does nothing at all where there is no service worker", async () => {
    Reflect.deleteProperty(navigator, "serviceWorker");
    await expect(installServiceWorker({ enabled: true })).resolves.toBeTypeOf("function");
  });

  it("re-checks for a new build every time the app comes back", async () => {
    const { registration } = registry();
    await installServiceWorker({ enabled: true });
    // An installed PWA is opened, not reloaded: without this it runs for weeks
    // on the build it was installed with.
    document.dispatchEvent(new Event("visibilitychange"));
    expect(registration.update).toHaveBeenCalledTimes(1);
  });

  it("stops re-checking once uninstalled", async () => {
    const { registration } = registry();
    const stop = await installServiceWorker({ enabled: true });
    stop();
    document.dispatchEvent(new Event("visibilitychange"));
    expect(registration.update).not.toHaveBeenCalled();
  });
});

describe("onUpdateReady", () => {
  it("announces a worker that is already waiting", async () => {
    const seen: boolean[] = [];
    registry({ waiting: worker("installed"), controller: {} });
    const stop = onUpdateReady((ready) => seen.push(ready));
    await installServiceWorker({ enabled: true });
    expect(seen.at(-1)).toBe(true);
    stop();
  });

  it("announces one that finishes installing while the app is open", async () => {
    const seen: boolean[] = [];
    const { registration } = registry({ controller: {} });
    const stop = onUpdateReady((ready) => seen.push(ready));
    await installServiceWorker({ enabled: true });
    const incoming = worker("installing");
    registration.installing = incoming;
    registration.listeners.get("updatefound")?.();
    incoming.settle("installed");
    expect(seen.at(-1)).toBe(true);
    stop();
  });

  it("says nothing on a first install", async () => {
    // No controller means this is the first worker this phone has ever had.
    // There is nothing to reload *into*, and offering a reload would be a
    // control with no effect.
    const seen: boolean[] = [];
    const { registration } = registry({ controller: null });
    const stop = onUpdateReady((ready) => seen.push(ready));
    await installServiceWorker({ enabled: true });
    const incoming = worker("installing");
    registration.installing = incoming;
    registration.listeners.get("updatefound")?.();
    incoming.settle("installed");
    expect(seen).toEqual([false]);
    stop();
  });
});

describe("applyUpdate", () => {
  it("asks the waiting worker to take over", async () => {
    const waiting = worker("installed");
    registry({ waiting, controller: {} });
    await installServiceWorker({ enabled: true });
    applyUpdate();
    expect(waiting.postMessage).toHaveBeenCalledWith({ type: "SKIP_WAITING" });
  });

  it("does nothing when nothing is waiting", async () => {
    registry();
    await installServiceWorker({ enabled: true });
    expect(() => applyUpdate()).not.toThrow();
  });

  it("reloads once when the new worker takes control", async () => {
    const reload = vi.fn();
    const { container } = registry({ waiting: worker("installed"), controller: {} });
    await installServiceWorker({ enabled: true, reload });
    container.listeners.get("controllerchange")?.();
    container.listeners.get("controllerchange")?.();
    // Twice, because Safari fires it again after the reload starts. A second
    // reload is a reload loop, which is the classic way to brick a PWA.
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run it and read the failure**

```bash
npx vitest run src/lib/sw.test.ts
```

Expected: FAIL — `Failed to resolve import "./sw"`.

- [ ] **Step 3: Write `web/src/lib/sw.ts`**

```ts
import { onVisible } from "./lifecycle";

/** The worker is at the root so its scope is the whole app. */
export const SW_URL = "/sw.js";

export interface SwOptions {
  /**
   * Defaults to `import.meta.env.PROD`. A parameter rather than a read, so a
   * test proves the production path without stubbing the environment.
   */
  enabled?: boolean;
  /** Injected: jsdom implements no navigation, so a test cannot let this run. */
  reload?: () => void;
}

type Listener = (ready: boolean) => void;

const listeners = new Set<Listener>();
let waiting: ServiceWorker | null = null;
let reloaded = false;

function announce(): void {
  for (const fn of [...listeners]) fn(waiting !== null);
}

/**
 * Subscribe to "a newer build has installed and is waiting for permission".
 * Called immediately with the state as it stands, like `subscribeOnline` in
 * `ConnectionProvider`: a subscriber that arrives after the worker installed
 * must not wait for the next one.
 */
export function onUpdateReady(fn: Listener): () => void {
  listeners.add(fn);
  fn(waiting !== null);
  return () => void listeners.delete(fn);
}

/**
 * Take the waiting build. The worker answers by calling `skipWaiting()`, which
 * fires `controllerchange`, which reloads the page — see `installServiceWorker`.
 * This is the whole of the "update without reinstalling" path: on iOS the only
 * other way to move an installed PWA onto a new build is to delete the icon.
 */
export function applyUpdate(): void {
  waiting?.postMessage({ type: "SKIP_WAITING" });
}

/** Drop every worker and every cache this origin holds. Dev, and the last resort. */
export async function unregisterAll(): Promise<void> {
  if (!("serviceWorker" in navigator)) return;
  const registrations = await navigator.serviceWorker.getRegistrations();
  await Promise.all(registrations.map((registration) => registration.unregister()));
  if ("caches" in globalThis) {
    const names = await caches.keys();
    await Promise.all(names.map((name) => caches.delete(name)));
  }
}

/**
 * Install the app shell's worker and watch for its successor. Returns the
 * uninstaller for the foreground update check.
 *
 * In dev this *unregisters* instead: a worker left behind by `npm run preview`
 * outlives the preview server, and `npm run dev` on the same origin then serves
 * a stale shell and fights HMR.
 */
export async function installServiceWorker(options: SwOptions = {}): Promise<() => void> {
  const { enabled = import.meta.env.PROD, reload = () => window.location.reload() } = options;
  if (!("serviceWorker" in navigator)) return () => {};
  if (!enabled) {
    await unregisterAll();
    return () => {};
  }

  navigator.serviceWorker.addEventListener("controllerchange", () => {
    // Once. Safari fires this again while the reload is already under way, and
    // a second reload is a reload loop — the classic way to brick a PWA.
    if (reloaded) return;
    reloaded = true;
    reload();
  });

  const registration = await navigator.serviceWorker.register(SW_URL);

  const track = (incoming: ServiceWorker | null) => {
    if (!incoming) return;
    const settle = () => {
      // A controller already present means this is an *update*. On a first
      // install there is nothing to reload into, and offering a reload would be
      // a control with no effect.
      if (incoming.state !== "installed" || !navigator.serviceWorker.controller) return;
      waiting = incoming;
      announce();
    };
    settle();
    incoming.addEventListener("statechange", settle);
  };

  track(registration.waiting);
  registration.addEventListener("updatefound", () => track(registration.installing));

  // An installed PWA is opened, not reloaded. Without this it could run for
  // weeks on the build it was installed with.
  return onVisible(() => void registration.update());
}
```

- [ ] **Step 4: Run it green**

```bash
npx vitest run src/lib/sw.test.ts
```

Expected: PASS, 12 tests. If they leak into each other, the module-level `waiting` is why: it is deliberate — it mirrors `ConnectionProvider`'s singletons, which are one per module load and so one per app — and the tests are written to tolerate it by calling their own `stop()` and asserting on `seen.at(-1)` rather than on the whole array. Do not reach for `vi.resetModules()`; it would give each test its own copy of the module and stop testing the singleton that ships.

- [ ] **Step 5: Wire it into `web/src/main.tsx`**

After the `createRoot(...).render(...)` call, not before it:

```ts
// After the render, deliberately: a worker that registers a frame late costs
// nothing, and one that delays the first paint costs everything. In dev this
// unregisters instead — see lib/sw.ts.
void installServiceWorker();
```

with `import { installServiceWorker } from "@/lib/sw";` at the top.

- [ ] **Step 6: Extend `web/src/main.test.ts`**

Read the file first for how it currently asserts on `main.tsx`'s side effects, and add one test in the same shape:

```ts
  // `import source from "../main.tsx?raw";` at the top of the file — *corrected in
  // task 1*, where this exact `readFileSync(fileURLToPath(new URL(…)))` shape cost a
  // deviation twice over. `new URL("…", import.meta.url)` is **Vite's asset syntax**,
  // not Node's: the plugin rewrites it into a `data:` URL, so `fileURLToPath` throws
  // `TypeError: The URL must be of scheme file`. And `node:fs` does not type-check
  // under `tsconfig.app.json`, whose `types` is `["vite/client"]` alone. `?raw` is the
  // house idiom for reading a file from a test (`src/test/index-html.test.ts:3-5`) and
  // has neither problem. Reach for `node:fs` only for bytes `?raw` would mangle — a
  // PNG — and move that file to `tsconfig.node.json` when you do.
  it("registers the service worker after the first render", () => {
    // Order matters: the render is what paints, and the worker is not on that
    // path. The assertion is on the source, because main.tsx runs once per
    // module load and cannot be re-imported per test.
    const render = source.indexOf("createRoot");
    const register = source.indexOf("installServiceWorker()");
    expect(render).toBeGreaterThan(-1);
    expect(register).toBeGreaterThan(render);
  });
```

If `main.test.ts` already imports `main.tsx` for real, follow its existing pattern instead of reading the source, and assert that `navigator.serviceWorker.register` was called.

- [ ] **Step 7: Run green and commit**

```bash
npx vitest run src/lib/sw.test.ts src/main.test.ts
npm run lint && npm run build
git add web/src/lib/sw.ts web/src/lib/sw.test.ts web/src/main.tsx web/src/main.test.ts
git commit -m "feat(web): register the worker, and notice the build waiting behind it"
```

Counts after this task: **74 files / ~1 577 tests**.

---

## Task 10: The Room says a newer Alfred is waiting

The handoff has no update copy — no scene, no gate variant, no row type (deviation 3). The note is invented, in the voice, and its second clause is the handoff's own, verbatim from the install gate's foot (`Alfred.dc.html:669`): *Nothing is lost; I pick up where we left off.*

It lives in the Room's header, under `OfflineNote`, in the same 13 px surface-pill idiom. A gate covers it, and that is accepted: the gates are transient, an update is not urgent, and a build that cannot get past a gate is a kill-switch case (task 11), not an update case.

**Files:**
- Create: `web/src/shell/UpdateNote.tsx`, `web/src/shell/UpdateNote.test.tsx`
- Create: `web/src/shell/useUpdateReady.ts`, `web/src/shell/useUpdateReady.test.tsx`
- Modify: `web/src/room/Room.tsx`

- [ ] **Step 1: Write the failing tests for the view**

`web/src/shell/UpdateNote.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { UpdateNote } from "./UpdateNote";

describe("UpdateNote", () => {
  it("says what is waiting and what it costs", async () => {
    render(<UpdateNote ready onReload={() => {}} />);
    expect(
      await screen.findByText(
        "A newer Alfred is waiting. Nothing is lost; I pick up where we left off.",
      ),
    ).toBeInTheDocument();
  });

  it("reloads on the tap", async () => {
    const onReload = vi.fn();
    render(<UpdateNote ready onReload={onReload} />);
    await userEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("stays mounted and empty while there is nothing to say", () => {
    // Same reason OfflineNote does: VoiceOver can miss a live region that is
    // inserted with its text already in it, and a new build is news.
    const { container } = render(<UpdateNote ready={false} onReload={() => {}} />);
    const region = container.querySelector('[role="status"]');
    expect(region).toBeInTheDocument();
    expect(region).toBeEmptyDOMElement();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("gives the reload a full-size hit area", () => {
    // 13px text is not a 44px target. The negative-inset pseudo-element is the
    // house pattern for growing one (docs/web-frontend.md).
    render(<UpdateNote ready onReload={() => {}} />);
    expect(screen.getByRole("button", { name: "Reload" }).className).toContain("after:-inset-y-3");
  });
});
```

- [ ] **Step 2: Write the failing test for the subscription**

`web/src/shell/useUpdateReady.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as sw from "@/lib/sw";
import { useUpdateReady } from "./useUpdateReady";

function Probe() {
  return <span>{useUpdateReady() ? "ready" : "current"}</span>;
}

describe("useUpdateReady", () => {
  it("starts from whatever onUpdateReady says first", () => {
    vi.spyOn(sw, "onUpdateReady").mockImplementation((fn) => {
      fn(true);
      return () => {};
    });
    render(<Probe />);
    expect(screen.getByText("ready")).toBeInTheDocument();
  });

  it("unsubscribes on unmount", () => {
    const stop = vi.fn();
    vi.spyOn(sw, "onUpdateReady").mockReturnValue(stop);
    const { unmount } = render(<Probe />);
    unmount();
    expect(stop).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 3: Run both and read the failures**

```bash
npx vitest run src/shell/UpdateNote.test.tsx src/shell/useUpdateReady.test.tsx
```

Expected: FAIL — neither module resolves.

- [ ] **Step 4: Write `web/src/shell/useUpdateReady.ts`**

```ts
import { useEffect, useState } from "react";
import { onUpdateReady } from "@/lib/sw";

/** Whether a newer build has installed and is waiting for permission to take over. */
export function useUpdateReady(): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => onUpdateReady(setReady), []);
  return ready;
}
```

- [ ] **Step 5: Write `web/src/shell/UpdateNote.tsx`**

```tsx
export interface UpdateNoteProps {
  ready: boolean;
  onReload: () => void;
}

/**
 * A newer build has installed and is waiting. Decision 7: it does not take over
 * by itself, because a swap under a running conversation loses what is on
 * screen — so this is the switch, and it is the whole of the "update without
 * reinstalling" path on an installed iOS PWA.
 *
 * The handoff has no copy for this: no scene, no gate variant, no row type. The
 * sentence is invented, and its second clause is the handoff's own, verbatim
 * from the install gate's foot (`Alfred.dc.html:669`).
 *
 * `--accent`, not `--green`: phase 3's rule is that accent is a decision waiting
 * on the world, and green is only `alive` and `applied`. An update waiting on
 * the user is exactly the first of those.
 *
 * The live region stays mounted while there is nothing to say, empty and
 * `sr-only`, for the reason `OfflineNote` does.
 */
export function UpdateNote({ ready, onReload }: UpdateNoteProps) {
  return (
    <div
      role="status"
      className={
        ready
          ? "mt-2 flex items-center gap-2 rounded-[10px] px-3 py-2 text-[13px] leading-[1.4]"
          : "sr-only"
      }
      style={ready ? { background: "var(--surface)" } : undefined}
    >
      {ready ? (
        <>
          <span
            aria-hidden="true"
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ background: "var(--accent)" }}
          />
          <span>A newer Alfred is waiting. Nothing is lost; I pick up where we left off.</span>
          <button
            type="button"
            onClick={onReload}
            className="relative ml-auto shrink-0 font-medium after:absolute after:-inset-x-2 after:-inset-y-3 after:content-['']"
            style={{ color: "var(--accent-text)" }}
          >
            Reload
          </button>
        </>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 6: Mount it in the Room**

In `web/src/room/Room.tsx`, add the imports:

```tsx
import { applyUpdate } from "@/lib/sw";
import { UpdateNote } from "@/shell/UpdateNote";
import { useUpdateReady } from "@/shell/useUpdateReady";
```

a line beside the other hooks:

```tsx
  const updateReady = useUpdateReady();
```

and the element directly under `<OfflineNote … />` in the header:

```tsx
        <UpdateNote ready={updateReady} onReload={applyUpdate} />
```

- [ ] **Step 7: Run green and commit**

```bash
npx vitest run src/shell/UpdateNote.test.tsx src/shell/useUpdateReady.test.tsx
npx vitest run
npm run lint && npm run build
git add web/src/shell/UpdateNote.tsx web/src/shell/UpdateNote.test.tsx \
        web/src/shell/useUpdateReady.ts web/src/shell/useUpdateReady.test.tsx web/src/room/Room.tsx
git commit -m "feat(web): the Room offers the build that is waiting behind it"
```

Counts after this task: **76 files / ~1 583 tests**.

---

## Task 11: The kill switch

A bad precache on an installed iOS PWA is very hard for a user to clear: there is no address bar, no developer tools, no "clear site data", and Settings › Safari › Clear History does not reliably reach a home-screen app's storage. The only remote lever is the bytes at `/sw.js`. *(Carried forward from task 7: nothing else reaches a poisoned `/assets/` entry either. That branch is cache-first with no expiry and no revalidation, and `purge()` — the only thing that deletes an entry — runs on activate, which happens only when `sw.js`'s own bytes change. So a wrong entry stays wrong until either this file is edited or the switch below is thrown.)* So the kill switch is a **file**, checked in and tested, and the procedure is a copy.

**It cannot be a deletion.** `mount_spa` (`core/channels/spa.py:42-49`) answers any unknown path with `index.html` and a 200, so deleting `web/public/sw.js` makes `/sw.js` serve a page of HTML. The browser's update check rejects it on MIME type, the update fails, and **the old worker stays installed and keeps serving its cache**. Deleting the file is the one action that looks like a fix and is not. *(Measured in task 7's fix round, and it is not hypothetical today: `GET /sw.js` on the deployed container returns 200 `text/html` — 1385 bytes of `index.html` — because that image was built before this file existed. That is exactly the state a deletion would restore.)*

**Files:**
- Create: `web/public/sw.kill.js`, `web/src/test/sw-kill.test.ts`

- [ ] **Step 1: Write the failing test**

`web/src/test/sw-kill.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import source from "../../public/sw.kill.js?raw";
import { loadWorker, ORIGIN } from "./sw-harness";

function killer() {
  const worker = loadWorker(source);
  worker.store.set(
    "alfred-shell-v1",
    new Map([[`${ORIGIN}/index.html`, new Response("<!doctype html>")]]),
  );
  worker.store.set("alfred-shell-v0", new Map());
  return worker;
}

describe("the kill switch", () => {
  it("takes over at once, without waiting to be asked", async () => {
    // The opposite of sw.js, and deliberately: this worker exists precisely for
    // the case where the app cannot be driven, so there is nobody to ask.
    const worker = killer();
    await worker.install();
    expect(worker.skipWaitingCalls).toBe(1);
  });

  it("drops every cache on the origin", async () => {
    const worker = killer();
    await worker.install();
    await worker.activate();
    expect([...worker.store.keys()]).toEqual([]);
  });

  it("unregisters itself, leaving a plain web page", async () => {
    const worker = killer();
    await worker.install();
    await worker.activate();
    expect(worker.unregisterCalls).toBe(1);
  });

  it("claims the open pages before it goes, then reloads them", async () => {
    // A client still controlled by the old worker would keep being served from
    // a cache that no longer exists. Claim, then unregister, then navigate.
    const worker = killer();
    await worker.install();
    await worker.activate();
    expect(worker.claimCalls).toBe(1);
    expect(worker.navigated).toEqual([`${ORIGIN}/`]);
  });

  it("caches nothing, ever", async () => {
    const worker = killer();
    await worker.install();
    await worker.activate();
    await expect(worker.request({ path: "/", mode: "navigate" })).resolves.toBeUndefined();
    await expect(worker.request({ path: "/assets/index-CY0Sh5Q2.js" })).resolves.toBeUndefined();
    expect([...worker.store.keys()]).toEqual([]);
  });
});
```

- [ ] **Step 2: Run it and read the failure**

```bash
npx vitest run src/test/sw-kill.test.ts
```

Expected: FAIL — `Failed to resolve import "../../public/sw.kill.js?raw"`.

- [ ] **Step 3: Write `web/public/sw.kill.js`**

```js
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
      for (const name of await self.caches.keys()) await self.caches.delete(name);
      /* Claim before unregistering: a client still controlled by the previous
         worker would keep being served from a cache that no longer exists. */
      await self.clients.claim();
      await self.registration.unregister();
      for (const client of await self.clients.matchAll({ type: "window" })) {
        await client.navigate(client.url);
      }
    })(),
  );
});

/* No fetch handler at all. Nothing this worker controls is served from storage. */
```

- [ ] **Step 4: Run green**

```bash
npx vitest run src/test/sw-kill.test.ts
npm run lint
```

Expected: PASS, 5 tests. ESLint silent.

- [ ] **Step 5: Commit**

```bash
git add web/public/sw.kill.js web/src/test/sw-kill.test.ts
git commit -m "feat(web): a kill switch that unregisters and drops every cache"
```

Counts after this task: **77 files / ~1 588 tests**.

---

## Task 12: The app opening with no network

The hole, found by reading `AuthGate` rather than by guessing: `useQuery(["auth-status"])` runs under react-query's default `networkMode: "online"`, which **pauses** a query when `navigator.onLine` is false. A paused query is not fetching, holds no data and raises no error, so `isPending` stays true — and `AuthGate`'s pending branch renders the presence field *and no copy*, for ever.

That is the exact defect phase 3 named as house style — *never say the house is empty before the server has answered, and say which of the three meanings the silence has* — one layer lower down. Phase 4 says it.

`navigator.onLine` is used for one thing only: to explain a read that **has not landed**. It is never used to claim the house is reachable and never to gate a request. Note that this makes it consistent by construction: when `onLine` is true but there is no route to the house, the query *runs and fails*, `isPending` goes false, and `AuthGate` falls through to its existing fail-closed `SignInGate` branch. The offline gate is reachable only in the state where react-query is paused, which is the state it describes.

Deviation 5: **no retry button.** With `onLine` false a retry queues a paused fetch and shows nothing. The gate clears itself when the `online` event fires — the same event react-query resumes on.

**Files:**
- Create: `web/src/shell/useOnline.ts`, `web/src/shell/useOnline.test.tsx`
- Create: `web/src/gates/OfflineGate.tsx`, `web/src/gates/OfflineGate.test.tsx`
- Modify: `web/src/gates/AuthGate.tsx`, `web/src/gates/AuthGate.test.tsx`

- [ ] **Step 1: Write the failing tests for `useOnline`**

`web/src/shell/useOnline.test.tsx`:

```tsx
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useOnline } from "./useOnline";

function setOnLine(value: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, value });
}

afterEach(() => {
  Reflect.deleteProperty(navigator, "onLine");
});

function Probe() {
  return <span>{useOnline() ? "online" : "offline"}</span>;
}

describe("useOnline", () => {
  it("starts from what the phone says now", () => {
    setOnLine(false);
    render(<Probe />);
    expect(screen.getByText("offline")).toBeInTheDocument();
  });

  it("follows the offline event", () => {
    setOnLine(true);
    render(<Probe />);
    act(() => void window.dispatchEvent(new Event("offline")));
    expect(screen.getByText("offline")).toBeInTheDocument();
  });

  it("follows the online event", () => {
    setOnLine(false);
    render(<Probe />);
    act(() => void window.dispatchEvent(new Event("online")));
    expect(screen.getByText("online")).toBeInTheDocument();
  });

  it("stops listening on unmount", () => {
    setOnLine(true);
    const { unmount } = render(<Probe />);
    unmount();
    // No assertion is possible on a removed listener beyond this not throwing;
    // the value is that a leaked listener would setState on an unmounted tree.
    expect(() => window.dispatchEvent(new Event("offline"))).not.toThrow();
  });
});
```

- [ ] **Step 2: Write `web/src/shell/useOnline.ts`**

```ts
import { useEffect, useState } from "react";

/**
 * Whether this phone believes it has a network.
 *
 * Used for exactly one thing: explaining a read that has **not landed**. Never
 * to claim the house is reachable — `navigator.onLine` is true for a phone on a
 * café's Wi-Fi with no route to anything — and never to gate a request.
 *
 * That narrow use is consistent by construction. react-query's default
 * `networkMode: "online"` reads the same flag and *pauses* rather than fails, so
 * `onLine === false` is precisely the state in which a query holds no data,
 * raises no error and stays pending. When `onLine` is true and the house is
 * unreachable anyway, the query runs, fails, and the caller's existing error
 * path takes it.
 */
export function useOnline(): boolean {
  const [online, setOnline] = useState(() => navigator.onLine);

  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);

  return online;
}
```

- [ ] **Step 3: Write the failing test for the gate**

`web/src/gates/OfflineGate.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OfflineGate } from "./OfflineGate";

describe("OfflineGate", () => {
  it("says which of the three silences this one is", () => {
    render(<OfflineGate />);
    expect(screen.getByText("offline · nothing read yet")).toBeInTheDocument();
    expect(screen.getByText("No way to the house.")).toBeInTheDocument();
    expect(
      screen.getByText(
        "This phone has no connection, so I cannot ask the house who you are. Nothing is lost; I pick up where we left off as soon as there is a signal.",
      ),
    ).toBeInTheDocument();
  });

  it("offers no button at all", () => {
    // With navigator.onLine false, react-query pauses; a `Try again` would queue
    // a paused fetch and show nothing. The gate clears itself on the `online`
    // event, which is the same event react-query resumes on.
    render(<OfflineGate />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Write `web/src/gates/OfflineGate.tsx`**

```tsx
import { Gate } from "@/gates/Gate";

/**
 * The cold launch with no network: the shell booted from the service worker's
 * cache, and nothing has been read — not even whether there is a session.
 *
 * Phase 3's rule, one layer lower: never say the house is empty before the
 * server has answered, and say which of the three silences this one is. The
 * other two — the read was refused, the store really is empty — are the benches'.
 *
 * The handoff has no copy for this; its offline scenes are all a *running* app
 * that lost its socket. Invented, and shaped on `DeniedGate`, whose form the
 * handoff did fix. No button: see the test, and deviation 5 in the plan.
 */
export function OfflineGate() {
  return (
    <Gate
      kicker="offline · nothing read yet"
      title="No way to the house."
      body="This phone has no connection, so I cannot ask the house who you are. Nothing is lost; I pick up where we left off as soon as there is a signal."
    />
  );
}
```

- [ ] **Step 5: Write the failing test for `AuthGate`**

Add to `web/src/gates/AuthGate.test.tsx`, in the shape the file already uses for its other cases:

```tsx
  it("says the phone is offline rather than showing an empty field for ever", async () => {
    // react-query's networkMode "online" PAUSES the auth read rather than
    // failing it, so isPending never resolves and the gate showed the presence
    // field and no copy, indefinitely.
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    renderAuthGate({ status: new Promise<never>(() => {}) });
    expect(await screen.findByText("No way to the house.")).toBeInTheDocument();
    Reflect.deleteProperty(navigator, "onLine");
  });

  it("shows the bare field, not the offline gate, while a read is merely slow", async () => {
    // onLine true and pending means the request is in flight. Saying "no
    // connection" there would be a guess, and the gate's whole point is that it
    // does not guess.
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    renderAuthGate({ status: new Promise<never>(() => {}) });
    expect(screen.queryByText("No way to the house.")).not.toBeInTheDocument();
    Reflect.deleteProperty(navigator, "onLine");
  });
```

Match `renderAuthGate`'s real signature from the top of that file; if it takes a `fetchAuthStatus` spy rather than an options object, use a never-resolving promise there instead.

- [ ] **Step 6: Wire it into `web/src/gates/AuthGate.tsx`**

Add the imports:

```tsx
import { OfflineGate } from "@/gates/OfflineGate";
import { useOnline } from "@/shell/useOnline";
```

a line beside the other hooks:

```tsx
  const online = useOnline();
```

and replace the `isPending` branch:

```tsx
  let body: ReactNode;
  if (isPending) {
    // react-query's `networkMode: "online"` PAUSES a read with no network
    // rather than failing it — not fetching, no data, no error — so without
    // this the gate would show the field and no copy indefinitely. With a
    // network, pending really is "in flight", and saying anything about a
    // connection would be a guess.
    body = online ? (
      <div
        className="relative flex flex-1 flex-col"
        style={{ background: "var(--bg)", color: "var(--fg)" }}
      >
        <GateField />
      </div>
    ) : (
      <OfflineGate />
    );
  } else if (needsSetup || setupActive) {
```

- [ ] **Step 7: Run green and commit**

```bash
npx vitest run src/shell/useOnline.test.tsx src/gates/OfflineGate.test.tsx src/gates/AuthGate.test.tsx
npx vitest run
npm run lint && npm run build
git add web/src/shell/useOnline.ts web/src/shell/useOnline.test.tsx \
        web/src/gates/OfflineGate.tsx web/src/gates/OfflineGate.test.tsx \
        web/src/gates/AuthGate.tsx web/src/gates/AuthGate.test.tsx
git commit -m "feat(web): a cold launch with no network says so, instead of nothing"
```

Counts after this task: **79 files / ~1 596 tests**.

---

## Task 13: Docs, backlog, QA checklist

**Files:** Modify `web/README.md`, `docs/web-frontend.md`; create `docs/backlog/low/pwa-phase4-followups.md`, `docs/superpowers/qa/2026-09-16-pwa-phase4-ios-checklist.md`.

- [ ] **Step 1: `docs/web-frontend.md` — a new section, "The service worker"**, placed after "Build / Serve / Container" because it depends on `SpaCacheMiddleware`'s three tiers. It must cover, in this order:

1. **Why it is hand-written and not `vite-plugin-pwa`** — decision 2, condensed: Vite's `index.html` *is* the precache manifest; the policy has to be readable in review; every behaviour is pinned by a test; and the load-bearing requirement is a *negative* one.
2. **The one rule** — app shell and static assets only; `/api/*`, `/health` and the sockets are never passed to `respondWith()`, so they are not merely network-only but never seen. Say it in those terms, and say why: *a cached API response is the client stating a server fact it has not just read, with a longer half-life.*
3. **The four events** — install (fetch `/index.html`, read its `/assets/` URLs, follow the stylesheet to the `woff2`, write the manifest sentinel, and **never** `skipWaiting`); activate (drop every other cache, drop every entry not in the sentinel, claim); fetch (navigation network-first, `/assets/*` cache-first, everything else untouched); message (`SKIP_WAITING` only). Under activate, say the two things that are not obvious from the code: **`caches` is origin-scoped**, so "every other cache" means every cache any code on this hostname owns, and the purge therefore assumes the origin is Alfred's alone and requires phase 5 to fold any cache of its own into `alfred-shell-<VERSION>`; and a record that is **absent, not a list, or not readable** is all read as "keep everything", because deleting on a record you cannot read is guessing with the only offline copy of the app.
4. **The two things a 200 does not mean** — `mount_spa`'s fallback answers any unknown path with `index.html`, so the worker decides on content type, not status; and a captive portal answers 200 with its own page, which is why the navigation branch checks `isShell` before refreshing the cache.
5. **How the cache is versioned** — `VERSION` in `sw.js`; bump it when the *policy* changes, not on a deploy, because asset URLs are content-hashed and the sentinel prune handles a deploy.
6. **"The kill switch"** — its own sub-heading. The procedure (`cp web/public/sw.kill.js web/public/sw.js`, rebuild, deploy), **why deleting `sw.js` does not work**, and the last resort (delete the home-screen icon and add it again; nothing is lost).
7. **How it is tested** — `src/test/sw-harness.ts`, `new Function("self", source)`, the `self.` convention that makes it possible, and the two jsdom facts (no real `Request` with `mode: "navigate"`; `Response` is a Node global).
8. **The manifest pair** — two files, `applyTheme` swaps the link, iOS reads it at Add-to-Home-Screen time, and on iOS it is `background_color` this really fixes.
9. **The icons** — the four files, the generation command, why the mark is geometry and not `<text>`, why there is no radius, and the measurement that lets one 512 PNG be `any maskable`.

- [ ] **Step 2: `docs/web-frontend.md` — update two existing sections.** Under "iOS constraints", add the new automated coverage (`icons.test.ts`, `manifest.test.ts`, `sw.test.ts`, `sw-kill.test.ts` for §4.2; `useOnline`/`OfflineGate` for §4.10's cold rehydrate) and the new device checklist beside the other three. Under "What phases 1 to 3 do not cover", rewrite the first bullet: the service worker, icons and manifest have landed; **Web Push and the six-step install rail have not**, and the reason is decision 6.

- [ ] **Step 3: `web/README.md`** — three edits.
  - The `public/` contents in the layout section: the four icon files, the two manifests, `sw.js` and `sw.kill.js`, with one line each.
  - A **"Regenerating the icons"** block carrying the exact `rsvg-convert` commands from task 1 step 4 and the ImageMagick fallback, and the sentence that they are checked-in bytes: CI does not run them and the container's `webbuild` stage is unchanged.
  - The "What it does not" section: delete the paragraph about the manifest's dark ground being a deferred defect (task 2 closed it), and replace the phase-4/5 bullet with one that says phase 4 landed and names what phase 5 still owes — see step 5.

- [ ] **Step 4: `docs/backlog/low/pwa-phase4-followups.md`** — one entry each, with the change that would close it:
  1. **The install rail is unbuilt** (decision 6). Phase 5 owns `Alfred.dc.html:669` (`install` gate) and `:670` (`standalone` gate), the home-screen prompt row at `:751`, and System › Reach at `:385-397`. Copy is final in all four.
  2. **`registration.update()` runs on every foreground**, with no minimum interval, so an app switched to and from ten times a minute makes ten conditional requests for `/sw.js`. Harmless today (the file is ~4 kB and tier-3 revalidated), but a `Date.now()` floor of a minute is the fix if it ever shows up in the logs.
  3. **The update note is only visible in the Room.** A gate covers it. Accepted in task 10, but if the device checklist finds it undiscoverable, a row in System › Maintenance is the alternative — the handoff has no design for either.
  4. **The precache and the live cache are the same cache.** Between install and activate the cached shell is the *new* HTML while the *old* worker is still serving. Unreachable except by an offline navigation in a window of seconds; the fix is a second cache and a copy at activate, which is more machinery than the window is worth.
  5. **A font missed at install is fetched on first use, so it is not in the sentinel**, and the next activate prunes it — costing one re-download per deploy on a browser whose subsets the stylesheet does not name. Closing it means writing the runtime-cached set back into the sentinel.
  6. **No `apple-touch-startup-image`, and nobody has looked at what iOS actually shows** (decision 11) — **measure first, then decide.** *(Corrected in task 2: this entry read "if the manifest-derived launch screen turns out to be wrong on the device, the per-device matrix is what replaces it", which assumed a manifest-derived launch screen exists on iOS. It does not — `apple-touch-startup-image` is the only mechanism iOS has.)* So the first step is an observation, not a fix: install the app in each theme and write down what the launch screen is — the icon on a plain ground, a white flash, a stretched screenshot. Only then is it worth pricing the per-device matrix, which is a dozen checked-in PNGs that go stale with every new iPhone size, against a launch screen nobody minds. Task 2's two manifests are unaffected either way: they are Chromium's, and `background_color` is what Android composes from.
  7. **The two manifests are a copy, kept honest by a test** rather than generated. A `vite build` hook that emits the light one from the dark one would remove the copy; it would also be the first build-time codegen in this tree.
  8. **`navigator.onLine` is the only offline signal at cold launch.** It is true on a network with no route to the house — which lands in `AuthGate`'s existing fail-closed branch rather than the offline gate, so it is correct, but it means a phone on a captive portal sees `SignInGate` rather than an explanation.
  9. **Nothing tests that `dist/sw.js` exists.** Task 14 step 4 checks it by hand. A Python-side assertion in `tests/core/channels/test_spa_ci.py` — `/sw.js` answers with a JavaScript content type and not `text/html` — would make a regression here a CI failure instead of a device failure, and that regression is the one that silently leaves every phone on its old worker.

- [ ] **Step 5: the QA checklist** — `docs/superpowers/qa/2026-09-16-pwa-phase4-ios-checklist.md`, in the phase-3 checklist's format. **This box is headless: none of this can be run here.** The list:
  - **Install.** Safari → Share → Add to Home Screen. The tile shows the accent `A` on the dark ground with **no black corners** and no page screenshot. The name under it reads `Alfred`.
  - **The launch screen — an observation, not an assertion.** *(Corrected in task 2: this step used to say the launch screen "is `#F6F3EE`, not `#25221F`" and called it the phase-1 defect task 2 closed. It very likely is not: iOS builds a launch screen from `apple-touch-startup-image`, which this app does not ship, and may never read the swapped manifest at all — decision 11 and deviation 6.)* So: install during daylight with the light theme active, kill the app, open it from the home screen and **write down what you see** — the colour of the screen before the app paints, whether an icon appears on it, whether it flashes white. Then switch to dark, reinstall, and record it again. Two identical answers mean the manifest swap is inert on iOS, which is the expected result and **not a defect to fix on the spot**; it is the measurement backlog item 6 is waiting for. Whatever the answer, note it against `docs/backlog/low/pwa-phase4-followups.md` §6.
  - **Standalone.** No Safari chrome, no address bar. The status bar is translucent over the app's own background, and the composer still sits above the home indicator.
  - **Cold offline launch.** Airplane mode, then open from the home screen. The app **paints** — real type, real palette, presence field — and shows `offline · nothing read yet` / `No way to the house.` Turn the radio back on: the gate clears itself with no tap.
  - **Cold offline launch on a deep link.** Airplane mode, tap a `/actions/{id}` link from a previous notification. The same shell loads; the Door does not open, because nothing has been read.
  - **The update path, end to end.** Deploy a build with a visible change. On the phone: switch away, switch back, and confirm the Room shows `A newer Alfred is waiting.` Tap `Reload`, confirm the change is there, and confirm it reloads **once** and does not loop.
  - **The update path with the app killed.** Swipe the app away, deploy, reopen. It must come up on the new build or offer it — not run silently on the old one.
  - **The kill switch, on a device.** Copy `sw.kill.js` over `sw.js`, deploy, open the app, and confirm it reloads into a worker-free page. Check Settings › Safari › Advanced › Website Data that the origin's storage has shrunk. **Then restore `sw.js` and confirm it re-registers.** Do not skip the restore — this is the step that proves the switch is reversible.
  - **The API is never served from cache.** With the app open and the house stopped, confirm the Room says `No connection to the house since HH:MM` and the System bench says `unknown since HH:MM` — never a stale `alive`, never a stale spend figure, never a stale trigger state.
  - **The keyboard, again.** Open the composer on the installed app. The shell must not move and the composer must not clip — the phase-2 bug, re-checked because `#root` is now painted over a cached shell.
  - **VoiceOver.** Turn it on and trigger an update. The note is announced once, and the Room's offline note is not announced twice.

- [ ] **Step 6: Commit**

```bash
git add docs/web-frontend.md web/README.md docs/backlog/low/pwa-phase4-followups.md \
        docs/superpowers/qa/2026-09-16-pwa-phase4-ios-checklist.md
git commit -m "docs(web): the service worker, the kill switch and the phase-4 device checklist"
```

---

## Task 14: Branch green, self-review, PR

- [ ] **Step 1: The whole gate, from `web/`**

```bash
cd ~/code/.worktrees/alfred/pwa-phase4-install/web
npm run lint && npx vitest run && npm run build
```

Expected: eslint silent, **~79 test files / ~1 596 tests passing**, build clean. The counts are a guide — a task that needed more tests is fine; a task with fewer than its section said has a skipped test.

- [ ] **Step 2: Run the suite three times.** Module-level state in `lib/sw.ts` (`waiting`, `reloaded`) and the `defineProperty` on `navigator` are exactly where order-dependent failures live.

```bash
for i in 1 2 3; do npx vitest run 2>&1 | tail -3; done
```

- [ ] **Step 3: Check what actually ships.**

```bash
rm -rf dist && npm run build && ls dist
```

Expected: `assets/`, `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`, `icon.svg`, `index.html`, `manifest-light.json`, `manifest.json`, `sw.js`, `sw.kill.js`. **No `favicon.svg` and no `icons.svg`.** If either is there, the build directory is stale.

```bash
head -c 400 dist/sw.js
```

Expected: the header comment, verbatim. `dist/sw.js` must be the file, not a redirect — if `/sw.js` ever serves `text/html`, no phone can register anything (task 11's header explains why).

- [ ] **Step 4: Mutation-check the four claims that matter most.** Four edits, each reverted immediately. If a test does not fail, the property is undefended.
  1. In `sw.js`, delete the `/api/` clause from the refusal guard. **Task 6 must fail.** If it does not, the single most important property of this phase is untested.
  2. In `sw.js`'s `asset()`, replace `isAsset(response)` with `response.ok`. **Task 7's "refuses to store the SPA fallback" must fail** — that guard is the difference between a deploy and a brick.
  3. In `sw.js`'s install, add `self.skipWaiting()`. **Task 8's first test must fail.**
  4. In `lib/sw.ts`, drop the `reloaded` guard. **Task 9's "reloads once" must fail.**

- [ ] **Step 5: Self-review the diff.** `git diff origin/master --stat`, then read it. Look for: a colour that is not a token; a new token missing from `src/test/contrast.ts` (there should be none — this phase adds no pair); an invented status word; `.t-meta` on a sentence a reader must trust; a `console.log`; a `TODO`; a bare global inside either worker (every one must go through `self.`); a top padding written as a literal instead of `calc(env(safe-area-inset-top, 0px) + Npx)`; and any caching branch that could reach `/api`.

- [ ] **Step 6: Secret hygiene over every commit on the branch** — the checks from the operator's exposure runbook (`~/code/alfred-deploy/PWA-EXPOSURE-RUNBOOK.md`, §"Secret hygiene", outside this repo), run across `git rev-list origin/master..HEAD`, not just `HEAD`. A leak amended out of the tip still ships in history. They are not quoted here or anywhere in this repo: the patterns contain the literals they hunt for. The new files are the ones to look at hardest — `ORIGIN` in the test harness is `https://alfred.example.com` and the plan's `sw-harness.ts` keeps it that way.

- [ ] **Step 7: Push and open the PR.** Body: what phase 4 adds (manifest pair, icon set, worker, kill switch, cold-offline gate); **the deviations table, first, because it is the most useful thing a reviewer can read**; the `vite-plugin-pwa` decision and its reasoning; an explicit note that the §6.4 install rail is phase 5's and why; the backlog file; and that the device checklist is unrun, with the launch-screen observation and the kill-switch round trip called out as the two that cannot be settled any other way — the first of them a measurement, because the plan's original claim that iOS composes a launch screen from the manifest was wrong (decision 11).

```bash
git push -u origin feat/pwa-phase4-install
gh pr create --title "PWA phase 4: install, service worker and offline states" --body "…"
```

**Do not merge.** Merging `master` deploys to the live box; the merge is the user's call.

---

## Self-review of this plan

**Spec coverage.** §8's phase-4 content, item by item: *manifest* → task 2; *icons* → task 1; *service worker* → tasks 3–8, with the kill switch in 11 and the page-side half in 9; *offline states* → task 12 (the cold launch) over the query-layer work phases 1–3 already shipped, which decision 5 explicitly does not reopen. §4.2 (raster icons, opaque, maskable) → task 1, tested at the byte level. §4.1 (secure context) → unchanged; HTTPS via NPM landed in phase 0. §4.9b (no `beforeinstallprompt` on iOS) → decision 6; the teaching rail is phase 5's by §8's own wording. §4.10 (iOS kills standalone PWAs) → the existing `onVisible` rehydration, now also driving `registration.update()` in task 9. §7's "Service worker policy" — *precache the app shell; cache-first for hashed assets; network-only for `/api/*` and both sockets* — is implemented exactly, with "network-only" strengthened to "never handled"; §7's `vite-plugin-pwa` recommendation is the one thing deviated from, argued in decision 2 and listed as deviation 1. §3.4's asset cache headers are already on `master` (`SpaCacheMiddleware`) and are confirmed in "Before you start" step 3 rather than re-done.

**Known gaps, stated rather than hidden.** (a) The §6.4 six-step rail is not built — decision 6, deviation 2, backlog §1. (b) Web Push is phase 5's entirely; task 13 writes down what phase 4 hands it. (c) Two of the four copy strings in this phase are invented, because the handoff has none for an update prompt or a cold offline launch — deviations 3 and 4, each naming the handoff line it borrows its cadence from. (d) Nothing here is verified on a device; the two steps that *can only* be settled on one (what the launch screen actually does, and the kill-switch round trip) are called out in task 14 step 7 so the PR says so. The first is now an **observation** — see decision 11 — and on Android there is no device at all, which deviation 6b's reinstall cost depends on.

**Placeholder scan.** Every code step carries real code. The two places a step says "read the file first" — `theme.test.ts`'s `beforeEach` in task 2 step 2, `main.test.ts`'s existing shape in task 9 step 6, and `AuthGate.test.tsx`'s `renderAuthGate` signature in task 12 step 5 — each give the code to write *and* the fallback if the existing shape differs, rather than deferring the decision. One thing the first draft got wrong and this review caught: task 3's `cache: "reload"` test originally asserted against a regex over the worker's own source, which is a test of how the file is *spelled* rather than of what it *does*, and it would have broken in task 4 the moment a third fetch site appeared. The harness records the request's cache mode instead (`Harness.reloaded`), and the assertion is `reloaded === fetched` — true at task 3, still true at task 4, and false the moment a fetch site drops the flag.

**Type consistency.** Every name a later task uses is fixed in the contract block: `CACHE`/`SHELL`/`MANIFEST`/`VERSION` and `isShell`/`isAsset`/`assetsInHtml`/`fontsInCss`/`precache`/`purge`/`navigate`/`asset` in the worker (tasks 3–8 extend the same file and never rename); `Harness`'s eleven members, `ORIGIN`, and the four response factories in the test harness (tasks 3–8 and 11 all drive it); `SW_URL`/`SwOptions`/`installServiceWorker`/`onUpdateReady`/`applyUpdate`/`unregisterAll` in `lib/sw.ts` (task 9 defines, task 10 consumes); `useUpdateReady`, `useOnline`, `UpdateNoteProps`, `OfflineGate`; and `THEME_COLOR`, which task 2 exports from `lib/theme.ts` and `src/test/manifest.test.ts` imports. The cache name `alfred-shell-v1` appears in the worker as a template literal over `VERSION` and in the harness as a constant — a `VERSION` bump means editing both, which is noted in task 13 step 1 item 5.
