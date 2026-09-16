# PWA Phase 4 — Install and Offline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 4 of spec §8 — *PWA: manifest, icons, service worker, offline states*. After this phase the client installs to the iOS home screen with a correct icon and splash, launches with no network at all, says something honest while it has read nothing, and can take a new build without being reinstalled. Gate: *Installs and behaves as native*.

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

6. **The §6.4 install rail is phase 5's, not this phase's.** Spec §8 lists phase 5's content as "Web Push: VAPID, adapter, registration, service-worker handlers, **install-and-enable sequence**" — the six-step rail is named there by the spec itself, and phase 4's line is "manifest, icons, service worker, offline states". Every step of that rail exists to reach step 6, *Allow in iOS*; shipping steps 1 to 4 with nothing behind them is a control that pretends, which is the defect phase 2's placeholder tabs were and phase 3's absent Reach card avoided. So: **no `install` gate, no `standalone` gate, no Reach prompt row and no System › Reach card in this phase.** What phase 4 owes phase 5 is that the gesture the rail teaches actually produces a correct app — a manifest with a real icon set, `display: standalone`, a splash that matches the theme, and a worker that phase 5 adds `push` and `notificationclick` handlers to. That list is written down in task 13 so phase 5 can start from it.

7. **A new worker waits; the user takes it.** `install` never calls `skipWaiting()`. A build that swaps itself under a running conversation loses whatever was on screen. The waiting worker is announced in the Room, and one tap posts `SKIP_WAITING`, which is what makes an update possible **without reinstalling the app** — the requirement that is otherwise very hard to satisfy on iOS. `registration.update()` runs on every return to the foreground (`onVisible`, already in `lib/lifecycle.ts`), because an installed PWA is *opened*, not reloaded, and could otherwise run for weeks on the build it was installed with.

8. **Navigations are network-first.** The cached shell is served on exactly one path: a phone with no network. Anything online gets the deploy that is live right now. This is the single most important brick mitigation, because it means a poisoned cache is invisible to an online phone *and* that the browser's own update check on `/sw.js` keeps running — which is what lets the kill switch land.

9. **The kill switch is a file, not a paragraph** (task 11). `web/public/sw.kill.js` is checked in, tested, and shipped by copying its bytes over `web/public/sw.js`. **Deleting `sw.js` does not work and must never be tried:** `mount_spa` (`core/channels/spa.py:42-49`) answers any unknown path with `index.html` and a 200, so the browser's update check would fetch a page of HTML, reject it on MIME type, and leave the old worker installed and still serving its cache.

10. **Icons are checked-in bytes, not a build step.** Four files (`icon.svg`, `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`) generated once from one source SVG by a command written down in `web/README.md`. No `sharp`, no `svgo`, no CI dependency on `rsvg-convert`, and the container's `webbuild` stage is unchanged. A test reads the PNG bytes directly and asserts the dimensions and the absence of an alpha channel, which is what iOS actually cares about (§4.2). *(Corrected in task 1: a header read alone proved decorative under mutation — it passes on a PNG rendered from any drawing at all — so the shipped test decodes the pixels with `node:zlib` and checks the colours, the census, the mark's reach and its shape. Still no new dependency.)*

11. **No `apple-touch-startup-image`.** The per-device media-query matrix is a dozen more checked-in PNGs that go stale with every new iPhone size. iOS 15+ composes a launch screen from the manifest's `name`, `background_color` and icon instead, which is why task 2 makes `background_color` follow the theme.

### Deviations from the spec and the handoff (deliberate; flag them in review)

