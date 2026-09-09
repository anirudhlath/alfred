# Alfred — web client

The phone-first PWA that replaced the Mission Control SPA. One screen (the Room),
one interrupt (the Door), and four identity gates over the top of them.

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
                the action reducer, the slide maths, audio and recording
src/shell/      providers and the two surfaces everything rises on
src/gates/      setup, sign-in, expired, denied — and the router between them
src/room/       the one screen: presence field, headline, timeline, composer
src/door/       the approval interrupt: banner, fuse, slide, deep link
src/sheets/     the held-back queue
src/test/       jsdom setup and shared fixtures
```

Tests live beside their source (`lib/history.ts` → `lib/history.test.ts`).

## Conventions

- Colours are CSS custom properties (`var(--accent)`), never Tailwind palette
  colours. Both themes are defined in `src/index.css` under `:root[data-theme]`.
- Type comes from the `.t-*` classes, not ad-hoc sizes.
- Imports use the `@/` alias; siblings are relative inside `src/lib/` and in tests.
- The status vocabulary is closed (spec §10). Phase 1 says `queued`, `applied`,
  `last true HH:MM` and `expired · not done`; `unknown since HH:MM`,
  `takes effect within 60 s`, `hot / cold` and `candidate · active · dormant · archived`
  are the Workshop's words and arrive with it. Mono, lower case — the one exception is
  the Door's phase pill (`Confirmed · queued`, `Applied`, `Expired`, `Answered`), set in
  the layer's own type, ink on paper. Do not invent new words for system state.

## What phase 1 covers

Shell, theme and viewport; the four identity gates; the Room (presence field,
headline, status line, offline note, DND row and the held-back sheet, the merged
timeline, composer, hold-to-talk, notifications); and the Door (banner, fuse,
slide-to-confirm, the five phases, tombstones, and the `/actions/:id` deep link).

## What it does not

- **The Workshop** (Activity, Memory, Triggers, System) — phase 2 and 3. There is
  no handle, no `why?` button and no causal thread yet.
- **Install and Reach gates, the service worker, icons and Web Push** — phases 4
  and 5. The standalone metas are already in `index.html`, and `public/manifest.json`
  carries the phase-1 palette's dark ground — a manifest cannot follow the theme the way
  `applyTheme` rewrites the `theme-color` meta, so a light-hour install still gets a dark
  splash until phase 4 decides otherwise. Nothing else in `public/` has been touched.
- **Desktop** — phase 6.

## Things worth knowing before you change something

- `--app-height` and `--keyboard-inset` are written by `installViewportVars()`
  from `innerHeight` and `visualViewport`. `100vh` is wrong in Safari; do not reach
  for it, and do not size the column from the visual viewport: the keyboard is paid
  for once, by `.pb-keyboard`.
- All audio plays through one `AudioContext` unlocked by the first tap
  (`lib/audio.ts`). A fresh `new Audio()` is silently dropped on iOS until then.
- `MediaRecorder` must negotiate `audio/mp4` → `audio/aac` → default. Safari has
  no WebM and throws from the constructor if you ask for it.
- A 200 from `POST /api/actions/{id}/confirm` means **queued**. `applied` comes
  only from the `home_action_results` telemetry stream.
- 401 and 403 raise a gate over whatever is on screen; they never redirect. The
  last-known Room stays visible behind them.
