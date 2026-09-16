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

- **Install and Reach gates, the service worker, icons and Web Push** — phases 4
  and 5. The standalone metas are already in `index.html`, and `public/manifest.json`
  carries the phase-1 palette's dark ground — a manifest cannot follow the theme the way
  `applyTheme` rewrites the `theme-color` meta, so a light-hour install still gets a dark
  splash until phase 4 decides otherwise. Nothing else in `public/` has been touched.
- **Desktop** — phase 6.

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