| # | Where | Source says | This plan ships | Why |
|---|---|---|---|---|
| 1 | Service worker | spec §7: "Add `vite-plugin-pwa` (Workbox)" | a 110-line hand-written `public/sw.js` | Decision 2. |
| 2 | §6.4 install rail | six-step rail, `install` + `standalone` gates, copy final at `Alfred.dc.html:669`/`:670` | **not built here** | Decision 6 — spec §8 puts "install-and-enable sequence" in phase 5, and four of six steps with nothing behind them is a control that pretends. |
| 3 | Update prompt | **nothing** — the handoff has no update, reload or new-version copy at all, and no `updatefound` scene | invented: `A newer Alfred is waiting. Nothing is lost; I pick up where we left off.` + a `Reload` text button | An update path the user can trigger without reinstalling is a hard requirement. The second clause is the handoff's own, verbatim from the install gate's foot (`Alfred.dc.html:669`). |
| 4 | Cold offline launch | **nothing** — the handoff's offline scenes are all *a running app that lost its socket*, never a launch with no network | invented: kicker `offline · nothing read yet`, title `No way to the house.`, body `This phone has no connection, so I cannot ask the house who you are. Nothing is lost; I pick up where we left off as soon as there is a signal.` | Phase 3's rule: never say the house is empty before the server has answered, and say *which* of the three meanings the silence has. Modelled line for line on the 403 gate (`DeniedGate.tsx`), whose shape the handoff did fix. |
| 5 | Cold offline gate | — | **no retry button** | With `navigator.onLine === false`, react-query pauses; a `Try again` would queue a paused fetch and show nothing. The gate clears itself when the `online` event fires, which is the same event react-query resumes on. A button that pretends is worse than no button. |
| 6 | `manifest.theme_color` | one static file, currently the dark token in both themes — a phase-1 defect deferred here, noted in `web/README.md` | **two manifests**, `/manifest.json` (dark) and `/manifest-light.json` (light); `applyTheme()` swaps the `<link rel="manifest">` alongside the `theme-color` meta it already rewrites | A manifest is static and the theme is the hour's. iOS reads the manifest at *Add to Home Screen* time, which is after the theme has applied, so the splash matches the theme the user installed under. On iOS `theme_color` is largely inert anyway (`apple-mobile-web-app-status-bar-style` is `black-translucent`, so the page's own background shows through); it is `background_color`, the launch screen, that this actually fixes. |
| 7 | Icon mark | `Alfred.dc.html:530`: letter `A`, `oklch(0.78 0.12 45)`, `500 30px 'DM Sans'`, on `#25221F` | the same mark drawn as two `<path>` strokes, not `<text>` | A `<text>` icon renders with whatever font the *rasteriser* has. `@fontsource-variable/dm-sans` ships `woff2` only, which neither `rsvg-convert` nor `resvg` can load, and the current `icon.svg` asks for Georgia — so today's icon is already whatever serif the machine had. Geometry renders identically everywhere. |
| 8 | Icon shape | current `icon.svg` has `rx="64"` | full-bleed square, no radius | iOS applies its own squircle mask to `apple-touch-icon`; anything outside the rounded rect is transparent, and iOS composites transparency as **black**, so `rx="64"` puts black slivers in the home-screen tile's corners. §4.2's "opaque, no alpha" is the same requirement said another way. |
| 9 | Maskable icon | spec §4.2: "plus 192/512 maskable for the manifest" | one 512 PNG marked `purpose: "any maskable"`, not a second file | Measured on the geometry in task 1: the glyph's furthest point is 170.5 px from the centre of a 512 canvas *(corrected in task 1: this said **190 px**, which is no corner of the drawing — see the correction under task 1 step 3)* and the maskable safe circle's radius is 204.8 px, so the same art is safe under a mask and correct unmasked. A separate maskable file would be the same bytes with a different name. |
| 10 | Precache scope | Workbox precaches every build output | `index.html` + the `/assets/` URLs the HTML names + the `/assets/*.woff2` the stylesheet names | The build emits 20 font files (~800 kB) because `@fontsource/geist-mono` ships `.woff` fallbacks beside every `.woff2`. No browser that can run a service worker asks for a `.woff`. Skipping them halves the precache; anything missed is still caught cache-first on first use. |
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
| `web/src/lib/theme.ts` | Export `THEME_COLOR`; `applyTheme` swaps the manifest link | 2 |
| `web/src/test/contrast.ts` | Gains `srgbHex()`, so a file that cannot hold an `oklch()` restates no literal | 1 |
| `web/src/test/manifest.test.ts` | Both manifests parsed, compared, and held to `THEME_COLOR` | 2 |
| `web/index.html` | `apple-touch-icon` link; the manifest link keeps its `id` for the swap | 1, 2 |
| `web/tsconfig.app.json`, `web/tsconfig.node.json` | `icons.test.ts` reads bytes, so it is checked by the Node project, not the browser one | 1 |
| `web/eslint.config.js` | A block for `public/*.js` with the service-worker globals | 3 |
| `web/src/test/sw-harness.ts` | Fake `ServiceWorkerGlobalScope`, fake `CacheStorage`, event dispatch | 3 |
| `web/public/sw.js` | The worker: precache, purge, route, update handshake | 3–8 |
| `web/src/test/sw.test.ts` | Every branch of the worker, through dispatched events | 3–8 |
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
| `web/README.md`, `docs/web-frontend.md`, `docs/backlog/low/pwa-phase4-followups.md`, `docs/superpowers/qa/2026-09-16-pwa-phase4-ios-checklist.md` | Docs, backlog, device QA | 13 |

