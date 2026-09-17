# Alfred — web client

The phone-first PWA that replaced the Mission Control SPA. One screen (the Room),
one interrupt (the Door), four identity gates over the top of them — and, under
the Room, the Workshop, where the house's streams are read as they happen.

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
src/lib/        no React: api, sockets, formatters, the presence physics,
                the action reducer, the slide maths, audio and recording,
                the stream catalogue (streams.ts), the feed reducer (feed.ts),
                the causal thread (trace.ts) — and one module per bench:
                memory.ts (the three-shape episodic adapter), triggers.ts
                (kinds and meta lines), system.ts (sessions, credentials,
                pairing, integrations, attention, the health cells)
src/shell/      providers, the two surfaces everything rises on, and the
                ErrorBoundary the Workshop wraps its panel in
src/gates/      setup, sign-in, expired, denied — and the router between them
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
src/test/       jsdom setup, shared fixtures, and the contrast restatement
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

## What phases 1 to 3 cover

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

## What it does not

- **Install and Reach gates, and Web Push** — the rest of phase 4, and phase 5.
  The standalone metas are already in `index.html`. The **icon set, the manifests and a
  service worker have shipped** — see "The icons" below, and "The service worker".
  Nothing registers the worker yet, so it is inert bytes in `public/` until it is. There are two manifests, `manifest.json` and
  `manifest-light.json`, identical but for `theme_color` and `background_color`, and
  `applyTheme` swaps the `<link rel="manifest">` alongside the `theme-color` meta.
  **Read that narrowly, because it is not yet known to fix anything on a phone.** A
  manifest is consumed at *install* time, so the most this can do is align an install
  with the theme in force at Add to Home Screen; it does not follow the theme afterwards,
  and someone who installs at 22:00 has a dark launch screen at noon for ever. On iOS it
  may do nothing at all: WebKit fetches at most one manifest per document load and has no
  observer for a later `href` change, and the swap runs after parsing, so the manifest
  iOS reads may always be the dark one `index.html` ships. iOS also builds its launch
  screen from `apple-touch-startup-image` links, which this app does not ship (see the
  phase-4 backlog) — not from the manifest. Chromium *does* observe the link, which is
  where the value is; it is also where the cost is, since Chrome's WebAPK update check
  fingerprints exactly those two colours. None of this is measured: the phase-4 device
  checklist is the only thing that can settle it, and it is unrun.
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

`public/sw.js` is hand-written — 83 lines of code under twice as many of comment — and
shipped verbatim by Vite's `public/` copy: no `vite-plugin-pwa`, no Workbox, no generated
precache manifest. The reasoning is in
`docs/superpowers/plans/2026-09-16-pwa-phase4-install-and-offline.md`; the short version
is that a versioned cache, an explicit purge and a documented kill switch all have to be
things a reviewer can *read in the shipped bytes*, and a negative requirement — "this
worker never caches `/api`" — is one assertion against a hand-written file and an argument
against a plugin's route table.

**What it does today is install, and only install.** It fetches `/index.html` past the
HTTP cache and takes the `/assets/…` URLs Vite wrote into it as the precache manifest —
the entry chunk and the stylesheet. It then reads the **stylesheet** for the `url()` rules
the HTML never mentions and takes the `.woff2` among them: 8 files, 139 436 of this build's
220 380 font bytes. The 10 `.woff` fallbacks `@fontsource` ships beside them are skipped,
because nothing that can run a service worker has needed one for years. All of it plus a
`/__shell-manifest` record of what it actually cached goes into `alfred-shell-v1`.

Three things about how it fetches. Everything goes out `cache: "reload"`, so a worker
installing seconds after a deploy cannot precache the bytes that deploy replaced.
Everything carries a 10-second `AbortSignal`, because `install` runs inside `waitUntil()`
and one request that is accepted and never answered — a captive portal — would otherwise
hang the install until the browser killed it, leaving the phone with no worker at all. And
a missing *font* is survivable where a missing *chunk* is not: the app is legible in the
system stack, so the font loop declines and carries on while the asset loop throws.

What may be cached is one function, `isAssetUrl`: same origin, under `/assets/`, no encoded
separator. The extraction either side of it is deliberately wide so that widening it cannot
widen the cache.

It has no `fetch` handler, so every request still goes to the network untouched, and
**nothing registers it yet** — `src/lib/sw.ts` is a later slice of phase 4. Until then it
is inert bytes in `public/`.

Three rules govern it, and they are restated at the top of the file itself:

1. **It caches the app shell and nothing else.** `/api/*`, `/health` and the two sockets
   are never fetched, never stored, and will never be passed to `respondWith()`. This
   client's whole design is that it never states a server fact it has not just read; a
   cached API response is that failure with a longer half-life.
2. **Navigations will be network-first**, so a bad cache is invisible to an online phone
   and the browser's own update check on `/sw.js` keeps running — which is what lets a
   kill switch land if this file ever has to be withdrawn.
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

The tests are `src/test/sw.test.ts` (the worker, driven event by event) and
`src/test/sw-harness.test.ts` (the fake scope itself — it has to model `ExtendableEvent`,
`Cache.put` and `Clients.matchAll` closely enough that a test passing here means a worker
that runs on a phone).

## Things worth knowing before you change something

- **The icons are checked-in bytes, not a build step.** `public/icon.svg` is the
  source — the handoff's home-screen mark (`Alfred.dc.html:530`), a capital `A` drawn as
  two stroked paths on a full-bleed square — and the three PNGs beside it are rendered
  from it once and committed. Nothing in the build, the container image or CI rasterises
  anything, so `rsvg-convert` is needed only by whoever changes the drawing:

  ```bash
  cd web
  rsvg-convert -w 180 -h 180 -b '#25221F' public/icon.svg -o public/apple-touch-icon.png
  rsvg-convert -w 192 -h 192 -b '#25221F' public/icon.svg -o public/icon-192.png
  rsvg-convert -w 512 -h 512 -b '#25221F' public/icon.svg -o public/icon-512.png
  ```

  `-b` is what forces an opaque background: iOS composites transparency on a home-screen
  tile as **black**, so the PNGs must carry no alpha (spec §4.2), which is also why the
  square is full-bleed rather than rounded — iOS applies its own mask. ImageMagick is a
  working substitute (`magick -background '#25221F' -density 1024 … -alpha remove -alpha off`);
  if either tool leaves the alpha channel in place, `magick in.png -alpha off PNG24:out.png`
  strips it. `src/test/icons.test.ts` decodes the committed PNGs and checks the pixels —
  the two colours and their census, the mark's reach against the maskable safe circle, and
  a scan down the middle that reads apex, counter, crossbar, feet — so a regenerated icon
  that came from a different drawing fails rather than ships. Both colours come from
  `--bg` and `--accent` via `contrast.ts`, so retuning the palette fails that test until
  the icon is redrawn and re-rendered.
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
  last-known Room stays visible behind them.
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