### The contract between tasks (names used exactly as spelled)

```ts
// public/sw.js — not a module; these are file-local and never imported. Listed
// so tasks 4–8 extend the same names task 3 introduces.
const VERSION = "v1";
const CACHE = `alfred-shell-${VERSION}`;   // every cache this origin holds starts "alfred-"
const SHELL = "/index.html";               // the one navigation response, cached under one key
const MANIFEST = "/__shell-manifest";      // install's record of what it cached, for activate to prune against
function assetsInHtml(html: string): string[];
function fontsInCss(css: string): string[];
function isShell(response: Response): boolean;   // ok && content-type is text/html
function isAsset(response: Response): boolean;   // ok && content-type is NOT text/html
async function precache(): Promise<void>;        // install
async function purge(): Promise<void>;           // activate
async function navigate(request): Promise<Response>;
async function asset(request, pathname: string): Promise<Response>;

// src/test/sw-harness.ts
export interface Harness {
  /** cache name → (absolute URL → Response). The whole of the fake CacheStorage. */
  store: Map<string, Map<string, Response>>;
  /** Program `self.fetch`: exact pathname → the response to answer with. */
  respond(pathname: string, response: Response): void;
  /** Every pathname `self.fetch` was called with, in order. */
  fetched: string[];
  /** Of those, the ones asked for with `{ cache: "reload" }`. */
  reloaded: string[];
  install(): Promise<void>;
  activate(): Promise<void>;
  /** `undefined` means the worker never called respondWith — i.e. left it to the network. */
  request(init: { path: string; mode?: string; method?: string; origin?: string }): Promise<Response | undefined>;
  message(data: unknown): Promise<void>;
  skipWaitingCalls: number;
  claimCalls: number;
  unregisterCalls: number;
  navigated: string[];
  /** Everything currently in CACHE, as pathnames, sorted. */
  keys(name?: string): string[];
}
export const ORIGIN = "https://alfred.example.com";
export function loadWorker(source: string): Harness;
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

// src/lib/theme.ts — newly exported; the value is unchanged
export const THEME_COLOR: Record<Theme, string>;   // { dark: "#25221F", light: "#F6F3EE" }
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

`web/public/manifest.json` today names one SVG icon and carries `#25221F` for both colours. `web/README.md` calls the second half a defect and defers it here: *"a manifest cannot follow the theme the way `applyTheme` rewrites the `theme-color` meta, so a light-hour install still gets a dark splash until phase 4 decides otherwise."* Decision: it follows the theme, through the link.

**Files:**
- Modify: `web/public/manifest.json`
- Create: `web/public/manifest-light.json`
- Modify: `web/src/lib/theme.ts`, `web/src/lib/theme.test.ts`
- Create: `web/src/test/manifest.test.ts`
- Modify: `web/index.html`, `web/src/test/index-html.test.ts`

- [ ] **Step 1: Write the failing tests for the two files**

`web/src/test/manifest.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { THEME_COLOR } from "@/lib/theme";

function manifest(name: string): Record<string, unknown> {
  return JSON.parse(
    readFileSync(fileURLToPath(new URL(`../../public/${name}`, import.meta.url)), "utf8"),
  ) as Record<string, unknown>;
}

const dark = manifest("manifest.json");
const light = manifest("manifest-light.json");

describe("the manifest", () => {
  it("is an installable standalone app with a stable identity", () => {
    // `id` is what a browser keys the install on. Without it the id defaults to
    // start_url, and any later change to start_url orphans every install.
    expect(dark).toMatchObject({
      id: "/",
      name: "Alfred",
      short_name: "Alfred",
      start_url: "/",
      scope: "/",
      display: "standalone",
      orientation: "portrait",
    });
  });

  it("ships the raster icons iOS and Chrome each need", () => {
    expect(dark.icons).toEqual([
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any maskable" },
    ]);
  });

  it("carries each theme's own ground", () => {
    expect(dark.theme_color).toBe(THEME_COLOR.dark);
    expect(dark.background_color).toBe(THEME_COLOR.dark);
    expect(light.theme_color).toBe(THEME_COLOR.light);
    expect(light.background_color).toBe(THEME_COLOR.light);
  });

  it("differs between the two files in nothing but those two colours", () => {
    // Two files is the price of a static manifest and an hourly theme. The
    // price is only worth paying if they cannot drift apart in anything else.
    const strip = (m: Record<string, unknown>) => {
      const { theme_color: _t, background_color: _b, ...rest } = m;
      return rest;
    };
    expect(strip(light)).toEqual(strip(dark));
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

Expected: FAIL — `THEME_COLOR` is not exported (`manifest.test.ts` will not even import), and the three `applyTheme` tests fail on `href` still reading `/manifest.json`.

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

- [ ] **Step 6: Export `THEME_COLOR` and swap the link in `web/src/lib/theme.ts`**

```ts
/**
 * `--bg` per theme, as index.css declares it. Safari's chrome reads it from the
 * meta; the two manifests carry it as `theme_color` and `background_color`, and
 * `src/test/manifest.test.ts` holds those files to this record.
 */
export const THEME_COLOR: Record<Theme, string> = { dark: "#25221F", light: "#F6F3EE" };

/** The manifest per theme. See `applyTheme` for why there are two. */
const MANIFEST: Record<Theme, string> = {
  dark: "/manifest.json",
  light: "/manifest-light.json",
};
```

and, inside `applyTheme`, after the `theme-color` line:

```ts
  // A manifest is a static file and the theme is the hour's, so there are two
  // of them and the link points at whichever is true now. iOS reads the
  // manifest at Add-to-Home-Screen time — after this has run — so the launch
  // screen matches the theme the app was installed under, which is the defect
  // phase 1 deferred to phase 4 (web/README.md). On iOS it is
  // `background_color` that this really fixes: the status bar is
  // `black-translucent`, so `theme_color` never gets a look in there.
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

Expected: `Test Files 72 passed (72)`, all green.

- [ ] **Step 9: Commit**

```bash
git add web/public/manifest.json web/public/manifest-light.json web/src/lib/theme.ts \
        web/src/lib/theme.test.ts web/src/test/manifest.test.ts web/index.html \
        web/src/test/index-html.test.ts
git commit -m "feat(web): a manifest per theme, so a light-hour install gets a light splash"
```

Counts after this task: **72 files / ~1 524 tests**.

---

## Task 3: The service-worker harness, and the first thing the worker does

A service worker has no DOM, so Testing Library is no help; and jsdom has no `ServiceWorkerGlobalScope`, so there is nothing to mount it in. The harness loads the **shipped bytes** — `public/sw.js` through Vite's `?raw`, exactly as `index-html.test.ts` loads the shipped `index.html` — into a hand-built scope with `new Function("self", source)(scope)`. That works only because every global the worker touches goes through `self.`, which is why that is a convention rather than a style note.

Two jsdom facts that shape the harness, both worth knowing before you write it:

- **Do not build real `Request` objects.** Node's undici throws `Cannot construct a Request with mode 'navigate'`, and `request.mode === "navigate"` is the whole of the navigation branch. The worker only ever reads `.url`, `.method` and `.mode`, so the harness passes a plain object and the real worker gets a real `Request`. The shapes agree on everything the worker uses.
- **`Response` is a Node 22 global** and survives the jsdom environment (jsdom implements no fetch, so vitest leaves Node's in place). If a future Node removes it, import it from `node:undici` in the harness only.

**Files:**
- Create: `web/src/test/sw-harness.ts`
- Create: `web/src/test/sw.test.ts`
- Create: `web/public/sw.js`
- Modify: `web/eslint.config.js`

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
  constructor(readonly entries: Map<string, Response>) {}
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
    // next one. Every install fetch, not merely the HTML.
    expect(worker.reloaded).toEqual(worker.fetched);
  });

  it("fails the install rather than caching half a shell", async () => {
    const worker = loadWorker(source);
    worker.respond("/index.html", html(SHELL_HTML));
    worker.respond("/assets/index-nhBuGG1b.css", css("body{color:red}"));
    // The entry chunk has no route: the network is gone mid-install. A
    // half-precached shell is a shell that boots to a blank screen offline.
    await expect(worker.install()).rejects.toThrow();
  });
});
```

Delete the third test's stray loop before you commit — it is there in the plan only to make the point that `worker.fetched` is the assertion; write it as:

```ts
  it("fetches past the HTTP cache, so a deploy cannot be precached stale", async () => {
    const worker = shell();
    await worker.install();
    expect(worker.fetched).toEqual([
      "/index.html",
      "/assets/index-CY0Sh5Q2.js",
      "/assets/index-nhBuGG1b.css",
    ]);
  });
```

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

Expected: PASS, 5 tests. ESLint silent — if it complains that `self` is undefined, the `public/*.js` block from step 1 is missing or misplaced.

- [ ] **Step 7: Commit**

```bash
git add web/eslint.config.js web/public/sw.js web/src/test/sw-harness.ts web/src/test/sw.test.ts
git commit -m "feat(web): a service worker that precaches the shell index.html names"
```

Counts after this task: **73 files / ~1 529 tests**.

---

## Task 4: The fonts, and the shell that is not a shell

Two holes in task 3's precache. The first: `dist/assets/` holds **20 font files**, referenced from the stylesheet's `url()` rules and not from the HTML, so a cold offline launch currently renders in `-apple-system` rather than DM Sans. The second: `mount_spa`'s fallback makes a 200 meaningless, and task 3 guards the *assets* against it but not the *shell fetch* against a redirect to something that is not HTML at all.

`@fontsource/geist-mono` ships a `.woff` beside every `.woff2`. No browser that can run a service worker asks for a `.woff`, so precaching them would double the install for nothing; anything missed is still picked up cache-first on first use (task 7).

**Files:**
- Modify: `web/public/sw.js`
- Modify: `web/src/test/sw.test.ts`

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

  it("refuses a shell that is not HTML", async () => {
    const worker = loadWorker(source);
    // A proxy misconfiguration, a captive portal, an auth redirect: whatever it
    // is, it is not this app, and caching it makes every offline launch show it.
    worker.respond("/index.html", js("not a page"));
    await expect(worker.install()).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run them and read the failures**

```bash
npx vitest run src/test/sw.test.ts -t "fonts"
```

Expected: FAIL on the first three (no font is fetched or cached at all); the last two already pass, because `isShell`/`isAsset` landed in task 3 — leave them in, they are the regression guard for those two branches.

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

Expected: PASS, 10 tests. Task 3's `records what it cached` still expects three entries, because that fixture's stylesheet has no `url()` at all — check it did not silently change.

- [ ] **Step 5: Commit**

```bash
git add web/public/sw.js web/src/test/sw.test.ts
git commit -m "feat(web): precache the woff2 the stylesheet names, and only those"
```

Counts after this task: **73 files / ~1 534 tests**.

---

## Task 5: Activate — the versioned purge

The requirement: *a versioned cache with an explicit activate-time purge of old versions*. Two purges, because there are two ways stale bytes accumulate. **Between versions:** every cache whose name is not `CACHE` goes, which is what `VERSION` is for — bump it and the next activate takes the whole previous world with it. **Within a version:** every entry that is not in the manifest install just wrote goes, which is what keeps a cache from growing by one build's assets on every deploy. Both matter on iOS, where an origin over its storage budget is evicted wholesale rather than trimmed.

One wrinkle, stated in a comment rather than engineered around: the new worker installs into the same cache the old one is still serving from, so for the seconds between install and activate the cached shell is the *new* HTML. Its assets are already cached by then, and the only reader of the cached shell is an offline navigation, so the window is both correct and nearly unreachable.

**Files:**
- Modify: `web/public/sw.js`
- Modify: `web/src/test/sw.test.ts`

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

Expected: FAIL — there is no `activate` listener, so nothing is deleted and `claimCalls` is 0.

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

Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
git add web/public/sw.js web/src/test/sw.test.ts
git commit -m "feat(web): purge every other cache and every stale entry on activate"
```

Counts after this task: **73 files / ~1 539 tests**.

---

## Task 6: What the worker refuses to touch

This is constraint 3 of the header comment and decision 3 of the plan, and it is the task most worth over-testing. **The worker never calls `respondWith()` for `/api/*`, `/health`, `/ws*`, a cross-origin URL, or any method but GET.** Not "network-only" — never seen, so nothing downstream of this branch can cache one.

`/ws` and `/ws/telemetry` are in the guard although a WebSocket upgrade never reaches a `fetch` handler. They are named so nobody removes a guard they believe is dead, and — more usefully — so nobody adds a *caching* branch they believe is needed.

**Files:**
- Modify: `web/public/sw.js`
- Modify: `web/src/test/sw.test.ts`

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

- [ ] **Step 3: Add the fetch listener with the refusals and nothing else**

```js
self.addEventListener("fetch", (event) => {
  const request = event.request;
  /* A POST is never a shell request, and the Cache API cannot store one anyway. */
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  /* Nothing in this client loads a cross-origin URL, and if one ever appears it
     is not the shell and must not land in the shell's cache. */
  if (url.origin !== new URL(self.registration.scope).origin) return;

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

Expected: PASS, 28 tests. ESLint silent.

- [ ] **Step 5: Commit**

```bash
git add web/public/sw.js web/src/test/sw.test.ts
git commit -m "feat(web): the worker never sees /api, /health or the sockets

These tests pass vacuously until task 7 adds the branches that could have
broken them; they are the regression guard from that point on."
```

Counts after this task: **73 files / ~1 552 tests**.

---

## Task 7: Navigation and assets

Two branches. A navigation goes to the network first and falls back to the cached shell — decision 8, and the reason a bad cache can only ever be seen by an offline phone. An `/assets/*` URL is content-hashed and served `immutable` by `SpaCacheMiddleware`, so it is cache-first, with the network filling a miss.

**Files:**
- Modify: `web/public/sw.js`
- Modify: `web/src/test/sw.test.ts`

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

Expected: PASS, 37 tests — including all thirteen of task 6's, which now have branches that could have broken them.

- [ ] **Step 5: Commit**

```bash
git add web/public/sw.js web/src/test/sw.test.ts
git commit -m "feat(web): network-first navigations, cache-first hashed assets"
```

Counts after this task: **73 files / ~1 561 tests**.

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

Expected: PASS, 40 tests. ESLint silent, build clean, and `web/dist/sw.js` now exists:

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

A bad precache on an installed iOS PWA is very hard for a user to clear: there is no address bar, no developer tools, no "clear site data", and Settings › Safari › Clear History does not reliably reach a home-screen app's storage. The only remote lever is the bytes at `/sw.js`. So the kill switch is a **file**, checked in and tested, and the procedure is a copy.

**It cannot be a deletion.** `mount_spa` (`core/channels/spa.py:42-49`) answers any unknown path with `index.html` and a 200, so deleting `web/public/sw.js` makes `/sw.js` serve a page of HTML. The browser's update check rejects it on MIME type, the update fails, and **the old worker stays installed and keeps serving its cache**. Deleting the file is the one action that looks like a fix and is not.

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
3. **The four events** — install (fetch `/index.html`, read its `/assets/` URLs, follow the stylesheet to the `woff2`, write the manifest sentinel, and **never** `skipWaiting`); activate (drop every other cache, drop every entry not in the sentinel, claim); fetch (navigation network-first, `/assets/*` cache-first, everything else untouched); message (`SKIP_WAITING` only).
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
  6. **No `apple-touch-startup-image`** (decision 11). If the manifest-derived launch screen turns out to be wrong on the device, the per-device matrix is what replaces it.
  7. **The two manifests are a copy, kept honest by a test** rather than generated. A `vite build` hook that emits the light one from the dark one would remove the copy; it would also be the first build-time codegen in this tree.
  8. **`navigator.onLine` is the only offline signal at cold launch.** It is true on a network with no route to the house — which lands in `AuthGate`'s existing fail-closed branch rather than the offline gate, so it is correct, but it means a phone on a captive portal sees `SignInGate` rather than an explanation.
  9. **Nothing tests that `dist/sw.js` exists.** Task 14 step 4 checks it by hand. A Python-side assertion in `tests/core/channels/test_spa_ci.py` — `/sw.js` answers with a JavaScript content type and not `text/html` — would make a regression here a CI failure instead of a device failure, and that regression is the one that silently leaves every phone on its old worker.

- [ ] **Step 5: the QA checklist** — `docs/superpowers/qa/2026-09-16-pwa-phase4-ios-checklist.md`, in the phase-3 checklist's format. **This box is headless: none of this can be run here.** The list:
  - **Install.** Safari → Share → Add to Home Screen. The tile shows the accent `A` on the dark ground with **no black corners** and no page screenshot. The name under it reads `Alfred`.
  - **Splash.** Install during daylight with the light theme active; the launch screen is `#F6F3EE`, not `#25221F`. Then switch to dark, reinstall, and confirm it is `#25221F`. This is the phase-1 defect task 2 closed, and it is the only way to verify it.
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

- [ ] **Step 7: Push and open the PR.** Body: what phase 4 adds (manifest pair, icon set, worker, kill switch, cold-offline gate); **the deviations table, first, because it is the most useful thing a reviewer can read**; the `vite-plugin-pwa` decision and its reasoning; an explicit note that the §6.4 install rail is phase 5's and why; the backlog file; and that the device checklist is unrun, with the splash-colour and kill-switch steps called out as the two that cannot be verified any other way.

```bash
git push -u origin feat/pwa-phase4-install
gh pr create --title "PWA phase 4: install, service worker and offline states" --body "…"
```

**Do not merge.** Merging `master` deploys to the live box; the merge is the user's call.

---

## Self-review of this plan

**Spec coverage.** §8's phase-4 content, item by item: *manifest* → task 2; *icons* → task 1; *service worker* → tasks 3–8, with the kill switch in 11 and the page-side half in 9; *offline states* → task 12 (the cold launch) over the query-layer work phases 1–3 already shipped, which decision 5 explicitly does not reopen. §4.2 (raster icons, opaque, maskable) → task 1, tested at the byte level. §4.1 (secure context) → unchanged; HTTPS via NPM landed in phase 0. §4.9b (no `beforeinstallprompt` on iOS) → decision 6; the teaching rail is phase 5's by §8's own wording. §4.10 (iOS kills standalone PWAs) → the existing `onVisible` rehydration, now also driving `registration.update()` in task 9. §7's "Service worker policy" — *precache the app shell; cache-first for hashed assets; network-only for `/api/*` and both sockets* — is implemented exactly, with "network-only" strengthened to "never handled"; §7's `vite-plugin-pwa` recommendation is the one thing deviated from, argued in decision 2 and listed as deviation 1. §3.4's asset cache headers are already on `master` (`SpaCacheMiddleware`) and are confirmed in "Before you start" step 3 rather than re-done.

**Known gaps, stated rather than hidden.** (a) The §6.4 six-step rail is not built — decision 6, deviation 2, backlog §1. (b) Web Push is phase 5's entirely; task 13 writes down what phase 4 hands it. (c) Two of the four copy strings in this phase are invented, because the handoff has none for an update prompt or a cold offline launch — deviations 3 and 4, each naming the handoff line it borrows its cadence from. (d) Nothing here is verified on a device; the two steps that *can only* be verified on one (the splash colour and the kill switch round trip) are called out in task 14 step 7 so the PR says so.

**Placeholder scan.** Every code step carries real code. The two places a step says "read the file first" — `theme.test.ts`'s `beforeEach` in task 2 step 2, `main.test.ts`'s existing shape in task 9 step 6, and `AuthGate.test.tsx`'s `renderAuthGate` signature in task 12 step 5 — each give the code to write *and* the fallback if the existing shape differs, rather than deferring the decision. One thing the first draft got wrong and this review caught: task 3's `cache: "reload"` test originally asserted against a regex over the worker's own source, which is a test of how the file is *spelled* rather than of what it *does*, and it would have broken in task 4 the moment a third fetch site appeared. The harness records the request's cache mode instead (`Harness.reloaded`), and the assertion is `reloaded === fetched` — true at task 3, still true at task 4, and false the moment a fetch site drops the flag.

**Type consistency.** Every name a later task uses is fixed in the contract block: `CACHE`/`SHELL`/`MANIFEST`/`VERSION` and `isShell`/`isAsset`/`assetsInHtml`/`fontsInCss`/`precache`/`purge`/`navigate`/`asset` in the worker (tasks 3–8 extend the same file and never rename); `Harness`'s eleven members, `ORIGIN`, and the four response factories in the test harness (tasks 3–8 and 11 all drive it); `SW_URL`/`SwOptions`/`installServiceWorker`/`onUpdateReady`/`applyUpdate`/`unregisterAll` in `lib/sw.ts` (task 9 defines, task 10 consumes); `useUpdateReady`, `useOnline`, `UpdateNoteProps`, `OfflineGate`; and `THEME_COLOR`, which task 2 exports from `lib/theme.ts` and `src/test/manifest.test.ts` imports. The cache name `alfred-shell-v1` appears in the worker as a template literal over `VERSION` and in the harness as a constant — a `VERSION` bump means editing both, which is noted in task 13 step 1 item 5.
