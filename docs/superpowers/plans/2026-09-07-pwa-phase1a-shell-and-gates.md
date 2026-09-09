# PWA Phase 1a — Shell, theme, viewport and the identity gates

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `web/` with the shell the approved design needs — tokens, type scale, motion, viewport/keyboard handling, the API and socket layer, and all four identity gates (setup, sign-in, expired, denied) — ending with an app that boots into a themed, safe-area-correct Room frame carrying the live status line. Tasks 15–28 (`docs/superpowers/plans/2026-09-07-pwa-phase1b-room-and-door.md`) fill that frame with the Room and the Door before the PR opens.

**Architecture:** A hard cut, not a retrofit. Task 1 deletes the outgoing Mission Control SPA (pages, shadcn component library, icon rail, telemetry rail) and its dependencies, leaving only the four library modules that are still correct: `ws.ts`, `chat-socket.ts`, `telemetry-socket.ts`, `webauthn.ts`. Everything else is built from the handoff. The shell is one column: `#root` is a flex column sized by `--app-height` (an `innerHeight` mirror that never shrinks for the keyboard; `--keyboard-inset`, from `visualViewport`, is what does), the Room is the only route, and gates are full-screen layers over it rather than separate URLs. Authentication stops being a redirect and becomes a layer: `api()` emits `expired` on 401 and `denied` on 403 through a tiny emitter, and `AuthGate` renders the matching gate over whatever is already on screen, so a lapsed session never blanks the last-known state.

**Tech Stack:** Vite 8 · React 19 · TypeScript 6 (`tsc -b`, `noUnusedLocals`, `verbatimModuleSyntax`) · Tailwind v4 (`@theme inline`, tokens as CSS custom properties on `:root[data-theme]`) · TanStack Query 5 · react-router 7 · Vitest 4 + Testing Library + jsdom · ESLint 10 (flat config, `react-hooks`, `react-refresh`). Fonts are self-hosted through `@fontsource-variable/dm-sans` and `@fontsource/geist-mono`.

**Spec:** `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md` — §4 (the twelve iOS constraints), §5 (capability inventory and honesty rules), §7 (stack and carry-over), §8 phase 1.
**Design handoff:** `docs/design/2026-09-04-pwa-client-handoff/README.md` (tokens, type scale, copy — final) and `Alfred.dc.html` (behaviour and maths — the source, not the summary).

---

## Product decisions already taken (do not reopen)

1. **Hard cut.** Phase 1 replaces `web/` entirely and deploys on merge to `master`. The old pages, the shadcn `components/ui` library and `AlfredProvider` are deleted in Task 1, not kept beside the new client.
2. **No Workshop in Phase 1.** No workshop handle, no `why?` buttons, no causal thread, no trace module. Those are Phase 2. Anything the handoff draws for the Workshop is out of scope here.
3. **No PWA plumbing in Phase 1.** Install gate, standalone gate, Reach/notification-permission gate, `vite-plugin-pwa`, service worker, icons and Web Push are Phases 4–5. `web/public/manifest.json` is left exactly as it is.
4. **Setup is three steps**: passkey registration → Home Assistant credentials (`GET /api/integrations`, entry `name === "home-service"`) → attention set (`GET /api/admin/attention`). `POST /api/onboarding` is not used; the design has no questionnaire.
5. **401 no longer redirects.** `api()` emits `expired` on `authEvents`; `AuthGate` raises the Expired gate over the children. 403 emits `denied` → Denied gate, dismissed with `Back to the room`. Socket close code 4001 emits `expired` too.
6. **Alfred's replies are plain text** (`white-space: pre-line`), never markdown — `react-markdown` is removed.
7. **The repository is public.** Every hostname in this plan, in code comments and in tests is `alfred.example.com`; every LAN address is `192.168.1.x`. The setup and sign-in gates read the real hostname from `location.hostname` at runtime.

### Deviations from the handoff (deliberate; flag them in review)

| # | Where | Handoff says | This plan ships | Why |
|---|---|---|---|---|
| 1 | Expired gate body | `The passkey session on this phone ran out after 30 days.` | `The passkey session on this phone ran out after eight hours. Anything below is last-known until you sign in again.` | Phase 0 cut the auth session TTL to 8 h (`_AUTH_SESSION_TTL = 8 * 3600`). The prototype's "30 days" is now false. This is the one copy change in Phase 1. |
| 2 | Room headline | `Quiet until 08:30.` only | adds `Quiet until further notice.` | DND can be set with no expiry (`dnd.until === null`), and the handoff itself flags that queue as the invisible failure. The headline must be able to say so. Used by 1b; the string is fixed here. |
| 3 | Gate kickers | `first run · alfred.local`, `alfred.local · signed out` | `first run · {location.hostname}`, `{location.hostname} · signed out` | The WebAuthn RP ID is pinned to the public hostname (spec §3.3) and the repo is public. The kicker is read at runtime. |
| 4 | Setup step 3 foot | `Change this any time under Workshop › System.` | unchanged, verbatim | Kept verbatim although the Workshop lands in Phase 2. Copy is final; the forward reference is accepted deliberately. |
| 5 | Sheet header `Done` | 44 px accent text button in the sheet header | unchanged, 44 px | The "50 px" sheet button in the brief is the sheet's *action* pill (`Drain queue now`), which plan 1b builds. The header `Done` stays as the handoff draws it. |
| 6 | `#root` height | `min-height:100dvh; min-height:var(--app-height)` | `min-height: var(--app-height, 100dvh)` | An undefined `var()` is invalid at computed-value time, which drops the property to `auto` rather than falling back to the previous declaration. The single-declaration form is the only one that actually keeps 100dvh before `installViewportVars()` has run. |

---

## Before you start

1. **Phases 0 and 0b must be merged.** This plan calls `GET /api/actions/pending`, `GET /api/admin/attention` and the 8-hour session. Check:

```bash
cd ~/code/alfred-deploy/alfred
git fetch origin
git show origin/master:core/identity/auth_routes.py | grep -n "_AUTH_SESSION_TTL = "
git show origin/master:core/channels/admin_api.py | grep -n '@router.get("/attention")'
```

Expected: `_AUTH_SESSION_TTL = 8 * 3600` and one `@router.get("/attention")` hit. If either is missing, stop — 0/0b are not on the trunk yet.

2. **Work in a fresh worktree** — never commit from `~/code/alfred-deploy/alfred` (that is the live checkout the deploy runner builds from):

```bash
cd ~/code/alfred-deploy/alfred
git worktree add ~/code/.worktrees/alfred/pwa-phase1-client -b feat/pwa-phase1-client origin/master
cd ~/code/.worktrees/alfred/pwa-phase1-client
git log --oneline -1
```

Expected: `4bebd6d fix(runner): keep draining child stdout past oversized lines; pin litellm log level (#238)`.

Plan 1b continues in this same worktree on this same branch; do not open a second one.

3. **Baseline the outgoing client before you delete it** — a red baseline means the checkout is broken, not your work:

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client/web
npm ci
npm run lint && npm test && npm run build
```

Expected: eslint prints nothing, vitest ends with `Test Files  N passed`, and vite prints `✓ built in …`. Keep `web/dist/` — the Python SPA gate skips itself without it.

4. **How to run things** (all from `web/`, except the SPA gate):

| Command | What it does |
|---|---|
| `npm test` | The whole vitest suite, once |
| `npm test -- src/lib/theme.test.ts` | One file |
| `npm test -- -t "resolves the stored theme"` | One test by name |
| `npm run lint` | ESLint over `web/` |
| `npm run build` | `tsc -b` then `vite build` — the type check lives here, not in lint |
| `cd .. && uv run pytest tests/core/channels/test_spa_ci.py -q` | The CI gate that serves the built `web/dist` |

5. **CI shape.** `.github/workflows/ci.yml` runs `npm ci && npm run lint && npm run test && npm run build` in `web/`, uploads `web/dist`, and the `spa` job runs `tests/core/channels/test_spa_ci.py` against it. `npm ci` means **`package-lock.json` must be committed with every dependency change**.

---

## Conventions

- **TDD, always.** Failing test → run it and read the failure → implement → run it green → commit. Every task ends with at least one commit; a task that touches two concerns may commit twice.
- **Conventional commits.** `feat(web): …`, `chore(web): …`, `test(web): …`, `refactor(web): …`. The `pr-title` CI job enforces the same shape on the PR title.
- **Imports use the `@/` alias** (`@/lib/api`, `@/shell/ThemeProvider`) except inside `src/lib/`, where sibling modules are imported relatively (`./api`) — that is the existing convention in `lib/` and the carried-over files already do it.
- **Named exports everywhere except `App.tsx`**, which keeps its default export because `main.tsx` imports it that way.
- **Colours come from CSS custom properties**, never from Tailwind's default palette. In JSX that is `style={{ color: "var(--muted)" }}` or a token utility from `@theme inline` (`bg-surface`, `text-fg2`). No `text-slate-400`, ever.
- **Type scale comes from the `.t-*` classes**, not ad-hoc `text-[15px]`. Sizes that are not in the scale (a 13 px note, a 10.5 px unsent stamp) are written as explicit arbitrary values.
- **Tests live next to the source** (`lib/theme.ts` → `lib/theme.test.ts`, `gates/Gate.tsx` → `gates/Gate.test.tsx`). Shared fixtures go in `src/test/fixtures.ts`.
- **Each test file builds its own providers.** There is no shared render helper; a six-line `wrapper` with a fresh `QueryClient` per file keeps tasks independent and stops one file's cache leaking into another.
- **`react-refresh/only-export-components`** fires on files that export a component *and* a hook. Silence it per line with `// eslint-disable-next-line react-refresh/only-export-components`, exactly as the outgoing `AlfredProvider.tsx` did.
- **The status vocabulary is closed** (handoff): `queued`, `applied`, `last true HH:MM`, `unknown since HH:MM`, `hot / cold`, `candidate · active · dormant · archived`, `expired · not done`, `takes effect within 60 s`. Always mono, always lower case. Do not invent new words for system state.
- **No placeholders, no dead code.** If a file is written in this plan it is written complete; if a name is used it was defined in an earlier task with exactly that spelling.

---

## File Structure

Everything below is under `web/` unless noted. "1b" marks files plan 1b creates — they are listed so the layout is visible, but this plan must not create them.

| File | Change | Responsibility |
|---|---|---|
| `package.json`, `package-lock.json` | Modify | Drop the SPA's dependency tree; add the two font packages |
| `index.html` | Rewrite | `viewport-fit=cover`, `interactive-widget=resizes-content`, the two `theme-color` metas, Apple standalone metas |
| `src/main.tsx` | Rewrite | `installViewportVars()` then `createRoot → <App/>` |
| `src/App.tsx` | Rewrite (Tasks 1, 14) | Providers and the two routes |
| `src/index.css` | Rewrite (Tasks 1, 3, 4) | Tailwind import, fonts, tokens per `[data-theme]`, `@theme inline`, type scale, keyframes, safe areas |
| `src/lib/types.ts` | Rewrite | The shared type contract (below) |
| `src/lib/format.ts` | Modify (Tasks 1, 9, 10, 13) | `hhmm`, `dayMonth`, `mmss`, `usd`, `evs`, `shortId`, `humaniseTool`, `rawCall`, `dayLabel` (+ the carried-over `summarize`/`timeOf`) |
| `src/lib/theme.ts` | Create | `Theme`, `THEME_KEY`, `resolveInitialTheme`, `applyTheme`, `rememberTheme`, `storedTheme` |
| `src/lib/viewport.ts` | Create | `installViewportVars`, `keyboardInset`, `useKeyboardOpen`, `KEYBOARD_OPEN_PX` |
| `src/lib/auth-events.ts` | Create | `authEvents.on(kind, fn)` / `.emit(kind)`, kind `expired` \| `denied` |
| `src/lib/api.ts` | Modify | `api`/`post`/`put`/`del`, `ApiError{status, detail}`, 401→expired, 403→denied |
| `src/lib/auth.ts` | Create | `fetchAuthStatus`, `DEVICE_KEY`, `rememberDevice`, `rememberedDevice`, `defaultDeviceName`, `deviceFootLine` |
| `src/lib/webauthn.ts` | Keep | Carry-over, byte-identical |
| `src/lib/ws.ts` | Modify | `pingIntervalMs` (30 s default), `lastMessageAt` |
| `src/lib/chat-socket.ts` | Modify | Carry-over + `pong` never reaches listeners |
| `src/lib/telemetry-socket.ts` | Keep | Carry-over, byte-identical |
| `src/lib/lifecycle.ts` | Create | `onVisible(fn): () => void` |
| `src/lib/audio.ts` | Keep (1b rewrites) | `playWavBase64` until Task 21 |
| `src/shell/ThemeProvider.tsx` | Create | `ThemeProvider`, `useTheme()` |
| `src/shell/ThemeToggle.tsx` | Create | 44×44 half-filled circle |
| `src/shell/presence.ts` | Create | `usePresence`, `useModalFocus`, `riseStyle`, `riseClass` |
| `src/shell/Layer.tsx` | Create | `Layer` (portal, rise in/out, reduced motion, focus + inert, `level`) |
| `src/shell/Sheet.tsx` | Create | Bottom sheet + scrim + explicit `Done` + Escape |
| `src/shell/ConnectionProvider.tsx` | Create | Socket singletons, `useConnection()`, `markTrue()` |
| `src/shell/QueryProvider.tsx` | Create | `QueryClient` defaults |
| `src/gates/Gate.tsx` | Create | kicker / title / body / children / footer layout |
| `src/gates/GateField.tsx` | Create | Static 220 px dot field |
| `src/gates/StepList.tsx` | Create | 48 px rows, 22 px ring, `progress` and `toggle` variants |
| `src/gates/SetupGate.tsx` | Create | The three first-run steps |
| `src/gates/SignInGate.tsx` | Create | Passkey sign-in |
| `src/gates/ExpiredGate.tsx` | Create | 401 |
| `src/gates/DeniedGate.tsx` | Create | 403 |
| `src/gates/AuthGate.tsx` | Create | Which gate, over what |
| `src/room/useOverview.ts` | Create | `["overview"]`, 30 s poll, `markTrue()` |
| `src/room/StatusLine.tsx` | Create | The mono 12 status line, all four variants |
| `src/room/Room.tsx` | Create (1b rewrites) | 1a: header + toggle + status line |
| `src/test/setup.ts` | Modify | `matchMedia`, `ResizeObserver`, `visualViewport` stubs |
| `src/test/fixtures.ts` | Create | `integrationsFixture`, `attentionFixture`, `overviewFixture` |
| `src/pages/`, `src/chat/`, `src/components/`, `src/assets/`, `src/App.css`, `src/fonts.d.ts`, `src/shell/{AlfredProvider,AppShell,CommandPalette,IconRail,TelemetryRail,TopBar}.tsx`, `src/lib/{notifications,utils}.ts`, `components.json` | Delete | The outgoing SPA |
| `src/lib/{presence-signal,headline,history,actions,slide,recorder}.ts`, `src/room/*`, `src/sheets/*`, `src/door/*` | 1b | Not this plan |

---

## The contract 1a hands to 1b

Plan 1b is written against these names. They are defined in the tasks below and must not be renamed, re-cased or re-ordered.

```ts
// src/lib/types.ts
export type Urgency = "informational" | "important" | "urgent";
export type Mood = "neutral" | "pleased" | "concerned" | "amused" | "serious";
export interface StreamSummary { length: number; last_id: string | null; last_ts: number | null; rate_5m: number }
export interface Overview { redis; cost; dnd; counts; streams; inference; reflex?; librarian? }
export interface StreamEntry { id: string; event: Record<string, unknown> }
export interface StreamPage { entries: StreamEntry[]; next_before: string | null }
export interface NotificationEvent { notification_id; title; body; urgency; source; timestamp; metadata? }
export interface AuthStatus { registered: boolean; authenticated: boolean }
export interface CredentialField { label; field_type; required; placeholder; default; help_text; transient }
export interface IntegrationInfo { name; category; kind?; schema; configured }
export interface AttentionDomain { domain: string; members: string[]; seen: string[] }
export interface PendingAction { request_id; tool_name; target_service; parameters; reason; source; timestamp; ttl_seconds; expires_at }
export interface ActionResultEvent { request_id; tool_name; status; result?; error?; timestamp? }
export type ChatServerMessage = …; export type TelemetryMessage = …;
```

| Kind | Names 1b may rely on |
|---|---|
| Modules | `@/lib/{types,format,theme,viewport,auth-events,api,auth,webauthn,ws,chat-socket,telemetry-socket,lifecycle}` |
| Shell | `ThemeProvider`/`useTheme`, `ThemeToggle`, `Layer`, `Sheet`, `presence.ts` (`usePresence`, `useModalFocus`, `riseStyle`, `riseClass`), `ConnectionProvider`/`useConnection`/`markTrue`, `QueryProvider` |
| `useConnection()` | `{ chat, telemetry, online, chatStatus, telemetryStatus, lastTrueAt, subscribeOnline }` — `subscribeOnline(fn)` runs `fn` on every chat-socket open, at once if it is open already, and returns the unsubscribe |
| Gates | `Gate`, `GateField`, `StepList`, `SetupGate`, `SignInGate`, `ExpiredGate`, `DeniedGate`, `AuthGate` |
| Room | `useOverview`, `StatusLine`, `Room` |
| Format | `hhmm(value)`, `dayMonth(date)`, `mmss(seconds)`, `usd(n)`, `evs(streams)`, `shortId(id)`, `humaniseTool(tool)`, `rawCall(tool, params)`, `dayLabel(date, now)` |
| CSS variables | `--bg --surface --field --line --keyboard --muted --fg2 --fg --accent --green --ink --paper --paper-muted --ring --scrim`, `--ease-rise --ease-settle`, `--app-height --keyboard-inset` |
| CSS classes | `.t-gate .t-headline .t-title .t-alfred .t-you .t-body .t-row .t-label .t-meta .t-monogram .t-fuse .t-status`, `.rise-in .rise-out .gate-field .pb-keyboard` |
| Keyframes | `rise`, `sink`, `fade`, `fade-out`, `breathe`, `wave`, `drift` |
| `localStorage` | `alfred.theme`, `alfred.device`, `alfred_session_id` (1b adds `alfred.unsent`) |
| Query keys | `["auth-status"] ["overview"] ["integrations"] ["attention"]` (1b adds `["room-history"] ["deferred"] ["pending-actions"]`) |

---

### Task 1: Clear the old client and swap the dependency tree

The outgoing SPA is desktop-shaped and its dependency tree (radix, shadcn, lucide, sonner, cmdk, react-markdown) exists to serve it. Deleting it first means every later task is written against an empty canvas rather than fighting a half-migrated one. What survives is exactly the four library modules the spec carries over plus `api.ts`, `audio.ts` and the test setup.

**Files:**
- Delete: `web/src/pages/`, `web/src/chat/`, `web/src/components/`, `web/src/assets/`, `web/src/App.css`, `web/src/fonts.d.ts`, `web/src/shell/AlfredProvider.tsx`, `web/src/shell/AppShell.tsx`, `web/src/shell/CommandPalette.tsx`, `web/src/shell/CommandPalette.test.tsx`, `web/src/shell/IconRail.tsx`, `web/src/shell/IconRail.test.tsx`, `web/src/shell/TelemetryRail.tsx`, `web/src/shell/TelemetryRail.test.tsx`, `web/src/shell/TopBar.tsx`, `web/src/lib/notifications.ts`, `web/src/lib/notifications.test.ts`, `web/src/lib/utils.ts`, `web/components.json`
- Modify: `web/package.json`, `web/package-lock.json`, `web/eslint.config.js`, `web/src/lib/types.ts`, `web/src/lib/format.ts`, `web/src/lib/format.test.ts`, `web/src/App.tsx`, `web/src/main.tsx`, `web/src/index.css`

- [ ] **Step 1: Delete the outgoing client**

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client/web
git rm -r -q src/pages src/chat src/components src/assets
git rm -q src/App.css src/fonts.d.ts components.json
git rm -q src/shell/AlfredProvider.tsx src/shell/AppShell.tsx src/shell/TopBar.tsx
git rm -q src/shell/CommandPalette.tsx src/shell/CommandPalette.test.tsx
git rm -q src/shell/IconRail.tsx src/shell/IconRail.test.tsx
git rm -q src/shell/TelemetryRail.tsx src/shell/TelemetryRail.test.tsx
git rm -q src/lib/notifications.ts src/lib/notifications.test.ts src/lib/utils.ts
git status --short
```

Expected: only `D` lines, and `src/` now contains `index.css`, `main.tsx`, `App.tsx`, `lib/`, `shell/` (empty), `test/`.

```bash
find src -type f | sort
```

Expected exactly:

```
src/App.tsx
src/index.css
src/lib/api.test.ts
src/lib/api.ts
src/lib/audio.ts
src/lib/chat-socket.test.ts
src/lib/chat-socket.ts
src/lib/format.test.ts
src/lib/format.ts
src/lib/telemetry-socket.ts
src/lib/types.ts
src/lib/webauthn.test.ts
src/lib/webauthn.ts
src/lib/ws.test.ts
src/lib/ws.ts
src/main.tsx
src/test/setup.ts
```

`web/public/` is untouched: `manifest.json`, `icon.svg`, `favicon.svg` and `icons.svg` stay as they are (decision 3).

- [ ] **Step 2: Rewrite `web/package.json`**

Complete file:

```json
{
  "name": "web",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "test": "vitest run --passWithNoTests",
    "preview": "vite preview"
  },
  "dependencies": {
    "@fontsource-variable/dm-sans": "^5.3.0",
    "@fontsource/geist-mono": "^5.3.0",
    "@tailwindcss/vite": "^4.3.0",
    "@tanstack/react-query": "^5.101.2",
    "react": "^19.2.6",
    "react-dom": "^19.2.6",
    "react-router-dom": "^7.17.0",
    "tailwindcss": "^4.3.3"
  },
  "devDependencies": {
    "@eslint/js": "^10.0.1",
    "@testing-library/jest-dom": "^6.9.1",
    "@testing-library/react": "^16.3.2",
    "@testing-library/user-event": "^14.6.1",
    "@types/node": "^24.12.3",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.1",
    "eslint": "^10.7.0",
    "eslint-plugin-react-hooks": "^7.1.1",
    "eslint-plugin-react-refresh": "^0.5.3",
    "globals": "^17.6.0",
    "jsdom": "^29.1.1",
    "tslib": "^2.8.1",
    "typescript": "~6.0.2",
    "typescript-eslint": "^8.59.2",
    "vite": "^8.0.12",
    "vitest": "^4.1.10"
  }
}
```

Removed: `radix-ui`, `lucide-react`, `sonner`, `react-markdown`, `cmdk`, `next-themes`, `tw-animate-css`, `class-variance-authority`, `clsx`, `tailwind-merge`, `@fontsource-variable/geist`, `@fontsource-variable/inter`, `@fontsource/jetbrains-mono`. Added: `@fontsource-variable/dm-sans`, `@fontsource/geist-mono`. Every dev dependency is unchanged.

- [ ] **Step 3: Install and prove the lockfile moved**

Run: `npm install`
Expected: npm prints a `removed N packages … audited N packages` summary (the counts drift with the registry) and rewrites `package-lock.json`. What must be true is checked next:

```bash
npm ls @fontsource-variable/dm-sans @fontsource/geist-mono
```

Expected: both resolve at `5.3.x`, no `UNMET DEPENDENCY`.

```bash
node -e "const d=require('./package-lock.json').packages[''].dependencies; const gone=['radix-ui','lucide-react','sonner','react-markdown','cmdk','next-themes','tw-animate-css','class-variance-authority','clsx','tailwind-merge','@fontsource-variable/geist','@fontsource-variable/inter','@fontsource/jetbrains-mono'].filter(n=>n in d); if(gone.length){console.error('still in the lockfile: '+gone.join(', '));process.exit(1)} console.log('root deps clean')"
```

Expected: `root deps clean`.

- [ ] **Step 4: Rewrite `web/src/lib/types.ts` to the contract**

Complete file:

```ts
/** Notification urgency, mirroring core/notifications/schema.py::Urgency. */
export type Urgency = "informational" | "important" | "urgent";

/** Response mood, mirroring bus/schemas/events.py::AlfredResponse.mood. */
export type Mood = "neutral" | "pleased" | "concerned" | "amused" | "serious";

export interface StreamSummary {
  length: number;
  last_id: string | null;
  last_ts: number | null;
  rate_5m: number;
}

/** GET /api/admin/overview. Every key is always present; `cost` may be null. */
export interface Overview {
  redis: { connected: boolean };
  cost: {
    date: string;
    spend_usd: number;
    cap_usd: number;
    alert_sent?: boolean;
    request_count?: number;
    avg_usd?: number;
  } | null;
  dnd: { active: boolean; until?: string | null; reason?: string | null; source?: string };
  counts: { sessions: number; devices: number; deferred: number; triggers: number };
  streams: Record<string, StreamSummary>;
  inference: { ollama: boolean; lmstudio: boolean };
  reflex?: { model: string | null; last_ms: number | null; p50_ms: number | null };
  librarian?: { last_run_at: string | null; reviewed: number | null; next_run_at: string | null };
}

export interface StreamEntry {
  id: string;
  event: Record<string, unknown>;
}

/** GET /api/admin/streams/{name}?count=&before= */
export interface StreamPage {
  entries: StreamEntry[];
  next_before: string | null;
}

/** A notification as it appears on the `notifications` stream and in the deferred queue. */
export interface NotificationEvent {
  notification_id: string;
  title: string;
  body: string;
  urgency: Urgency;
  source: string;
  timestamp: string;
  metadata?: Record<string, unknown>;
}

export interface AuthStatus {
  registered: boolean;
  authenticated: boolean;
}

export interface CredentialField {
  label: string;
  field_type: "text" | "password" | "url";
  required: boolean;
  placeholder: string;
  default: string;
  help_text: string;
  transient: boolean;
}

export interface IntegrationInfo {
  name: string;
  category: string;
  kind?: "adapter" | "service";
  schema: { fields: Record<string, CredentialField> };
  configured: Record<string, boolean>;
}

/** One domain of the Reflex attention set. `members` may act, `seen` is everything observed. */
export interface AttentionDomain {
  domain: string;
  members: string[];
  seen: string[];
}

/** GET /api/actions/pending and GET /api/actions/{request_id}. */
export interface PendingAction {
  request_id: string;
  tool_name: string;
  target_service: string;
  parameters: Record<string, unknown>;
  reason: string | null;
  source: string;
  timestamp: string;
  ttl_seconds: number;
  expires_at: string;
}

/** An entry on the `home_action_results` stream. */
export interface ActionResultEvent {
  request_id: string;
  tool_name: string;
  status: "success" | "error";
  result?: unknown;
  error?: string | null;
  timestamp?: string;
}

/** Chat WS server→client frames (`/ws`, protocol unchanged). */
export type ChatServerMessage =
  | { type: "session"; session_id: string }
  | { type: "transcription"; text: string; session_id: string }
  | {
      type: "response";
      text: string;
      audio?: string;
      session_id: string;
      actions_taken?: string[];
      mood?: Mood;
    }
  | {
      type: "notification";
      title: string;
      body: string;
      urgency: Urgency;
      notification_id: string;
      audio?: string;
      metadata?: Record<string, unknown>;
    }
  | { type: "error"; text: string; session_id?: string }
  | { type: "pong" };

/** Telemetry WS frames (`/ws/telemetry`). */
export type TelemetryMessage =
  | { type: "subscribed"; streams: string[] }
  | { type: "entry"; stream: string; id: string; event: Record<string, unknown> }
  | { type: "status"; detail: string }
  | { type: "pong" };
```

Everything the deleted pages needed (`EpisodicEntry`, `SemanticFile`, `Routine`, `Trigger`, `DeferredNotification`, `SessionInfo`, `DeviceInfo`) is gone. `mood` and `urgency` are now unions rather than `string`, which is what the Room's row renderers in 1b type against.

- [ ] **Step 5: Trim `web/src/lib/format.ts` and its test**

`categorize`, `CATEGORY_CLASS` and `SourceCategory` encode the retired seven-colour source palette; the new design colours rows by stream hue instead. `summarize` and `timeOf` read raw stream events and stay — Phase 2's Activity bench renders exactly those. Tasks 9, 10 and 13 add the new helpers to this file.

Complete `web/src/lib/format.ts`:

```ts
type Ev = Record<string, unknown>;

/** One-line summary of a raw stream event, for feed rows. */
export function summarize(stream: string, event: Ev): string {
  const type = String(event.event_type ?? "");
  if (type === "state_changed") return `${event.entity_id} → ${event.new_state}`;
  if (type === "action_request" || type === "action_result")
    return String(event.tool_name ?? type);
  if (type === "trigger_fired") return `${event.trigger_name} fired`;
  if (type === "user_request") return String(event.content ?? "").slice(0, 60);
  if (type === "alfred_response") return String(event.text ?? "").slice(0, 60);
  if (type === "reflex_observation") {
    const action = event.action as Ev | null | undefined;
    if (action?.tool_name != null) return String(action.tool_name);
    // Passive observation: the Reflex Engine saw this and acted on nothing.
    // Render the transition it saw, or these all read as one identical word.
    const trigger = event.trigger_event as Ev | null | undefined;
    const entity = String(trigger?.entity_id ?? "");
    if (!entity) return "observation";
    // `||` not `??`, matching the Python summary: an empty state reads as unknown.
    return `${entity}: ${trigger?.old_state || "unknown"} → ${trigger?.new_state || "unknown"}`;
  }
  if (stream === "notifications") return String(event.title ?? "notification");
  return type || "event";
}

/** `HH:MM:SS` from a Redis stream id (`<ms>-<seq>`). */
export function timeOf(streamId: string): string {
  const ms = Number(streamId.split("-")[0]);
  return new Date(ms).toLocaleTimeString("en-GB", { hour12: false });
}
```

Complete `web/src/lib/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { summarize, timeOf } from "./format";

describe("summarize", () => {
  it("summarizes state changes", () => {
    expect(
      summarize("events", { event_type: "state_changed", entity_id: "light.study", new_state: "off" }),
    ).toBe("light.study → off");
  });
  it("summarizes action requests", () => {
    expect(summarize("actions", { event_type: "action_request", tool_name: "dim_lights" })).toBe("dim_lights");
  });
  it("falls back to event_type", () => {
    expect(summarize("events", { event_type: "mystery" })).toBe("mystery");
  });
  it("summarizes an acted-on reflex observation as its tool", () => {
    expect(
      summarize("reflex_observations", {
        event_type: "reflex_observation",
        trigger_event: { entity_id: "light.study", old_state: "on", new_state: "off" },
        action: { tool_name: "dim_lights" },
        result: { status: "success" },
      }),
    ).toBe("dim_lights");
  });
  it("summarizes a passive observation as its state transition", () => {
    expect(
      summarize("reflex_observations", {
        event_type: "reflex_observation",
        trigger_event: { entity_id: "light.study", old_state: "on", new_state: "off" },
        action: null,
        result: null,
      }),
    ).toBe("light.study: on → off");
  });
  it("renders a first sighting's missing old_state as unknown", () => {
    expect(
      summarize("reflex_observations", {
        event_type: "reflex_observation",
        trigger_event: { entity_id: "sensor.hallway", new_state: "23.5" },
        action: null,
      }),
    ).toBe("sensor.hallway: unknown → 23.5");
  });
  it("falls back when trigger_event is missing entirely", () => {
    expect(summarize("reflex_observations", { event_type: "reflex_observation" })).toBe(
      "observation",
    );
  });
});

describe("timeOf", () => {
  it("formats a stream id as HH:MM:SS", () => {
    expect(timeOf("1718000000000-0")).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});
```

- [ ] **Step 6: Reduce `index.css`, `App.tsx` and `main.tsx` to a placeholder shell**

Complete `web/src/index.css` (Task 3 fills it in properly):

```css
@import "tailwindcss";
```

Complete `web/src/App.tsx`:

```tsx
export default function App() {
  return <main>Alfred</main>;
}
```

Complete `web/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

The font imports left with `fonts.d.ts`; Task 3 re-adds them in `index.css`, where the rest of the design system lives.

- [ ] **Step 7: Drop the eslint override for the deleted component library**

In `web/eslint.config.js`, delete the trailing block that names `src/components/ui/**` — that directory no longer exists. Everything else (the plugin set, the browser globals, `globalIgnores(['dist'])`) is unchanged. The file ends:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
])
```

- [ ] **Step 8: Everything green on the empty canvas**

Run: `npm run lint`
Expected: no output (exit 0).

Run: `npm test`
Expected: `Test Files  5 passed (5)` — `api.test.ts`, `chat-socket.test.ts`, `format.test.ts`, `webauthn.test.ts`, `ws.test.ts`. A different count means something was deleted that should not have been, or a deleted test file survived.

Run: `npm run build`
Expected: `tsc -b` silent, then vite prints `✓ built in …` and writes `dist/index.html` plus one hashed JS chunk. No `Could not resolve` errors — that would mean a deleted module is still imported.

- [ ] **Step 9: Commit**

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
git add -A web
git commit -m "chore(web): clear the old client for the PWA rewrite"
```

---

### Task 2: `index.html` and the test environment

Four of the twelve iOS constraints (spec §4.3, §4.4, §4.5, §4.12) are decided in the document head, before any React renders: `viewport-fit=cover` for the island and the home indicator, `interactive-widget=resizes-content` so the software keyboard shrinks the layout instead of covering it, one `theme-color` meta that `applyTheme()` (Task 3) rewrites so Safari's chrome follows the app's theme — the app's theme is stored-choice-else-hour, not the OS scheme, so per-scheme `media` variants would disagree with it, and the Apple standalone metas. They are easy to lose in a later edit, so a test greps the file.

The same task teaches jsdom the three APIs the client relies on and jsdom does not implement.

**Files:**
- Modify: `web/index.html`
- Modify: `web/src/test/setup.ts`
- Test: `web/src/test/index-html.test.ts` (new)
- Test: `web/src/test/setup.test.ts` (new)

- [ ] **Step 1: Write the failing head test**

Create `web/src/test/index-html.test.ts`:

```ts
import { describe, expect, it } from "vitest";
// Read the shipped file rather than a rendered DOM: these metas are never mounted
// by a test that renders components, and losing one is silent until a phone shows it.
// Vite's `?raw` import needs no Node types and works under the jsdom environment,
// where `import.meta.url` is an http: URL rather than a file.
import html from "../../index.html?raw";

describe("index.html", () => {
  it("declares the document language", () => {
    expect(html).toContain('<html lang="en">');
  });

  it("opts into the notch and the resizing keyboard", () => {
    const viewport = /<meta name="viewport" content="([^"]+)"/.exec(html)?.[1] ?? "";
    expect(viewport).toContain("width=device-width");
    expect(viewport).toContain("initial-scale=1");
    expect(viewport).toContain("viewport-fit=cover");
    expect(viewport).toContain("interactive-widget=resizes-content");
  });

  it("does not disable pinch zoom", () => {
    expect(html).not.toContain("user-scalable=no");
    expect(html).not.toContain("maximum-scale=1");
  });

  it("ships one theme-color, the dark first-paint token, for applyTheme() to rewrite", () => {
    // The theme is the app's (stored choice, else the hour), never the OS scheme, so
    // a per-scheme `media` pair would disagree with it for anyone whose phone is set
    // the other way. applyTheme() keeps this one meta in step with data-theme.
    expect(html).toContain('<meta name="theme-color" content="#25221F" />');
    expect(html).not.toContain("prefers-color-scheme");
  });

  it("stops iOS turning ids and times into phone links", () => {
    expect(html).toContain('<meta name="format-detection" content="telephone=no" />');
  });

  it("asks iOS for a standalone app with a translucent status bar", () => {
    expect(html).toContain('<meta name="apple-mobile-web-app-capable" content="yes" />');
    expect(html).toContain(
      '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />',
    );
    expect(html).toContain('<meta name="apple-mobile-web-app-title" content="Alfred" />');
  });

  it("keeps the manifest link untouched for phase 4", () => {
    expect(html).toContain('<link rel="manifest" href="/manifest.json" />');
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/test/index-html.test.ts`
Expected: FAIL — five of the seven assertions fail: the outgoing head has `content="width=device-width, initial-scale=1.0"` (no `viewport-fit`, no `interactive-widget`), no `theme-color` meta, no Apple metas, no `format-detection`, and `<html lang="en" style="background:#090c12">` fails the language assertion because of the inline style. The pinch-zoom and manifest assertions already pass.

- [ ] **Step 3: Rewrite `web/index.html`**

Complete file:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content" />
    <meta name="theme-color" content="#25221F" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
    <meta name="apple-mobile-web-app-title" content="Alfred" />
    <meta name="format-detection" content="telephone=no" />
    <link rel="icon" href="/icon.svg" />
    <link rel="manifest" href="/manifest.json" />
    <title>Alfred</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Notes: the `style="background:#090c12"` on `<html>` is gone — the background is a theme token now, applied by `ThemeProvider` in Task 3. `format-detection` stops iOS turning ids and times in the timeline into phone links. The viewport meta stays on one line so the grep in the test is exact. Fonts are self-hosted through Vite and need no `<link rel="preconnect">`.

- [ ] **Step 4: Run the head test**

Run: `npm test -- src/test/index-html.test.ts`
Expected: `Test Files  1 passed (1)`, 7 tests.

- [ ] **Step 5: Write the failing environment test**

Create `web/src/test/setup.test.ts`:

```ts
import { describe, expect, it } from "vitest";

describe("test environment", () => {
  it("answers matchMedia with a non-matching MediaQueryList", () => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    expect(mq.matches).toBe(false);
    expect(mq.media).toBe("(prefers-reduced-motion: reduce)");
    expect(() => mq.addEventListener("change", () => {})).not.toThrow();
  });

  it("provides a constructible ResizeObserver", () => {
    const observer = new ResizeObserver(() => {});
    expect(() => observer.observe(document.body)).not.toThrow();
    observer.disconnect();
  });

  it("provides a visualViewport that mirrors the window and takes listeners", () => {
    const viewport = window.visualViewport;
    expect(viewport).toBeTruthy();
    expect(viewport!.height).toBe(window.innerHeight);
    expect(viewport!.offsetTop).toBe(0);

    const seen: string[] = [];
    const onResize = () => seen.push("resize");
    viewport!.addEventListener("resize", onResize);
    viewport!.dispatchEvent(new Event("resize"));
    expect(seen).toEqual(["resize"]);
    viewport!.removeEventListener("resize", onResize);
  });

  it("registers the jest-dom matchers", () => {
    document.body.innerHTML = '<button type="button" disabled>x</button>';
    expect(document.querySelector("button")).toBeDisabled();
  });

  it("keeps a working localStorage on every supported Node", () => {
    localStorage.setItem("alfred.probe", "1");
    expect(localStorage.getItem("alfred.probe")).toBe("1");
    localStorage.removeItem("alfred.probe");
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npm test -- src/test/setup.test.ts`
Expected: FAIL — `window.matchMedia is not a function`, `ResizeObserver is not defined` and `expect(received).toBeTruthy()` on `window.visualViewport` (jsdom implements none of the three). The jest-dom matcher test passes already; the localStorage one passes already.

- [ ] **Step 7: Extend `web/src/test/setup.ts`**

Complete file:

```ts
import "@testing-library/jest-dom/vitest";

// Node 22 leaves localStorage to jsdom. Node >=26 defines its own `localStorage`
// global that stays `undefined` unless the process is started with
// --localstorage-file, and because vitest's jsdom environment shares one object
// with globalThis, that undefined own-property wins over jsdom's implementation.
// Modules reading localStorage at construction time then throw
// "Cannot read properties of undefined (reading 'getItem')".
//
// Install a minimal in-memory Storage when the global is missing so the suite
// behaves the same on both Node versions. No-op where jsdom's already works.
if (typeof globalThis.localStorage === "undefined") {
  const store = new Map<string, string>();
  const memoryStorage: Storage = {
    get length() {
      return store.size;
    },
    key: (index) => [...store.keys()][index] ?? null,
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => void store.set(key, String(value)),
    removeItem: (key) => void store.delete(key),
    clear: () => store.clear(),
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: memoryStorage,
    configurable: true,
    writable: true,
  });
}

// jsdom implements none of matchMedia, ResizeObserver or visualViewport. The first
// and last are load-bearing — the reduced-motion branch in Layer, and
// installViewportVars — and ResizeObserver is stubbed as a precaution: nothing in
// phase 1 constructs one, and a component that starts to should not begin by
// crashing every test. A test that needs a different answer overrides its own
// with vi.stubGlobal.
if (typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  });
}

if (typeof globalThis.ResizeObserver === "undefined") {
  class TestResizeObserver implements ResizeObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    writable: true,
    value: TestResizeObserver,
  });
}

// A real EventTarget, so installViewportVars() can add listeners and a test can
// dispatch `resize`/`scroll` at it. Defaults mirror the jsdom window, which means
// keyboardInset() is 0 until a test says otherwise.
class TestVisualViewport extends EventTarget {
  width = window.innerWidth;
  height = window.innerHeight;
  offsetTop = 0;
  offsetLeft = 0;
  pageTop = 0;
  pageLeft = 0;
  scale = 1;
}

if (!window.visualViewport) {
  Object.defineProperty(window, "visualViewport", {
    configurable: true,
    writable: true,
    value: new TestVisualViewport() as unknown as VisualViewport,
  });
}
```

- [ ] **Step 8: Run the environment test and the whole suite**

Run: `npm test -- src/test/setup.test.ts`
Expected: `Test Files  1 passed (1)`, 5 tests.

Run: `npm test`
Expected: `Test Files  7 passed (7)` — the five carried over plus the two new ones.

Run: `npm run lint && npm run build`
Expected: no eslint output; `tsc -b` silent; vite `✓ built in …`.

- [ ] **Step 9: Commit**

```bash
git add web/index.html web/src/test
git commit -m "feat(web): iOS-correct document head and a jsdom environment that matches the phone"
```

---

### Task 3: Tokens, fonts, type scale, motion — and the theme that switches them

The whole design system is fifteen custom properties per theme, twelve type-scale classes, two easing curves and five keyframes (Task 5 adds the leave's two keyframes and its mirrored curve). Putting all of it in `index.css` behind `:root[data-theme]` means the theme switch is one attribute write, with no React re-render of anything that only needs a colour.

Theme resolution: a stored choice always wins; with nothing stored, 07:00–18:59 is light and everything else is dark ("default dark after sunset, light in daytime").

**Files:**
- Create: `web/src/lib/theme.ts`, `web/src/lib/theme.test.ts`
- Create: `web/src/shell/ThemeProvider.tsx`, `web/src/shell/ThemeToggle.tsx`, `web/src/shell/ThemeProvider.test.tsx`
- Modify: `web/src/index.css`

- [ ] **Step 1: Write the failing theme test**

Create `web/src/lib/theme.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { applyTheme, rememberTheme, resolveInitialTheme, storedTheme, THEME_KEY } from "./theme";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("resolveInitialTheme", () => {
  it("resolves the stored theme whatever the hour", () => {
    expect(resolveInitialTheme(new Date(2026, 8, 7, 12, 0), "dark")).toBe("dark");
    expect(resolveInitialTheme(new Date(2026, 8, 7, 23, 0), "light")).toBe("light");
  });

  it("ignores a stored value that is not a theme", () => {
    expect(resolveInitialTheme(new Date(2026, 8, 7, 12, 0), "banana")).toBe("light");
    expect(resolveInitialTheme(new Date(2026, 8, 7, 23, 0), "")).toBe("dark");
  });

  it("is light from 07:00 to 18:59 and dark otherwise", () => {
    const at = (hour: number) => resolveInitialTheme(new Date(2026, 8, 7, hour, 30), null);
    expect(at(0)).toBe("dark");
    expect(at(6)).toBe("dark");
    expect(at(7)).toBe("light");
    expect(at(12)).toBe("light");
    expect(at(18)).toBe("light");
    expect(at(19)).toBe("dark");
    expect(at(23)).toBe("dark");
  });
});

describe("applyTheme", () => {
  it("writes data-theme on the document element, and nothing to storage", () => {
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it("tints Safari's chrome to match", () => {
    // index.html ships the meta; the test page does not, so make one.
    const meta = document.createElement("meta");
    meta.name = "theme-color";
    meta.content = "#25221F";
    document.head.append(meta);
    try {
      applyTheme("light");
      expect(meta.content).toBe("#F6F3EE");
      applyTheme("dark");
      expect(meta.content).toBe("#25221F");
    } finally {
      meta.remove();
    }
  });

  it("does not need the meta to exist", () => {
    expect(() => applyTheme("light")).not.toThrow();
  });
});

describe("rememberTheme", () => {
  it("uses the alfred.theme key", () => {
    expect(THEME_KEY).toBe("alfred.theme");
  });

  it("persists the choice", () => {
    rememberTheme("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("survives a storage that refuses to write", () => {
    vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => rememberTheme("light")).not.toThrow();
  });
});

describe("storedTheme", () => {
  it("is null before anything is stored", () => {
    expect(storedTheme()).toBeNull();
  });

  it("reads back what rememberTheme wrote", () => {
    rememberTheme("light");
    expect(storedTheme()).toBe("light");
  });

  it("is null when storage cannot be read", () => {
    vi.spyOn(localStorage, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(storedTheme()).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/lib/theme.test.ts`
Expected: FAIL — `Failed to resolve import "./theme"`. The module does not exist yet.

- [ ] **Step 3: Write `web/src/lib/theme.ts`**

Complete file:

```ts
export type Theme = "dark" | "light";

export const THEME_KEY = "alfred.theme";

/** Light between 07:00 and 18:59, dark otherwise — unless a choice was stored. */
export function resolveInitialTheme(now: Date, stored: string | null): Theme {
  if (stored === "dark" || stored === "light") return stored;
  const hour = now.getHours();
  return hour >= 7 && hour < 19 ? "light" : "dark";
}

/** `--bg` per theme, as index.css declares it. Safari's chrome reads it from the meta. */
const THEME_COLOR: Record<Theme, string> = { dark: "#25221F", light: "#F6F3EE" };

/** The one place the theme becomes visible: the attribute the palette hangs off. */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  // The theme is ours, not the OS's — a phone in system light mode runs dark here
  // at 21:00 — so the meta cannot carry a `prefers-color-scheme` pair; it follows.
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[theme]);
}

/**
 * Persist a choice. Only a choice: the time-of-day fallback is applied but never
 * remembered, or the first launch would freeze the theme for every launch after.
 */
export function rememberTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Private mode or a storage-quota refusal. The theme still applies for this
    // session; only the memory of it is lost.
  }
}

export function storedTheme(): string | null {
  try {
    return localStorage.getItem(THEME_KEY);
  } catch {
    return null;
  }
}
```

- [ ] **Step 4: Run the theme test**

Run: `npm test -- src/lib/theme.test.ts`
Expected: `Test Files  1 passed (1)`, 12 tests.

- [ ] **Step 5: Write the whole design system into `web/src/index.css`**

Complete file:

```css
@import "tailwindcss";
@import "@fontsource-variable/dm-sans/standard.css";
@import "@fontsource/geist-mono/400.css";
@import "@fontsource/geist-mono/500.css";

/* ------------------------------------------------------------------ palette
   Handoff, "Design tokens". Both blocks carry every token, so one selector is
   the whole theme and nothing leaks between them. Dark is also what :root gets
   before ThemeProvider has written data-theme — the first paint of a cold start
   is dark, which is the design's default. */
:root,
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #25221F;
  --surface: #2F2B27;
  --field: #2F2B27;
  --line: #35312C;
  --keyboard: #1C1A17;
  --muted: #9A9186;
  --fg2: #D9D2C8;
  --fg: #F1ECE4;
  --accent: oklch(0.78 0.12 45);
  --green: oklch(0.70 0.13 150);
  --ink: #F1ECE4;
  --paper: #25221F;
  --paper-muted: #7D756B;
  --ring: #D6D0C7;
  --scrim: rgba(20, 17, 14, 0.45);
}

:root[data-theme="light"] {
  color-scheme: light;
  --bg: #F6F3EE;
  --surface: #EFEAE2;
  --field: #FFFFFF;
  --line: #E6E1D9;
  --keyboard: #E6E1D9;
  --muted: #8A8177;
  --fg2: #4D4740;
  --fg: #221F1B;
  --accent: oklch(0.72 0.13 45);
  --green: oklch(0.70 0.13 150);
  --ink: #221F1B;
  --paper: #F1ECE4;
  --paper-muted: #9A9186;
  --ring: #3D3832;
  --scrim: rgba(20, 17, 14, 0.45);
}

/* ---------------------------------------------------- theme-independent bits
   The two easing curves, the font stacks, and the two viewport variables
   installViewportVars() overwrites on the same element. The stream hues
   (h = 30 + 45·i in the handoff) are not tokens yet: phase 1 paints three of
   them, as literal `oklch(0.62 0.11 <h>)` values in ActRow and HeldBackSheet,
   and the Workshop's rings will want all eight. */
:root {
  --ease-rise: cubic-bezier(0.2, 0.8, 0.2, 1);
  --ease-settle: cubic-bezier(0.2, 1.4, 0.3, 1);

  --font-sans: "DM Sans Variable", -apple-system, Helvetica, sans-serif;
  --font-mono: "Geist Mono", ui-monospace, SFMono-Regular, monospace;

  --app-height: 100dvh;
  --keyboard-inset: 0px;
}

/* Tailwind utilities that point at the tokens rather than copying them, so a
   theme switch moves every utility with it. */
@theme inline {
  --color-bg: var(--bg);
  --color-surface: var(--surface);
  --color-field: var(--field);
  --color-line: var(--line);
  --color-keyboard: var(--keyboard);
  --color-muted: var(--muted);
  --color-fg2: var(--fg2);
  --color-fg: var(--fg);
  --color-accent: var(--accent);
  --color-green: var(--green);
  --color-ink: var(--ink);
  --color-paper: var(--paper);
  --color-paper-muted: var(--paper-muted);
  --color-ring: var(--ring);
  --color-scrim: var(--scrim);

  --font-sans: var(--font-sans);
  --font-mono: var(--font-mono);

  --ease-rise: var(--ease-rise);
  --ease-settle: var(--ease-settle);
}

@layer base {
  html,
  body {
    height: 100%;
    /* Constraint §4.6: a standalone app must not rubber-band like a web page.
       The scroll containers inside the shell keep their own overscroll, so a
       timeline can still bounce at its ends without moving the page. */
    overscroll-behavior: none;
  }

  body {
    margin: 0;
    background: var(--bg);
    color: var(--fg);
    font-family: var(--font-sans);
    -webkit-font-smoothing: antialiased;
  }

  /* Constraint §4.4: 100vh is wrong in Safari. --app-height is the innerHeight
     mirror; the fallback keeps the shell full-height before the JS has run. One
     declaration, not two — an undefined var() is invalid at computed-value time
     and would fall all the way back to `auto`, not to a previous declaration. */
  #root {
    display: flex;
    flex-direction: column;
    min-height: var(--app-height, 100dvh);
  }

  button {
    font: inherit;
    color: inherit;
    cursor: pointer;
  }

  /* No scrollbars anywhere: iOS hides them anyway, and the app looks like iOS. */
  ::-webkit-scrollbar {
    display: none;
  }
}

@layer components {
  /* ---------------------------------------------------------- the type scale
     DM Sans for anything a person says or reads, Geist Mono for anything the
     machine says about itself. Alfred's voice is one size above yours. */
  .t-gate {
    font-size: 30px;
    line-height: 1.15;
    font-weight: 500;
    letter-spacing: -0.02em;
  }
  .t-headline {
    font-size: 26px;
    line-height: 1.15;
    font-weight: 500;
    letter-spacing: -0.02em;
  }
  .t-title {
    font-size: 20px;
    line-height: 1.2;
    font-weight: 500;
    letter-spacing: -0.02em;
  }
  .t-alfred {
    font-size: 19px;
    line-height: 1.4;
    font-weight: 400;
    letter-spacing: -0.01em;
  }
  .t-you {
    font-size: 15px;
    line-height: 1.4;
    font-weight: 400;
  }
  .t-body {
    font-size: 15px;
    line-height: 1.5;
    font-weight: 400;
  }
  .t-row {
    font-size: 14.5px;
    line-height: 1.35;
    font-weight: 400;
  }
  .t-label {
    font-size: 11px;
    line-height: 1;
    font-weight: 500;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .t-meta {
    font-family: var(--font-mono);
    font-size: 11px;
    line-height: 1.5;
    font-weight: 400;
    color: var(--muted);
  }
  .t-monogram {
    font-family: var(--font-mono);
    font-size: 9px;
    line-height: 22px;
    font-weight: 500;
  }
  .t-fuse {
    font-family: var(--font-mono);
    font-size: 40px;
    line-height: 1;
    font-weight: 500;
    letter-spacing: -0.02em;
  }
  .t-status {
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.5;
    color: var(--muted);
  }

  /* --------------------------------------------------------------- motion
     Everything that enters does so from the bottom. --layer-duration is set by
     the component (sheets 380, workshop 400, door 420). Nothing fades in, except
     under reduce-motion, below. */
  .rise-in {
    animation: rise var(--layer-duration, 400ms) var(--ease-rise) both;
  }
  .rise-out {
    animation: rise var(--layer-duration, 400ms) var(--ease-rise) reverse both;
  }

  /* The gates' static presence field: a dot grid that drifts a pixel and back. */
  .gate-field {
    background-image: radial-gradient(
      circle,
      color-mix(in oklab, var(--accent) 55%, transparent) 1px,
      transparent 1.6px
    );
    background-size: 12px 12px;
    -webkit-mask-image: linear-gradient(to bottom, rgba(0, 0, 0, 0.9) 25%, transparent 100%);
    mask-image: linear-gradient(to bottom, rgba(0, 0, 0, 0.9) 25%, transparent 100%);
    animation: drift 6s ease-in-out infinite;
  }
  :root[data-theme="light"] .gate-field {
    background-image: radial-gradient(
      circle,
      color-mix(in oklab, var(--accent) 70%, transparent) 1px,
      transparent 1.6px
    );
  }
}

@keyframes rise {
  from {
    transform: translateY(100%);
  }
  to {
    transform: translateY(0);
  }
}

/* Only used as the reduce-motion substitute for `rise`. */
@keyframes fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@keyframes breathe {
  0%,
  100% {
    opacity: 0.55;
  }
  50% {
    opacity: 1;
  }
}

@keyframes wave {
  0%,
  100% {
    transform: scaleY(0.4);
  }
  50% {
    transform: scaleY(1);
  }
}

@keyframes drift {
  0% {
    background-position: 0 0;
  }
  50% {
    background-position: 2px 1px;
  }
  100% {
    background-position: 0 0;
  }
}

/* Handoff: "reduce-motion: presence field static, rise becomes a 200 ms opacity
   step". The blanket rule stops every other animation and transition; the two
   class rules that follow it win on specificity and give `rise` its opacity
   step. The canvas presence field checks the same query in JS (plan 1b). */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }

  .rise-in {
    animation: fade 200ms linear both !important;
  }
  .rise-out {
    animation: fade 200ms linear reverse both !important;
  }
}
```

- [ ] **Step 6: Write the failing provider test**

Create `web/src/shell/ThemeProvider.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY } from "@/lib/theme";
import { ThemeProvider, useTheme } from "./ThemeProvider";
import { ThemeToggle } from "./ThemeToggle";

function Probe() {
  const { theme } = useTheme();
  return <span data-testid="theme">{theme}</span>;
}

afterEach(() => {
  vi.useRealTimers();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("ThemeProvider", () => {
  it("applies the stored theme to the document on mount", () => {
    localStorage.setItem(THEME_KEY, "light");
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("falls back to the time of day when nothing is stored, and does not remember it", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 8, 7, 23, 0));
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    // Remembering the fallback would make tonight's dark tomorrow's noon.
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it("is light by day", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 8, 7, 12, 0));
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("toggles, re-applies and persists", async () => {
    const user = userEvent.setup();
    localStorage.setItem(THEME_KEY, "dark");
    render(
      <ThemeProvider>
        <ThemeToggle />
        <Probe />
      </ThemeProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Switch theme" }));

    expect(screen.getByTestId("theme")).toHaveTextContent("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("refuses to be used outside the provider", () => {
    expect(() => render(<Probe />)).toThrow("useTheme outside ThemeProvider");
  });
});

describe("ThemeToggle", () => {
  it("is a 44x44 button labelled for screen readers", () => {
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );
    const button = screen.getByRole("button", { name: "Switch theme" });
    expect(button).toHaveClass("h-11", "w-11");
  });
});
```

- [ ] **Step 7: Run it to verify it fails**

Run: `npm test -- src/shell/ThemeProvider.test.tsx`
Expected: FAIL — `Failed to resolve import "./ThemeProvider"`.

- [ ] **Step 8: Write the provider and the toggle**

Complete `web/src/shell/ThemeProvider.tsx`:

```tsx
import { createContext, useContext, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { applyTheme, rememberTheme, resolveInitialTheme, storedTheme, type Theme } from "@/lib/theme";

export interface ThemeValue {
  theme: Theme;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Resolved once, at mount: the time of day must not flip the theme under the
  // user while the app is open.
  const [theme, setTheme] = useState<Theme>(() => resolveInitialTheme(new Date(), storedTheme()));

  // A layout effect, so the attribute lands before the first paint: :root is dark
  // until it is written, and a stored light theme must not flash dark on launch.
  useLayoutEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const value = useMemo<ThemeValue>(
    () => ({
      theme,
      toggle: () => {
        // Persisted here and not in the effect: only a choice is remembered, never
        // the time-of-day fallback, or the first launch would fix the theme for good.
        const next = theme === "dark" ? "light" : "dark";
        rememberTheme(next);
        setTheme(next);
      },
    }),
    [theme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme(): ThemeValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme outside ThemeProvider");
  return ctx;
}
```

Complete `web/src/shell/ThemeToggle.tsx`:

```tsx
import { useTheme } from "@/shell/ThemeProvider";

/**
 * Top-right of the Room header. 44x44 hit area, pulled into the header's
 * optical alignment with negative margins, around a 14 px half-filled circle.
 */
export function ThemeToggle() {
  const { toggle } = useTheme();
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Switch theme"
      className="-mt-2 -mr-2.5 flex h-11 w-11 shrink-0 items-center justify-center border-0 bg-transparent text-muted"
    >
      <span
        aria-hidden="true"
        className="block h-3.5 w-3.5 rounded-full border-[1.5px] border-current"
        style={{ background: "linear-gradient(90deg, currentColor 50%, transparent 50%)" }}
      />
    </button>
  );
}
```

- [ ] **Step 9: Run the provider test and the suite**

Run: `npm test -- src/shell/ThemeProvider.test.tsx`
Expected: `Test Files  1 passed (1)`, 6 tests.

Run: `npm test`
Expected: `Test Files  9 passed (9)`.

- [ ] **Step 10: Prove the fonts are actually bundled**

Run: `npm run build`
Expected: `✓ built in …`.

```bash
ls dist/assets/*.woff2 | head -3
grep -o "DM Sans Variable" dist/assets/*.css | head -1
grep -o "Geist Mono" dist/assets/*.css | head -1
```

Expected: at least one `.woff2` in `dist/assets/`, and both family names present in the emitted CSS. An empty result means the `@import` did not resolve — check the package paths in `index.css` before going further, because every later task inherits it.

- [ ] **Step 11: Commit**

```bash
git add web/src/index.css web/src/lib/theme.ts web/src/lib/theme.test.ts web/src/shell
git commit -m "feat(web): design tokens, type scale, motion and the theme switch"
```

---

### Task 4: The viewport and the software keyboard

Constraints §4.4 and §4.5. Safari's `100vh` includes chrome that is not there, and the software keyboard covers fixed elements instead of resizing the page. `window.innerHeight` is the honest layout height — it follows Safari's toolbars — and `visualViewport` is the only source for the keyboard: `innerHeight − height − offsetTop` is how much of the window it has eaten. `installViewportVars()` mirrors both into custom properties so CSS can use them without a re-render, and `useKeyboardOpen()` gives the composer (plan 1b) a boolean. `--app-height` deliberately never shrinks for the keyboard: `.pb-keyboard` pays for it, once. (WebKit does not implement `interactive-widget`, so on iOS the layout viewport never resizes for the keyboard; on browsers that do, the inset is 0 and the layout viewport has already paid.)

**Files:**
- Create: `web/src/lib/viewport.ts`, `web/src/lib/viewport.test.ts`
- Modify: `web/src/index.css`

- [ ] **Step 1: Write the failing viewport test**

Create `web/src/lib/viewport.test.ts`:

```ts
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installViewportVars, KEYBOARD_OPEN_PX, keyboardInset, useKeyboardOpen } from "./viewport";

/** A visualViewport a test can drive: a real EventTarget with settable geometry. */
class FakeVisualViewport extends EventTarget {
  height: number;
  offsetTop: number;

  constructor(height: number, offsetTop = 0) {
    super();
    this.height = height;
    this.offsetTop = offsetTop;
  }

  resizeTo(height: number): void {
    this.height = height;
    this.dispatchEvent(new Event("resize"));
  }

  scrollTo(offsetTop: number): void {
    this.offsetTop = offsetTop;
    this.dispatchEvent(new Event("scroll"));
  }
}

const original = window.visualViewport;

function install(viewport: FakeVisualViewport | null): void {
  Object.defineProperty(window, "visualViewport", {
    configurable: true,
    writable: true,
    value: viewport,
  });
}

function setInnerHeight(value: number): void {
  Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value });
}

function resizeWindowTo(value: number): void {
  setInnerHeight(value);
  window.dispatchEvent(new Event("resize"));
}

const appHeight = () => document.documentElement.style.getPropertyValue("--app-height");
const keyboard = () => document.documentElement.style.getPropertyValue("--keyboard-inset");

beforeEach(() => setInnerHeight(852));

afterEach(() => {
  install(original as FakeVisualViewport | null);
  document.documentElement.style.removeProperty("--app-height");
  document.documentElement.style.removeProperty("--keyboard-inset");
});

describe("installViewportVars", () => {
  it("mirrors the window onto the document element", () => {
    install(new FakeVisualViewport(852));
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("852px");
    expect(keyboard()).toBe("0px");

    uninstall();
  });

  it("reports the keyboard inset when the viewport shrinks, and keeps the column", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);
    const uninstall = installViewportVars();

    viewport.resizeTo(500);

    // .pb-keyboard pays for the keyboard; a shorter column would pay for it twice.
    expect(keyboard()).toBe("352px");
    expect(appHeight()).toBe("852px");

    uninstall();
  });

  it("follows the viewport when it is scrolled under the keyboard", () => {
    const viewport = new FakeVisualViewport(500);
    install(viewport);
    const uninstall = installViewportVars();

    viewport.scrollTo(60);

    // 852 - 500 - 60: the offset is part of what is hidden.
    expect(keyboard()).toBe("292px");
    expect(appHeight()).toBe("852px");

    uninstall();
  });

  it("rounds the inset to whole pixels", () => {
    const viewport = new FakeVisualViewport(500.4);
    install(viewport);
    const uninstall = installViewportVars();

    // 852 - 500.4 = 351.6
    expect(keyboard()).toBe("352px");

    uninstall();
  });

  it("follows window resizes, with or without a visual viewport", () => {
    install(null);
    let uninstall = installViewportVars();

    resizeWindowTo(400);

    expect(appHeight()).toBe("400px");
    expect(keyboard()).toBe("0px");

    uninstall();
    install(new FakeVisualViewport(300));
    uninstall = installViewportVars();

    resizeWindowTo(300);

    expect(appHeight()).toBe("300px");
    expect(keyboard()).toBe("0px");

    uninstall();
  });

  it("stops updating once uninstalled", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);
    const uninstall = installViewportVars();
    uninstall();

    viewport.resizeTo(500);
    expect(keyboard()).toBe("0px");

    viewport.scrollTo(60);
    expect(keyboard()).toBe("0px");

    resizeWindowTo(400);
    expect(appHeight()).toBe("852px");
  });

  it("reports no keyboard where there is no visual viewport", () => {
    install(null);
    const uninstall = installViewportVars();

    expect(appHeight()).toBe("852px");
    expect(keyboard()).toBe("0px");
    expect(keyboardInset()).toBe(0);

    uninstall();
  });

  it("never reports a negative inset", () => {
    // iOS briefly reports a viewport taller than the window during rotation.
    install(new FakeVisualViewport(900));
    const uninstall = installViewportVars();

    expect(keyboard()).toBe("0px");
    expect(appHeight()).toBe("852px");

    uninstall();
  });
});

describe("useKeyboardOpen", () => {
  it("flips as the inset crosses the threshold, not at any inset at all", () => {
    const viewport = new FakeVisualViewport(852);
    install(viewport);

    const { result } = renderHook(() => useKeyboardOpen());
    expect(result.current).toBe(false);

    act(() => viewport.resizeTo(852 - KEYBOARD_OPEN_PX)); // inset exactly at the line — not open
    expect(result.current).toBe(false);

    act(() => viewport.resizeTo(852 - KEYBOARD_OPEN_PX - 1)); // one past it — open
    expect(result.current).toBe(true);

    act(() => viewport.scrollTo(1)); // the scroll takes it back to the line
    expect(result.current).toBe(false);

    act(() => viewport.resizeTo(852));
    expect(result.current).toBe(false);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/lib/viewport.test.ts`
Expected: FAIL — `Failed to resolve import "./viewport"`.

- [ ] **Step 3: Write `web/src/lib/viewport.ts`**

Complete file:

```ts
import { useSyncExternalStore } from "react";

/** Below this, the inset is Safari's toolbar or a rotation artefact, not a keyboard. */
export const KEYBOARD_OPEN_PX = 80;

/**
 * How much of the window is hidden below the visual viewport — the software
 * keyboard, in practice. 0 where `visualViewport` is unavailable, because a
 * guess here would move the composer for no reason.
 */
export function keyboardInset(): number {
  const viewport = window.visualViewport;
  if (!viewport) return 0;
  return Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
}

/**
 * Call `fn` whenever the geometry may have changed. `scroll` matters as much as
 * `resize`: iOS scrolls the visual viewport under a focused field rather than
 * resizing it again, and offsetTop is part of the inset. Returns the unsubscriber.
 */
function subscribe(fn: () => void): () => void {
  const viewport = window.visualViewport;
  viewport?.addEventListener("resize", fn);
  viewport?.addEventListener("scroll", fn);
  window.addEventListener("resize", fn);
  return () => {
    viewport?.removeEventListener("resize", fn);
    viewport?.removeEventListener("scroll", fn);
    window.removeEventListener("resize", fn);
  };
}

/**
 * Mirror the window into `--app-height` and the keyboard into `--keyboard-inset`
 * on the document element. Returns the uninstaller; `main.tsx` calls this once
 * and never uninstalls, tests always do.
 */
export function installViewportVars(): () => void {
  const root = document.documentElement;

  const apply = () => {
    // innerHeight, not visualViewport.height: the column must not shrink for the
    // keyboard, because .pb-keyboard already pays for it and the composer would
    // rise twice. innerHeight follows Safari's toolbars (100vh does not), and on
    // browsers that honour `interactive-widget` it follows the keyboard too — in
    // which case the inset below is 0, and nothing is paid twice either.
    root.style.setProperty("--app-height", `${Math.round(window.innerHeight)}px`);
    root.style.setProperty("--keyboard-inset", `${Math.round(keyboardInset())}px`);
  };

  apply();
  return subscribe(apply);
}

const isKeyboardOpen = () => keyboardInset() > KEYBOARD_OPEN_PX;

/** True while the software keyboard is up. Drives the composer's padding. */
export function useKeyboardOpen(): boolean {
  return useSyncExternalStore(subscribe, isKeyboardOpen);
}
```

- [ ] **Step 4: Run the viewport test**

Run: `npm test -- src/lib/viewport.test.ts`
Expected: `Test Files  1 passed (1)`, 9 tests.

- [ ] **Step 5: Add the keyboard padding class to `index.css`**

In `web/src/index.css`, inside the `@layer components` block, insert this after the `:root[data-theme="light"] .gate-field { … }` rule and before the block's closing brace:

```css
  /* ---------------------------------------------------- keyboard + home indicator
     Constraint §4.3 (home indicator) and §4.5 (the keyboard covers fixed
     elements). --keyboard-inset is written by installViewportVars() and is 0px
     whenever the keyboard is down, so .pb-keyboard is safe to apply always.
     A Tailwind padding utility on the same element would out-rank this rule
     outright — `utilities` is a later layer than `components` — and silently drop
     the inset, so the element that carries .pb-keyboard carries no other padding. */
  .pb-keyboard {
    padding-bottom: calc(var(--keyboard-inset, 0px) + env(safe-area-inset-bottom, 0px));
  }
```

Also correct the `#root` comment Task 3 wrote, a few rules above: `--app-height is the visualViewport` → `--app-height is the innerHeight` (the line break after it stays where it is).

- [ ] **Step 6: Full suite and build**

Run: `npm test`
Expected: `Test Files  10 passed (10)`.

Run: `npm run lint && npm run build`
Expected: no eslint output, `tsc -b` silent, `✓ built in …`.

```bash
grep -o "safe-area-inset" dist/assets/*.css | wc -l
```

Expected: `1` or more — the class survived Tailwind's build (it is plain CSS in a layer, not a utility, so it is never purged). (`grep -o … | wc -l` counts occurrences; `grep -c` would count lines, and the minified bundle is one line.)

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/viewport.ts web/src/lib/viewport.test.ts web/src/index.css
git commit -m "feat(web): mirror the visual viewport into --app-height and --keyboard-inset"
```

---

### Task 5: `Layer` and `Sheet` — everything that rises

Two primitives carry every full-screen and bottom-anchored surface in the design: gates and the Door use `Layer` (380/400/420 ms rise, ink/paper left to the caller), the Held-back sheet uses `Sheet` (scrim, grab bar, explicit `Done`). Both must stay mounted for the length of their leave animation and then disappear, and both must collapse to a 200 ms opacity step under reduce-motion.

Both are modal, so both behave like it: rendered into `<body>` through a portal, they take focus while they are up, make everything behind them `inert` — the app in `#root` and any surface already up — and give focus back to where it was once they have left. The shared pieces — `usePresence`, `useModalFocus`, `riseStyle`/`riseClass` — live in `presence.ts`, a plain module, so the component files export only components. `Layer` takes a `level`: the handoff stacks sheet (`z-20`) < Door and workshop (`z-30`) < gate (`z-40`), so a lapsed session paints over an open Door whatever order they were mounted in.

Standalone mode has no browser chrome (constraint §4.12), so every one of these has its own dismiss: the sheet's scrim, `Done` and the Escape key, the gates' own buttons.

**Files:**
- Create: `web/src/shell/presence.ts`, `web/src/shell/Layer.tsx`, `web/src/shell/Sheet.tsx`, `web/src/shell/Layer.test.tsx`
- Modify: `web/src/index.css` (an `--ease-sink` token, the `.rise-out` rule, a `sink` and a `fade-out` keyframes)

- [ ] **Step 1: Write the failing test**

Create `web/src/shell/Layer.test.tsx`:

```tsx
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Layer } from "./Layer";
import { Sheet } from "./Sheet";

/** Only the reduce-motion query answers `matches`; everything else stays false. */
function stubReducedMotion(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: matches && query === "(prefers-reduced-motion: reduce)",
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

/** The app column a real page has, so the surfaces have something to make inert. */
function mountApp(): HTMLButtonElement {
  const root = document.createElement("div");
  root.id = "root";
  const button = document.createElement("button");
  button.textContent = "Talk";
  root.append(button);
  document.body.append(root);
  return button;
}

beforeEach(() => {
  vi.useFakeTimers();
  stubReducedMotion(false);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.getElementById("root")?.remove();
});

describe("Layer", () => {
  it("renders nothing while closed", () => {
    render(
      <Layer open={false} label="Door">
        <p>hidden</p>
      </Layer>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("rises as a labelled modal with the caller's duration", () => {
    render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    const dialog = screen.getByRole("dialog", { name: "Door" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog.className).toContain("rise-in");
    expect(dialog.className).toContain("z-30");
    expect(dialog.style.getPropertyValue("--layer-duration")).toBe("420ms");
    expect(screen.getByText("visible")).toBeInTheDocument();
  });

  it("stacks gates above layers", () => {
    render(
      <Layer open label="Session lapsed" level="gate">
        <p>gate</p>
      </Layer>,
    );
    expect(screen.getByRole("dialog").className).toContain("z-40");
  });

  it("stays mounted for the leave animation, then unmounts", () => {
    const { rerender } = render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    rerender(
      <Layer open={false} label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    expect(screen.getByRole("dialog").className).toContain("rise-out");
    act(() => vi.advanceTimersByTime(419));
    expect(screen.queryByRole("dialog")).not.toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("leaves in 200 ms under reduce-motion", () => {
    stubReducedMotion(true);
    const { rerender } = render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    rerender(
      <Layer open={false} label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    act(() => vi.advanceTimersByTime(200));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("re-opening mid-leave cancels the unmount", () => {
    const { rerender } = render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    rerender(
      <Layer open={false} label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    act(() => vi.advanceTimersByTime(200));
    rerender(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    act(() => vi.advanceTimersByTime(1000));
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("rise-in");
    expect(dialog.className).not.toContain("rise-out");
  });

  it("clears the leave timer when unmounted mid-leave", () => {
    const { rerender, unmount } = render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    // React keeps a scheduler timer of its own; count the leave timer on top of it.
    const idle = vi.getTimerCount();
    rerender(
      <Layer open={false} label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    expect(vi.getTimerCount()).toBe(idle + 1);
    unmount();
    expect(vi.getTimerCount()).toBe(idle);
  });

  it("takes focus, makes the app inert, and gives both back when it has left", () => {
    const talk = mountApp();
    talk.focus();
    const { rerender } = render(
      <Layer open label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    expect(document.activeElement).toBe(screen.getByRole("dialog"));
    expect(document.getElementById("root")).toHaveAttribute("inert");

    rerender(
      <Layer open={false} label="Door" durationMs={420}>
        <p>visible</p>
      </Layer>,
    );
    // Still inert while it sinks: nothing behind it is reachable mid-leave.
    expect(document.getElementById("root")).toHaveAttribute("inert");
    act(() => vi.advanceTimersByTime(420));
    expect(document.getElementById("root")).not.toHaveAttribute("inert");
    expect(document.activeElement).toBe(talk);
  });

  it("keeps the app inert until the last surface has left", () => {
    mountApp();
    const { rerender } = render(
      <>
        <Sheet open title="Held back" onClose={() => {}}>
          <p>sheet</p>
        </Sheet>
        <Layer open label="Door" durationMs={420}>
          <p>door</p>
        </Layer>
      </>,
    );
    rerender(
      <>
        <Sheet open title="Held back" onClose={() => {}}>
          <p>sheet</p>
        </Sheet>
        <Layer open={false} label="Door" durationMs={420}>
          <p>door</p>
        </Layer>
      </>,
    );
    act(() => vi.advanceTimersByTime(420));
    expect(screen.queryByRole("dialog", { name: "Door" })).toBeNull();
    expect(document.getElementById("root")).toHaveAttribute("inert");
  });

  it("a gate over a sheet makes the sheet inert, swallows Escape, and keeps focus", () => {
    mountApp().focus();
    const onClose = vi.fn();
    const stacked = (sheetOpen: boolean) => (
      <>
        <Sheet open={sheetOpen} title="Held back" onClose={onClose}>
          <p>sheet</p>
        </Sheet>
        <Layer open label="Session lapsed" level="gate">
          <button type="button">Sign in</button>
        </Layer>
      </>
    );
    const { rerender } = render(stacked(true));
    const gate = screen.getByRole("dialog", { name: "Session lapsed" });
    expect(document.activeElement).toBe(gate);
    expect(screen.getByRole("dialog", { name: "Held back" })).toHaveAttribute("inert");

    fireEvent.keyDown(gate, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();

    // The sheet leaves underneath: focus stays in the gate, the app stays inert.
    rerender(stacked(false));
    act(() => vi.advanceTimersByTime(380));
    expect(screen.queryByRole("dialog", { name: "Held back" })).toBeNull();
    expect(document.activeElement).toBe(gate);
    expect(document.getElementById("root")).toHaveAttribute("inert");
  });

  it("gives the sheet back when the gate over it leaves", () => {
    mountApp();
    const stacked = (gateOpen: boolean) => (
      <>
        <Sheet open title="Held back" onClose={() => {}}>
          <p>sheet</p>
        </Sheet>
        <Layer open={gateOpen} label="Session lapsed" level="gate">
          <button type="button">Sign in</button>
        </Layer>
      </>
    );
    const { rerender } = render(stacked(true));
    const sheet = screen.getByRole("dialog", { name: "Held back" });
    rerender(stacked(false));
    // Still inert while the gate sinks.
    expect(sheet).toHaveAttribute("inert");
    act(() => vi.advanceTimersByTime(400));
    expect(sheet).not.toHaveAttribute("inert");
    expect(document.activeElement).toBe(sheet);
    expect(document.getElementById("root")).toHaveAttribute("inert");
  });
});

describe("Sheet", () => {
  it("shows the title, the children and a Done button", () => {
    render(
      <Sheet open title="Held back" onClose={() => {}}>
        <p>three things</p>
      </Sheet>,
    );
    const dialog = screen.getByRole("dialog", { name: "Held back" });
    expect(dialog.className).toContain("z-20");
    expect(screen.getByText("three things")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  it("closes on Done, on the scrim and on Escape from inside it", () => {
    const onClose = vi.fn();
    render(
      <Sheet open title="Held back" onClose={onClose}>
        <p>three things</p>
      </Sheet>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.keyDown(screen.getByRole("button", { name: "Done" }), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it("unmounts 380 ms after closing", () => {
    const { rerender } = render(
      <Sheet open title="Held back" onClose={() => {}}>
        <p>three things</p>
      </Sheet>,
    );
    rerender(
      <Sheet open={false} title="Held back" onClose={() => {}}>
        <p>three things</p>
      </Sheet>,
    );
    act(() => vi.advanceTimersByTime(379));
    expect(screen.queryByRole("dialog")).not.toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/shell/Layer.test.tsx`
Expected: FAIL — `Failed to resolve import "./Layer"`.

- [ ] **Step 3: Write `web/src/shell/presence.ts`**

Complete file:

```ts
import { useEffect, useState, type CSSProperties, type RefObject } from "react";

/** The handoff's reduce-motion substitute for every rise. */
const REDUCED_MOTION_MS = 200;

function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export interface Presence {
  mounted: boolean;
  leaving: boolean;
}

/**
 * Keep a surface mounted for the length of its leave animation.
 *
 * A timer, not `animationend`: jsdom fires no animation events, and a missed
 * event would strand a full-screen layer over the app forever. The duration is
 * the caller's, except under reduce-motion where every leave is 200 ms.
 */
export function usePresence(open: boolean, durationMs: number): Presence {
  const [mounted, setMounted] = useState(open);
  const [leaving, setLeaving] = useState(false);
  const [wasOpen, setWasOpen] = useState(open);

  // State adjusted during render, the way react.dev documents for "storing
  // information from previous renders": an opening layer is mounted on this
  // very pass, and a closing one starts its leave, with no effect in between.
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setMounted(true);
      setLeaving(false);
    } else if (mounted) {
      setLeaving(true);
    }
  }

  useEffect(() => {
    if (!leaving) return;
    const ms = prefersReducedMotion() ? REDUCED_MOTION_MS : durationMs;
    const timer = setTimeout(() => {
      setMounted(false);
      setLeaving(false);
    }, ms);
    // Re-opening mid-leave flips `leaving` back, which clears this.
    return () => clearTimeout(timer);
  }, [leaving, durationMs]);

  return { mounted, leaving };
}

/** The modal surfaces that are up, bottom to top. Everything under the top one is inert. */
const surfaces: HTMLElement[] = [];

/**
 * While a modal surface is up, everything behind it is inert and focus lives
 * in the surface — Full Keyboard Access and VoiceOver must not wander into a
 * room they cannot see. That includes a surface under another one: a gate over
 * an open sheet makes the sheet inert too, and gives it back when it leaves.
 * Focus goes back where it was once the surface has left, unless that place
 * is now behind another surface.
 *
 * `active` is the presence's `mounted`, so the surface stays inert-backed for
 * its leave animation too, and the panel ref is set by the time this runs.
 */
export function useModalFocus(active: boolean, panel: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const node = panel.current;
    if (!active || !node) return;
    const app = document.getElementById("root");
    const previous = document.activeElement;
    app?.setAttribute("inert", "");
    surfaces.at(-1)?.setAttribute("inert", "");
    surfaces.push(node);
    node.focus({ preventScroll: true });
    return () => {
      const index = surfaces.indexOf(node);
      if (index >= 0) surfaces.splice(index, 1);
      const top = surfaces.at(-1);
      if (top) top.removeAttribute("inert");
      else app?.removeAttribute("inert");
      if (
        previous instanceof HTMLElement &&
        previous.isConnected &&
        previous.closest("[inert]") === null
      ) {
        previous.focus({ preventScroll: true });
      }
    };
  }, [active, panel]);
}

type RiseStyle = CSSProperties & { "--layer-duration": string };

/** The theme's surface colours and the duration `.rise-in`/`.rise-out` read. */
export function riseStyle(durationMs: number): RiseStyle {
  return { background: "var(--bg)", color: "var(--fg)", "--layer-duration": `${durationMs}ms` };
}

export function riseClass(leaving: boolean): string {
  return leaving ? "rise-out" : "rise-in";
}
```

- [ ] **Step 4: Write `web/src/shell/Layer.tsx`**

Complete file:

```tsx
import { useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { riseClass, riseStyle, useModalFocus, usePresence } from "./presence";

export interface LayerProps {
  open: boolean;
  /** The dialog's accessible name — standalone mode has no window title to fall back on. */
  label: string;
  /** Sheets 380, workshop 400, door 420. */
  durationMs?: number;
  /**
   * The handoff's stack, sheet < door < gate: a lapsed session paints over an
   * open Door, whatever order they were mounted in.
   */
  level?: "layer" | "gate";
  className?: string;
  children: ReactNode;
}

/**
 * A full-screen surface that rises from the bottom, stays for its leave
 * animation, and holds focus while it is up. Rendered into `<body>` so the
 * app in `#root` can be made inert behind it.
 */
export function Layer({
  open,
  label,
  durationMs = 400,
  level = "layer",
  className = "",
  children,
}: LayerProps) {
  const { mounted, leaving } = usePresence(open, durationMs);
  const panel = useRef<HTMLDivElement>(null);
  useModalFocus(mounted, panel);
  if (!mounted) return null;

  return createPortal(
    <div
      ref={panel}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-label={label}
      className={`fixed inset-0 ${level === "gate" ? "z-40" : "z-30"} flex flex-col overflow-hidden outline-none ${riseClass(leaving)} ${className}`}
      style={riseStyle(durationMs)}
    >
      {children}
    </div>,
    document.body,
  );
}
```

- [ ] **Step 5: Write `web/src/shell/Sheet.tsx`**

Complete file:

```tsx
import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { riseClass, riseStyle, useModalFocus, usePresence } from "./presence";

const SHEET_MS = 380;

export interface SheetProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}

/**
 * Bottom-anchored sheet over a 45% scrim: grab bar, title, `Done`. The scrim,
 * `Done` and the Escape key all dismiss it — constraint §4.12, a standalone
 * app has no browser chrome to escape with.
 */
export function Sheet({ open, title, onClose, children }: SheetProps) {
  const { mounted, leaving } = usePresence(open, SHEET_MS);
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useModalFocus(mounted, panel);

  // On the panel, not `document`: the sheet holds focus while it is on top, so
  // the key reaches it, and a gate over it swallows Escape instead.
  useEffect(() => {
    const node = panel.current;
    if (!mounted || !node) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    node.addEventListener("keydown", onKeyDown);
    return () => node.removeEventListener("keydown", onKeyDown);
  }, [mounted, onClose]);

  if (!mounted) return null;

  return createPortal(
    // The dialog is the whole thing, scrim included, so the scrim's `Close` is
    // inside the modal subtree and assistive tech can reach it. z-20, under
    // Layer's z-30: the handoff stacks sheet < Door < gate, so a critical action
    // or a lapsed session paints over an open sheet, not under it.
    <div
      ref={panel}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="fixed inset-0 z-20 flex flex-col justify-end outline-none"
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 border-0"
        style={{ background: "var(--scrim)" }}
      />
      <div
        className={`relative flex max-h-[78%] flex-col overflow-hidden rounded-t-[28px] ${riseClass(leaving)}`}
        style={riseStyle(SHEET_MS)}
      >
        <div
          aria-hidden="true"
          className="mx-auto mt-2 h-[5px] w-10 rounded-full"
          style={{ background: "var(--line)" }}
        />
        <div className="flex items-center justify-between gap-3 px-5 pt-2 pb-3">
          <h2 id={titleId} className="t-title">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="-mr-2 flex h-11 min-w-11 items-center justify-end border-0 bg-transparent text-[15px] font-medium"
            style={{ color: "var(--accent)" }}
          >
            Done
          </button>
        </div>
        <div
          className="flex flex-1 flex-col gap-3 overflow-y-auto px-5"
          style={{ paddingBottom: "calc(40px + env(safe-area-inset-bottom, 0px))" }}
        >
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}
```

- [ ] **Step 6: Give the leave its own keyframes in `web/src/index.css`**

`.rise-out` was `rise` played in reverse. Two classes on the same `animation-name` do not restart the animation, so a layer re-opened mid-leave would finish sinking. Reversing the direction also reversed the easing, so the leave keeps a mirrored curve of its own. Four edits.

In the theme-independent `:root` block, the comment's "The two easing curves" becomes "The easing curves", and after `--ease-settle` add:

```css
  /* `rise` mirrored in time, for the leave — what `animation-direction:
     reverse` used to play. Not a third curve. */
  --ease-sink: cubic-bezier(0.8, 0, 0.8, 0.2);
```

In `@layer components`, replace the two motion rules:

```css
  .rise-in {
    animation: rise var(--layer-duration, 400ms) var(--ease-rise) both;
  }
  /* `sink` is its own keyframes, not `rise` reversed: two classes on the same
     animation-name would not restart it, so a layer re-opened mid-leave would
     finish sinking. Reversing the direction also reversed the easing, which
     `--ease-sink` keeps: the leave still accelerates away. */
  .rise-out {
    animation: sink var(--layer-duration, 400ms) var(--ease-sink) both;
  }
```

After `@keyframes rise`, replace the `fade` keyframes and its comment with:

```css
@keyframes sink {
  from {
    transform: translateY(0);
  }
  to {
    transform: translateY(100%);
  }
}

/* Only used as the reduce-motion substitutes for `rise` and `sink`. */
@keyframes fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@keyframes fade-out {
  from {
    opacity: 1;
  }
  to {
    opacity: 0;
  }
}
```

In the `prefers-reduced-motion` block, the comment now reads "give `rise` and `sink` their opacity step", and the two class rules become:

```css
  .rise-in {
    animation: fade 200ms linear both !important;
  }
  .rise-out {
    animation: fade-out 200ms linear both !important;
  }
```

- [ ] **Step 7: Run the test**

Run: `npm test -- src/shell/Layer.test.tsx`
Expected: `Test Files  1 passed (1)`, 14 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  11 passed (11)`, no eslint output, `✓ built in …`.

- [ ] **Step 8: Commit**

```bash
git add web/src/shell/presence.ts web/src/shell/Layer.tsx web/src/shell/Sheet.tsx web/src/shell/Layer.test.tsx web/src/index.css
git commit -m "feat(web): Layer and Sheet, the two surfaces everything rises on"
```

---

### Task 6: The API client stops redirecting and starts announcing

Decision 5. The outgoing `api()` did `location.assign("/login")` on a 401, which throws away the whole app — including the last-known state the design insists on keeping visible — and has no answer at all for a 403. Both become events instead: a two-method emitter that `AuthGate` (Task 10) listens to. `api()` still throws, so every caller's error path is unchanged. One 401 is not a lapse: the backend answers a rejected passkey assertion (`/api/auth/{login,register}/complete`) with 401 `Authentication failed`, and that is the attempt failing on the gate the user is already standing on, so `api()` does not announce it.

**Files:**
- Create: `web/src/lib/auth-events.ts`, `web/src/lib/auth-events.test.ts`
- Modify: `web/src/lib/api.ts`, `web/src/lib/api.test.ts`

- [ ] **Step 1: Write the failing emitter test**

Create `web/src/lib/auth-events.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { authEvents, type AuthEventKind } from "./auth-events";

// The emitter is a module singleton: unsubscribe in cleanup, not in the test
// body, or one failed assertion leaks its handler into every later test.
const subscriptions: Array<() => void> = [];
function listen(kind: AuthEventKind, fn: () => void): () => void {
  const off = authEvents.on(kind, fn);
  subscriptions.push(off);
  return off;
}

afterEach(() => {
  for (const off of subscriptions.splice(0)) off();
  vi.restoreAllMocks();
});

describe("authEvents", () => {
  it("calls every handler registered for a kind", () => {
    const first = vi.fn();
    const second = vi.fn();
    listen("expired", first);
    listen("expired", second);

    authEvents.emit("expired");

    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("keeps the kinds apart", () => {
    const expired = vi.fn();
    const denied = vi.fn();
    listen("expired", expired);
    listen("denied", denied);

    authEvents.emit("denied");

    expect(expired).not.toHaveBeenCalled();
    expect(denied).toHaveBeenCalledTimes(1);
  });

  it("stops calling a handler once it unsubscribes", () => {
    const handler = vi.fn();
    const off = listen("expired", handler);
    off();

    authEvents.emit("expired");

    expect(handler).not.toHaveBeenCalled();
  });

  it("survives a handler that unsubscribes itself mid-emit", () => {
    const seen: string[] = [];
    const off1 = listen("expired", () => {
      seen.push("first");
      off1();
    });
    listen("expired", () => seen.push("second"));

    expect(() => authEvents.emit("expired")).not.toThrow();
    expect(seen).toEqual(["first", "second"]);
  });

  it("keeps going past a handler that throws, and does not rethrow", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const second = vi.fn();
    listen("expired", () => {
      throw new Error("handler broke");
    });
    listen("expired", second);

    expect(() => authEvents.emit("expired")).not.toThrow();
    expect(second).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalledWith("auth-events handler failed", expect.any(Error));
  });

  it("is a no-op with nothing listening", () => {
    expect(() => authEvents.emit("denied")).not.toThrow();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/lib/auth-events.test.ts`
Expected: FAIL — `Failed to resolve import "./auth-events"`.

- [ ] **Step 3: Write `web/src/lib/auth-events.ts`**

Complete file:

```ts
/**
 * Two facts the app must react to from anywhere: the session lapsed (401), and
 * the house refused this network (403). A module-level emitter rather than
 * context, because `api()` is a plain function with no React above it.
 */
export type AuthEventKind = "expired" | "denied";

type Handler = () => void;

class AuthEvents {
  private handlers = new Map<AuthEventKind, Set<Handler>>();

  /** Returns the unsubscribe function — safe to use directly as an effect cleanup. */
  on(kind: AuthEventKind, fn: Handler): () => void {
    const existing = this.handlers.get(kind) ?? new Set<Handler>();
    existing.add(fn);
    this.handlers.set(kind, existing);
    return () => {
      existing.delete(fn);
    };
  }

  emit(kind: AuthEventKind): void {
    // Copy first: a handler is allowed to unsubscribe itself while we iterate.
    for (const fn of [...(this.handlers.get(kind) ?? [])]) {
      // `api()` emits on its way to throwing an ApiError; a handler that throws
      // must not skip the others or replace the error the caller is catching.
      try {
        fn();
      } catch (err) {
        console.error("auth-events handler failed", err);
      }
    }
  }
}

export const authEvents = new AuthEvents();
```

- [ ] **Step 4: Run the emitter test**

Run: `npm test -- src/lib/auth-events.test.ts`
Expected: `Test Files  1 passed (1)`, 6 tests.

- [ ] **Step 5: Replace `web/src/lib/api.test.ts`**

Complete file:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, del, post, put } from "./api";
import { authEvents, type AuthEventKind } from "./auth-events";

// `authEvents` is a module singleton: unsubscribe in cleanup, not in the test
// body, or one failed assertion leaks its handler into every later test.
const subscriptions: Array<() => void> = [];
function listen(kind: AuthEventKind, fn: () => void): void {
  subscriptions.push(authEvents.on(kind, fn));
}

afterEach(() => {
  for (const off of subscriptions.splice(0)) off();
  vi.unstubAllGlobals();
});

function stubFetch(status: number, body: unknown) {
  const mock = vi.fn<typeof fetch>(
    async () => new Response(status === 204 ? null : JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("api", () => {
  it("returns parsed JSON on success", async () => {
    stubFetch(200, { ok: true });
    await expect(api("/api/admin/overview")).resolves.toEqual({ ok: true });
  });

  it("sends JSON and keeps caller headers", async () => {
    const mock = stubFetch(200, {});
    await api("/x", { headers: { "X-Test": "1" } });
    const init = mock.mock.calls[0][1] as RequestInit;
    expect(init.headers).toMatchObject({ "Content-Type": "application/json", "X-Test": "1" });
  });

  it("resolves undefined for a 204", async () => {
    stubFetch(204, null);
    await expect(api("/x", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("carries the FastAPI detail onto the error", async () => {
    stubFetch(503, { detail: "Attention store unavailable" });
    await expect(api("/x")).rejects.toMatchObject({
      status: 503,
      detail: "Attention store unavailable",
      message: "Attention store unavailable",
    });
  });

  it("keeps a non-JSON error body as the detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("upstream is down", { status: 502 })),
    );
    await expect(api("/x")).rejects.toMatchObject({ status: 502, detail: "upstream is down" });
  });

  it("falls back to the status text when the error body is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 500, statusText: "Internal Server Error" })),
    );
    await expect(api("/x")).rejects.toMatchObject({
      status: 500,
      detail: "Internal Server Error",
      message: "Internal Server Error",
    });
  });

  it("announces a lapsed session on 401 and still throws", async () => {
    const expired = vi.fn();
    listen("expired", expired);
    const assign = vi.fn();
    vi.stubGlobal("location", { assign, pathname: "/", hostname: "alfred.example.com" });
    stubFetch(401, { detail: "Authentication required" });

    await expect(api("/x")).rejects.toBeInstanceOf(ApiError);

    expect(expired).toHaveBeenCalledTimes(1);
    // The old client hard-redirected here; the Expired gate now rises over the room.
    expect(assign).not.toHaveBeenCalled();
  });

  it("does not call a rejected passkey attempt a lapsed session", async () => {
    const expired = vi.fn();
    listen("expired", expired);
    stubFetch(401, { detail: "Authentication failed" });

    await expect(post("/api/auth/login/complete", {})).rejects.toMatchObject({
      status: 401,
      detail: "Authentication failed",
    });
    await expect(post("/api/auth/register/complete", {})).rejects.toMatchObject({ status: 401 });

    expect(expired).not.toHaveBeenCalled();
  });

  it("announces a refused network on 403 and still throws", async () => {
    const denied = vi.fn();
    listen("denied", denied);
    stubFetch(403, { detail: "Request from untrusted network 192.168.1.24" });

    await expect(api("/x")).rejects.toMatchObject({ status: 403 });

    expect(denied).toHaveBeenCalledTimes(1);
  });

  it("does not announce anything for other failures", async () => {
    const expired = vi.fn();
    const denied = vi.fn();
    listen("expired", expired);
    listen("denied", denied);
    stubFetch(500, { detail: "boom" });

    await expect(api("/x")).rejects.toMatchObject({ status: 500 });

    expect(expired).not.toHaveBeenCalled();
    expect(denied).not.toHaveBeenCalled();
  });
});

describe("post / put / del", () => {
  it("post sends a JSON body", async () => {
    const mock = stubFetch(200, { status: "ok" });
    await post("/api/actions/a91f/confirm", { note: "yes" });
    const init = mock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"note":"yes"}');
  });

  it("post with no body sends none", async () => {
    const mock = stubFetch(200, {});
    await post("/api/auth/logout");
    const init = mock.mock.calls[0][1] as RequestInit;
    expect(init.body).toBeUndefined();
  });

  it("put sends a JSON body", async () => {
    const mock = stubFetch(200, { status: "ok" });
    await put("/api/integrations/home-service/credentials", { url: "http://192.168.1.10:8123" });
    const init = mock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("PUT");
    expect(init.body).toBe('{"url":"http://192.168.1.10:8123"}');
  });

  it("del sends DELETE", async () => {
    const mock = stubFetch(200, { deleted: true });
    await del("/api/auth/sessions/abc");
    expect((mock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npm test -- src/lib/api.test.ts`
Expected: FAIL — `put` is not exported (`TypeError: put is not a function`, since Vite transpiles the import rather than failing to link it). Once that is fixed the 401/403 announcement tests and the `detail` assertions still fail against the old implementation.

- [ ] **Step 7: Rewrite `web/src/lib/api.ts`**

Complete file:

```ts
import { authEvents } from "./auth-events";

export class ApiError extends Error {
  status: number;
  /** The FastAPI `detail` string, or the raw body when it was not JSON. */
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function readDetail(resp: Response): Promise<string> {
  const text = await resp.text();
  try {
    const parsed: unknown = JSON.parse(text);
    if (
      parsed &&
      typeof parsed === "object" &&
      "detail" in parsed &&
      typeof (parsed as { detail: unknown }).detail === "string"
    ) {
      return (parsed as { detail: string }).detail;
    }
  } catch {
    // Not JSON — the raw text is the most honest thing we have.
  }
  return text;
}

/**
 * A rejected passkey assertion is a 401 too — that attempt failing, not a
 * session lapsing. The gate that asked shows it; no Expired gate over it.
 */
const isPasskeyAttempt = (path: string): boolean =>
  path.startsWith("/api/auth/login/") || path.startsWith("/api/auth/register/");

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!resp.ok) {
    const detail = await readDetail(resp);
    // Announce, never navigate: the gate rises over whatever is on screen, so the
    // last-known state stays visible behind it (spec §5.2, "live is not last-known").
    if (resp.status === 401 && !isPasskeyAttempt(path)) authEvents.emit("expired");
    if (resp.status === 403) authEvents.emit("denied");
    throw new ApiError(resp.status, detail || resp.statusText);
  }

  // 204 has no body; `await resp.json()` would throw on it.
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const post = <T>(path: string, body?: unknown): Promise<T> =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const put = <T>(path: string, body?: unknown): Promise<T> =>
  api<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) });

export const del = <T>(path: string): Promise<T> => api<T>(path, { method: "DELETE" });
```

- [ ] **Step 8: Run the API test and the suite**

Run: `npm test -- src/lib/api.test.ts`
Expected: `Test Files  1 passed (1)`, 14 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  12 passed (12)`, no eslint output, `✓ built in …`.

```bash
grep -rn "location.assign" src/
```

Expected: no output — the hard redirect is gone from the client entirely.

- [ ] **Step 9: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/api.test.ts web/src/lib/auth-events.ts web/src/lib/auth-events.test.ts
git commit -m "feat(web): 401 and 403 raise gates instead of redirecting"
```

---


### Task 7: Auth helpers, and the WebAuthn module carried over untouched

`webauthn.ts` is correct and hard-won (the `_challenge_id` / `_device_name` field layout has to match `auth.js` exactly). It is carried over byte-identical — this task proves that rather than rewriting it — and joined by the small helpers the gates need: the status read, and the device name the sign-in gate's foot line remembers, since `GET /api/auth/status` deliberately says nothing about the device before you are authenticated (spec §10).

**Files:**
- Keep unchanged: `web/src/lib/webauthn.ts`, `web/src/lib/webauthn.test.ts`
- Create: `web/src/lib/auth.ts`, `web/src/lib/auth.test.ts`

- [ ] **Step 1: Prove the WebAuthn carry-over is untouched**

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
git diff --stat origin/master -- web/src/lib/webauthn.ts web/src/lib/webauthn.test.ts
```

Expected: no output. If there is any, revert those two files to `origin/master` — nothing in Phase 1 requires a change to them. `registerPasskey(deviceName)`, `loginPasskey(conditional?, signal?)` and `logout()` are the three functions the gates call.

- [ ] **Step 2: Write the failing auth test**

Create `web/src/lib/auth.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEVICE_KEY, defaultDeviceName, fetchAuthStatus, rememberDevice, rememberedDevice } from "./auth";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("fetchAuthStatus", () => {
  it("reads GET /api/auth/status", async () => {
    const mock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ registered: true, authenticated: false }), { status: 200 }),
    );
    vi.stubGlobal("fetch", mock);

    await expect(fetchAuthStatus()).resolves.toEqual({ registered: true, authenticated: false });
    expect(mock.mock.calls[0][0]).toBe("/api/auth/status");
    expect((mock.mock.calls[0][1] as RequestInit | undefined)?.method ?? "GET").toBe("GET");
  });
});

describe("defaultDeviceName", () => {
  it("names the common devices", () => {
    expect(
      defaultDeviceName(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
      ),
    ).toBe("iPhone");
    expect(defaultDeviceName("Mozilla/5.0 (iPad; CPU OS 18_2 like Mac OS X) AppleWebKit/605.1.15")).toBe("iPad");
    expect(defaultDeviceName("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15")).toBe("Mac");
    expect(defaultDeviceName("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36")).toBe("Android");
    expect(defaultDeviceName("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")).toBe("Windows");
  });

  it("falls back rather than guessing", () => {
    expect(defaultDeviceName("curl/8.9.1")).toBe("This device");
  });
});

describe("rememberDevice / rememberedDevice", () => {
  it("uses the alfred.device key", () => {
    expect(DEVICE_KEY).toBe("alfred.device");
  });

  it("round-trips a device", () => {
    rememberDevice({ name: "iPhone", registeredAt: "2026-09-07T07:02:00.000Z" });
    expect(rememberedDevice()).toEqual({ name: "iPhone", registeredAt: "2026-09-07T07:02:00.000Z" });
  });

  it("is null before anything is remembered", () => {
    expect(rememberedDevice()).toBeNull();
  });

  it("is null for a corrupt value", () => {
    localStorage.setItem(DEVICE_KEY, "{not json");
    expect(rememberedDevice()).toBeNull();
  });

  it("is null for a value of the wrong shape", () => {
    localStorage.setItem(DEVICE_KEY, JSON.stringify({ name: 17 }));
    expect(rememberedDevice()).toBeNull();
  });

  it("is null when registeredAt is missing", () => {
    localStorage.setItem(DEVICE_KEY, JSON.stringify({ name: "iPhone" }));
    expect(rememberedDevice()).toBeNull();
  });

  it("survives a storage that refuses to write", () => {
    vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => rememberDevice({ name: "iPhone", registeredAt: "2026-09-07T07:02:00.000Z" })).not.toThrow();
  });

  it("is null when storage cannot be read", () => {
    vi.spyOn(localStorage, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(rememberedDevice()).toBeNull();
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npm test -- src/lib/auth.test.ts`
Expected: FAIL — `Failed to resolve import "./auth"`.

- [ ] **Step 4: Write `web/src/lib/auth.ts`**

Complete file:

```ts
import { api } from "./api";
import type { AuthStatus } from "./types";

export const DEVICE_KEY = "alfred.device";

export interface RememberedDevice {
  name: string;
  /** ISO 8601, from the moment registration completed. */
  registeredAt: string;
}

export function fetchAuthStatus(): Promise<AuthStatus> {
  return api<AuthStatus>("/api/auth/status");
}

/**
 * A first guess at what to call this passkey. The user never sees a prompt for
 * it in Phase 1, so it has to be right often and harmless when wrong.
 *
 * iOS before Mac, because every iOS user agent carries "like Mac OS X" — a real
 * iPad would be named "Mac" otherwise. An iPad in desktop mode reports plain
 * "Macintosh" and no ordering can catch it; that is accepted, since an iPad that
 * calls itself a Mac is a better failure than a Mac that calls itself an iPad.
 */
export function defaultDeviceName(ua: string = navigator.userAgent): string {
  if (/iPhone/i.test(ua)) return "iPhone";
  if (/iPad/i.test(ua)) return "iPad";
  if (/Macintosh|Mac OS X/i.test(ua)) return "Mac";
  if (/Android/i.test(ua)) return "Android";
  if (/Windows/i.test(ua)) return "Windows";
  return "This device";
}

export function rememberDevice(device: RememberedDevice): void {
  try {
    localStorage.setItem(DEVICE_KEY, JSON.stringify(device));
  } catch {
    // Private mode. The sign-in gate's foot line falls back to "this phone".
  }
}

export function rememberedDevice(): RememberedDevice | null {
  try {
    const raw = localStorage.getItem(DEVICE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof (parsed as RememberedDevice).name === "string" &&
      typeof (parsed as RememberedDevice).registeredAt === "string"
    ) {
      return parsed as RememberedDevice;
    }
    return null;
  } catch {
    return null;
  }
}
```

- [ ] **Step 5: Run the auth test and the suite**

Run: `npm test -- src/lib/auth.test.ts`
Expected: `Test Files  1 passed (1)`, 11 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  13 passed (13)`, no eslint output, `✓ built in …`.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/auth.ts web/src/lib/auth.test.ts
git commit -m "feat(web): auth status read and the remembered passkey device"
```

---

### Task 8: Gate primitives — the layout, the field and the step list

All four gates are the same composition: a static dot field at the top, bottom-aligned copy (`mono kicker · 30/500 title · 15/1.5 body`), an optional step list, and a footer of `56 px primary · 50 px secondary · mono foot`. Building that once means the four gates in Tasks 9 and 10 are copy and behaviour only.

The step list has two shapes in the design: the setup progress rail (done / current / ahead) and the attention rows (allowed / ask me, tapped to flip). Same 48 px row, same 22 px ring, different meaning — one component, two variants.

**Files:**
- Create: `web/src/gates/Gate.tsx`, `web/src/gates/GateField.tsx`, `web/src/gates/StepList.tsx`
- Create: `web/src/gates/Gate.test.tsx`, `web/src/gates/StepList.test.tsx`
- Modify: `web/src/index.css` (one comment line)

- [ ] **Step 1: Write the failing Gate test**

Create `web/src/gates/Gate.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Gate } from "./Gate";

describe("Gate", () => {
  it("lays out kicker, title, body and foot", () => {
    render(
      <Gate
        kicker="first run · alfred.example.com"
        title="Good evening. I am Alfred."
        body="This device will hold the only key to the house."
        foot="The passkey never leaves the phone. Nothing here phones home."
      />,
    );

    expect(screen.getByText("first run · alfred.example.com")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Good evening. I am Alfred." }),
    ).toBeInTheDocument();
    expect(screen.getByText("This device will hold the only key to the house.")).toBeInTheDocument();
    expect(
      screen.getByText("The passkey never leaves the phone. Nothing here phones home."),
    ).toBeInTheDocument();
  });

  it("renders the dot field, hidden from assistive tech", () => {
    const { container } = render(<Gate kicker="k" title="t" />);
    const field = container.querySelector(".gate-field");
    expect(field).not.toBeNull();
    expect(field).toHaveAttribute("aria-hidden", "true");
  });

  it("calls the primary action", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Gate kicker="k" title="t" primary={{ label: "Create passkey with Face ID", onClick }} />,
    );

    await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("disables the primary while busy and says so", () => {
    render(
      <Gate
        kicker="k"
        title="t"
        primary={{ label: "Continue", onClick: () => {}, busy: true }}
      />,
    );
    const button = screen.getByRole("button", { name: "Continue" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("disables the primary when asked", () => {
    render(
      <Gate kicker="k" title="t" primary={{ label: "Finish", onClick: () => {}, disabled: true }} />,
    );
    expect(screen.getByRole("button", { name: "Finish" })).toBeDisabled();
  });

  it("renders a secondary action only when given one", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    const { rerender } = render(<Gate kicker="k" title="t" />);
    expect(screen.queryByRole("button")).toBeNull();

    rerender(<Gate kicker="k" title="t" secondary={{ label: "Do this later", onClick }} />);
    await user.click(screen.getByRole("button", { name: "Do this later" }));

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("renders children between the body and the footer", () => {
    render(
      <Gate kicker="k" title="t" body="b">
        <p>step list goes here</p>
      </Gate>,
    );
    expect(screen.getByText("step list goes here")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/gates/Gate.test.tsx`
Expected: FAIL — `Failed to resolve import "./Gate"`.

- [ ] **Step 3: Write `web/src/gates/GateField.tsx`**

Complete file:

```tsx
/**
 * The gates' presence: a 220 px dot grid whose only movement is `drift`, a
 * pixel out and back over six seconds. Not the Room's canvas field (plan 1b) —
 * a gate is not a place where Alfred is listening.
 */
export function GateField() {
  return (
    <div
      aria-hidden="true"
      className="gate-field pointer-events-none absolute inset-x-0 top-0 h-[220px]"
    />
  );
}
```

- [ ] **Step 4: Write `web/src/gates/Gate.tsx`**

Complete file:

```tsx
import type { ReactNode } from "react";
import { GateField } from "@/gates/GateField";

export interface GateAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
}

export interface GateProps {
  kicker: string;
  title: string;
  body?: string;
  /** A step list, a credential form — anything between the body and the footer. */
  children?: ReactNode;
  primary?: GateAction;
  secondary?: { label: string; onClick: () => void };
  foot?: string;
}

export function Gate({ kicker, title, body, children, primary, secondary, foot }: GateProps) {
  return (
    <div
      className="relative flex flex-1 flex-col overflow-hidden"
      style={{ background: "var(--bg)", color: "var(--fg)" }}
    >
      <GateField />

      {/* Copy sits at the bottom of the field, not the middle: padding 0 28 12. */}
      <div className="relative flex flex-1 flex-col justify-end gap-3 px-7 pb-3">
        <div className="t-meta">{kicker}</div>
        <h1 className="t-gate">{title}</h1>
        {body ? (
          <p className="t-body" style={{ color: "var(--fg2)" }}>
            {body}
          </p>
        ) : null}
        {children}
      </div>

      <div
        className="relative flex flex-col gap-2.5 px-5 pt-2"
        style={{ paddingBottom: "calc(40px + env(safe-area-inset-bottom, 0px))" }}
      >
        {primary ? (
          <button
            type="button"
            onClick={primary.onClick}
            disabled={primary.disabled === true || primary.busy === true}
            aria-busy={primary.busy === true}
            className="h-14 rounded-[28px] border-0 text-[16px] font-medium disabled:opacity-60"
            style={{ background: "var(--ink)", color: "var(--paper)" }}
          >
            {primary.label}
          </button>
        ) : null}

        {secondary ? (
          <button
            type="button"
            onClick={secondary.onClick}
            className="h-[50px] rounded-[25px] border-0 bg-transparent text-[15px] font-medium"
            style={{ color: "var(--fg)" }}
          >
            {secondary.label}
          </button>
        ) : null}

        {foot ? <div className="t-meta text-center">{foot}</div> : null}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run the Gate test**

Run: `npm test -- src/gates/Gate.test.tsx`
Expected: `Test Files  1 passed (1)`, 7 tests.

- [ ] **Step 6: Write the failing StepList test**

Create `web/src/gates/StepList.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StepList } from "./StepList";

describe("StepList — progress", () => {
  it("marks each row done, current or ahead", () => {
    render(
      <StepList
        variant="progress"
        steps={[
          { label: "Register this iPhone", meta: "07:02", state: "done" },
          { label: "Connect Home Assistant", state: "current" },
          { label: "Choose what the reflex may touch", state: "todo" },
        ]}
      />,
    );

    expect(screen.getByText("Register this iPhone").closest("[data-step-state]")).toHaveAttribute(
      "data-step-state",
      "done",
    );
    expect(screen.getByText("Connect Home Assistant").closest("[data-step-state]")).toHaveAttribute(
      "data-step-state",
      "current",
    );
    expect(
      screen.getByText("Choose what the reflex may touch").closest("[data-step-state]"),
    ).toHaveAttribute("data-step-state", "todo");
    expect(screen.getByText("07:02")).toBeInTheDocument();
  });

  it("tells a screen reader what the ring colours say", () => {
    render(
      <StepList
        variant="progress"
        steps={[
          { label: "Register this iPhone", state: "done" },
          { label: "Connect Home Assistant", state: "current" },
          { label: "Choose what the reflex may touch", state: "todo" },
        ]}
      />,
    );

    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(screen.getByRole("list")).toHaveAttribute("role", "list");
    expect(rows[0]).toHaveTextContent("done");
    expect(rows[0]).not.toHaveTextContent("not done");
    expect(rows[0]).not.toHaveAttribute("aria-current");
    expect(rows[1]).toHaveAttribute("aria-current", "step");
    expect(rows[1]).not.toHaveTextContent(/done/);
    expect(rows[2]).toHaveTextContent("not done");
    expect(rows[2]).not.toHaveAttribute("aria-current");
  });

  it("renders no buttons — a progress rail is not tappable", () => {
    render(
      <StepList variant="progress" steps={[{ label: "Register this iPhone", state: "current" }]} />,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("StepList — toggle", () => {
  it("shows the state word for each row", () => {
    render(
      <StepList
        variant="toggle"
        onToggle={() => {}}
        steps={[
          { id: "light", label: "Light · 6 found", allowed: true },
          { id: "fan", label: "Fan · 4 found", allowed: false },
        ]}
      />,
    );

    expect(screen.getByRole("list")).toHaveAttribute("role", "list");
    const light = screen.getByRole("button", { name: /Light · 6 found/ });
    const fan = screen.getByRole("button", { name: /Fan · 4 found/ });
    expect(light).toHaveTextContent("allowed");
    expect(fan).toHaveTextContent("ask me");
    expect(light).toHaveAttribute("aria-pressed", "true");
    expect(fan).toHaveAttribute("aria-pressed", "false");
  });

  it("reports the row that was tapped", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(
      <StepList
        variant="toggle"
        onToggle={onToggle}
        steps={[{ id: "media_player", label: "Media player · 2 found", allowed: true }]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Media player · 2 found/ }));

    expect(onToggle).toHaveBeenCalledWith("media_player");
  });
});
```

- [ ] **Step 7: Run it to verify it fails**

Run: `npm test -- src/gates/StepList.test.tsx`
Expected: FAIL — `Failed to resolve import "./StepList"`.

- [ ] **Step 8: Write `web/src/gates/StepList.tsx`**

Complete file:

```tsx
import type { CSSProperties } from "react";

export type StepState = "done" | "current" | "todo";

export interface ProgressStep {
  label: string;
  meta?: string;
  state: StepState;
}

export interface ToggleStep {
  id: string;
  label: string;
  allowed: boolean;
}

export type StepListProps =
  | { variant: "progress"; steps: ProgressStep[] }
  | { variant: "toggle"; steps: ToggleStep[]; onToggle: (id: string) => void };

const ROW: CSSProperties = { borderTop: "1px solid var(--line)" };

function ring(filled: boolean, border: string): CSSProperties {
  return {
    borderColor: border,
    background: filled ? border : "transparent",
  };
}

/** What a screen reader hears where a sighted user sees the ring's colour. */
const STATE_WORD: Record<StepState, string | null> = {
  done: "done",
  current: null, // aria-current="step" says it
  todo: "not done",
};

/** 22 px ring: accent filled = done/allowed, fg ring = current, line = ahead/ask me. */
function Ring({ style }: { style: CSSProperties }) {
  return (
    <span
      aria-hidden="true"
      className="box-border block h-[22px] w-[22px] shrink-0 rounded-full border-[1.5px]"
      style={style}
    />
  );
}

/**
 * Both variants carry an explicit `role="list"`: preflight strips list-style,
 * and WebKit strips the list semantics with it — VoiceOver would not say
 * "3 items" without the attribute.
 */
export function StepList(props: StepListProps) {
  if (props.variant === "progress") {
    return (
      <ol role="list" className="flex flex-col pt-2">
        {props.steps.map((step) => (
          <li
            key={step.label}
            data-step-state={step.state}
            aria-current={step.state === "current" ? "step" : undefined}
            className="flex min-h-12 items-center gap-3"
            style={ROW}
          >
            <Ring
              style={
                step.state === "done"
                  ? ring(true, "var(--accent)")
                  : step.state === "current"
                    ? ring(false, "var(--fg)")
                    : ring(false, "var(--line)")
              }
            />
            <span
              className="t-row flex-1"
              style={{ color: step.state === "todo" ? "var(--muted)" : "var(--fg)" }}
            >
              {step.label}
            </span>
            {STATE_WORD[step.state] ? <span className="sr-only">{STATE_WORD[step.state]}</span> : null}
            {step.meta ? <span className="t-meta">{step.meta}</span> : null}
          </li>
        ))}
      </ol>
    );
  }

  return (
    <ul role="list" className="flex flex-col pt-2">
      {props.steps.map((step) => (
        <li key={step.id}>
          <button
            type="button"
            aria-pressed={step.allowed}
            onClick={() => props.onToggle(step.id)}
            className="flex min-h-12 w-full items-center gap-3 border-0 bg-transparent text-left"
            style={ROW}
          >
            <Ring style={step.allowed ? ring(true, "var(--accent)") : ring(false, "var(--line)")} />
            <span className="t-row flex-1" style={{ color: "var(--fg)" }}>
              {step.label}
            </span>
            <span className="t-meta">{step.allowed ? "allowed" : "ask me"}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 9: Run the StepList test and the suite**

Run: `npm test -- src/gates/StepList.test.tsx`
Expected: `Test Files  1 passed (1)`, 5 tests.

- [ ] **Step 10: Align the `.gate-field` comment in `web/src/index.css`**

The field is not static — `drift` runs on it — so the comment should say what it does, not deny it. Replace the one comment line above `.gate-field`:

```css
  /* The gates' presence field: a dot grid that only drifts a pixel and back. */
```

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  15 passed (15)`, 112 tests, no eslint output, `✓ built in …`.

- [ ] **Step 11: Commit**

```bash
git add web/src/gates web/src/index.css
git commit -m "feat(web): gate layout, dot field and the two-variant step list"
```

---

### Task 9: The setup gate — passkey, Home Assistant, attention

Decision 4. Three steps, each of which is a real write to a real endpoint:

0. `registerPasskey(defaultDeviceName())` → `rememberDevice(…)`. Registration is trusted-network gated (spec §3.1), so a 403 here means "not at home" and is `AuthGate`'s Denied gate, not this gate's error line.
1. `GET /api/integrations` → the entry with `name === "home-service"` → render `schema.fields` (`url`, then `token`) → `PUT /api/integrations/home-service/credentials`. A **502 means the credentials were stored but could not be pushed** to the service (`web_server.py::_save_service_credentials`) — that is a success with a caveat, so it advances and reports the caveat in the foot line.
2. `GET /api/admin/attention` → one row per domain. `lock`, `alarm_control_panel` and `cover` are filtered out and never shown: *"Locks, alarms and the garage are never on this list; those always come to you."* The step is skipped entirely when there are no rows to show or the store is down (503).

This task also lands `hhmm` in `format.ts` (the step rail, which steps 0 and 1 both show, stamps the registration time on step 1 — as in the handoff prototype) and starts `test/fixtures.ts`.

Three things the gate needs that earlier tasks did not: `failureText` in `auth.ts` — one sentence for a foot line, the server's own words or a written line for the `NotAllowedError`/`AbortError` a cancelled or timed-out Face ID raises (WebKit's message for those is a paragraph about privacy considerations; `SignInGate` reuses it in Task 10) — a `Gate` whose secondary can be disabled and whose foot is a `role="status"` region, and paged attention writes: `AttentionUpdate` caps `allow` and `ask` at 200 entities (`core/channels/admin_api.py`), and a domain's `:seen` set holds every entity that ever changed state, so the sensors alone can pass that. Adding is additive, so a long list goes in pages of 200. Only rows that end up different from how they started are written — a row tapped twice looks untouched, and is — so the attention query is pinned (`staleTime: Infinity`): a refetch on refocus would move that baseline under a choice already made. And the toggle list scrolls under the pinned footer rather than growing the page: a real HA has dozens of domains, not the fixture's six.

**Files:**
- Modify: `web/src/lib/format.ts`, `web/src/lib/format.test.ts`
- Modify: `web/src/lib/auth.ts`, `web/src/lib/auth.test.ts` (add `failureText`)
- Modify: `web/src/gates/Gate.tsx`, `web/src/gates/Gate.test.tsx` (disabled secondary, status foot)
- Create: `web/src/test/fixtures.ts`
- Create: `web/src/gates/SetupGate.tsx`, `web/src/gates/SetupGate.test.tsx`

- [ ] **Step 1: Write the failing `hhmm` test**

Append to `web/src/lib/format.test.ts` (and add `hhmm` to the import at the top):

```ts
describe("hhmm", () => {
  it("formats a Date as a zero-padded local clock time", () => {
    expect(hhmm(new Date(2026, 8, 7, 21, 14))).toBe("21:14");
    expect(hhmm(new Date(2026, 8, 7, 7, 2))).toBe("07:02");
    expect(hhmm(new Date(2026, 8, 7, 0, 0))).toBe("00:00");
  });

  it("accepts an ISO string and an epoch", () => {
    expect(hhmm("2026-09-07T21:14:00")).toBe("21:14");
    expect(hhmm(new Date(2026, 8, 7, 18, 5).getTime())).toBe("18:05");
  });

  it("says so rather than printing NaN", () => {
    expect(hhmm("not a time")).toBe("--:--");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/lib/format.test.ts`
Expected: FAIL — `TypeError: hhmm is not a function` (3 failed, 9 passed): Vitest surfaces a missing named export as `undefined` at call time, not as a module error.

- [ ] **Step 3: Add `hhmm` to `web/src/lib/format.ts`**

Insert at the top of the file, above `summarize`:

```ts
/** `21:14` — the device's own clock, which is the only one the user reads. */
export function hhmm(value: string | number | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--";
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
}
```

Run: `npm test -- src/lib/format.test.ts`
Expected: `Test Files  1 passed (1)`, 12 tests (Task 1's review added one `summarize()` case to this file).

- [ ] **Step 4: Write the failing `failureText` test**

Append to `web/src/lib/auth.test.ts` (add `failureText` to the `./auth` import, and `import { ApiError } from "./api";` above it):

```ts
describe("failureText", () => {
  it("repeats the server's own words", () => {
    expect(failureText(new ApiError(503, "Attention store unavailable"))).toBe(
      "Attention store unavailable",
    );
  });

  it("writes its own line for a cancelled or timed-out Face ID", () => {
    const webkit =
      "The operation either timed out or was not allowed. See: https://www.w3.org/TR/webauthn-2/#sctn-privacy-considerations-client.";
    expect(failureText(new DOMException(webkit, "NotAllowedError"))).toBe("Face ID was cancelled.");
    expect(failureText(new DOMException("Aborted", "AbortError"))).toBe("Face ID was cancelled.");
  });

  it("keeps any other DOMException's message", () => {
    expect(failureText(new DOMException("Already registered", "InvalidStateError"))).toBe(
      "Already registered",
    );
  });

  it("falls back for anything that is not an Error", () => {
    expect(failureText(new Error("Credential creation cancelled"))).toBe(
      "Credential creation cancelled",
    );
    expect(failureText("nope")).toBe("Something went wrong.");
  });
});
```

- [ ] **Step 5: Run it to verify it fails**

Run: `npm test -- src/lib/auth.test.ts`
Expected: FAIL — `TypeError: failureText is not a function` (4 failed, 11 passed).

- [ ] **Step 6: Add `failureText` to `web/src/lib/auth.ts`**

Append to the end of the file:

```ts
/**
 * One sentence for a gate's foot line. The server's own words when it has them
 * (an `ApiError` carries `detail` as its message); a written line for the two
 * DOMExceptions a cancelled or timed-out Face ID raises, whose messages are
 * WebKit's paragraph about privacy considerations, not something Alfred says.
 */
export function failureText(error: unknown): string {
  if (error instanceof DOMException) {
    return error.name === "NotAllowedError" || error.name === "AbortError"
      ? "Face ID was cancelled."
      : error.message;
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}
```

Run: `npm test -- src/lib/auth.test.ts`
Expected: `Test Files  1 passed (1)`, 15 tests.

- [ ] **Step 7: Write the failing Gate tests**

A gate's secondary must be holdable while a write is in flight, and its foot line — where the gates put their errors — must be spoken when it changes. Insert into `web/src/gates/Gate.test.tsx`, directly above the `renders children between the body and the footer` case:

```tsx
  it("disables the secondary when asked", () => {
    render(
      <Gate
        kicker="k"
        title="t"
        secondary={{ label: "Do this later", onClick: () => {}, disabled: true }}
      />,
    );
    expect(screen.getByRole("button", { name: "Do this later" })).toBeDisabled();
  });

  it("speaks the foot line: it is a status region", () => {
    const { rerender } = render(<Gate kicker="k" title="t" foot="Stored encrypted at rest." />);
    expect(screen.getByRole("status")).toHaveTextContent("Stored encrypted at rest.");

    rerender(<Gate kicker="k" title="t" foot="Face ID was cancelled." />);
    expect(screen.getByRole("status")).toHaveTextContent("Face ID was cancelled.");
  });
```

- [ ] **Step 8: Run them to verify they fail**

Run: `npm test -- src/gates/Gate.test.tsx`
Expected: FAIL — `Received element is not disabled`; `Unable to find an accessible element with the role "status"` (2 failed, 7 passed).

- [ ] **Step 9: Update `web/src/gates/Gate.tsx`**

`secondary` becomes an `Omit<GateAction, "busy">` (it can be disabled, never busy), and the foot is a `role="status"` region:

```tsx
import type { ReactNode } from "react";
import { GateField } from "@/gates/GateField";

export interface GateAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
}

export interface GateProps {
  kicker: string;
  title: string;
  body?: string;
  /** A step list, a credential form — anything between the body and the footer. */
  children?: ReactNode;
  primary?: GateAction;
  secondary?: Omit<GateAction, "busy">;
  /**
   * Rendered as a status region: the gates put their errors and caveats here,
   * and a change to it must be spoken, not just painted.
   */
  foot?: string;
}

export function Gate({ kicker, title, body, children, primary, secondary, foot }: GateProps) {
  return (
    <div
      className="relative flex flex-1 flex-col overflow-hidden"
      style={{ background: "var(--bg)", color: "var(--fg)" }}
    >
      <GateField />

      {/* Copy sits at the bottom of the field, not the middle: padding 0 28 12. */}
      <div className="relative flex flex-1 flex-col justify-end gap-3 px-7 pb-3">
        <div className="t-meta">{kicker}</div>
        <h1 className="t-gate">{title}</h1>
        {body ? (
          <p className="t-body" style={{ color: "var(--fg2)" }}>
            {body}
          </p>
        ) : null}
        {children}
      </div>

      <div
        className="relative flex flex-col gap-2.5 px-5 pt-2"
        style={{ paddingBottom: "calc(40px + env(safe-area-inset-bottom, 0px))" }}
      >
        {primary ? (
          <button
            type="button"
            onClick={primary.onClick}
            disabled={primary.disabled === true || primary.busy === true}
            aria-busy={primary.busy === true}
            className="h-14 rounded-[28px] border-0 text-[16px] font-medium disabled:opacity-60"
            style={{ background: "var(--ink)", color: "var(--paper)" }}
          >
            {primary.label}
          </button>
        ) : null}

        {secondary ? (
          <button
            type="button"
            onClick={secondary.onClick}
            disabled={secondary.disabled === true}
            className="h-[50px] rounded-[25px] border-0 bg-transparent text-[15px] font-medium disabled:opacity-60"
            style={{ color: "var(--fg)" }}
          >
            {secondary.label}
          </button>
        ) : null}

        {foot ? (
          <div role="status" className="t-meta text-center">
            {foot}
          </div>
        ) : null}
      </div>
    </div>
  );
}
```

Run: `npm test -- src/gates/Gate.test.tsx`
Expected: `Test Files  1 passed (1)`, 9 tests.

- [ ] **Step 10: Create `web/src/test/fixtures.ts`**

Complete file (Task 13 adds the overview; plan 1b adds stream pages, pending actions and notification frames):

```ts
import type { AttentionDomain, IntegrationInfo } from "@/lib/types";

/**
 * `GET /api/integrations`. Two entries on purpose: the setup gate must find
 * `home-service` by name rather than by position.
 */
export const integrationsFixture: IntegrationInfo[] = [
  {
    name: "weather",
    category: "weather",
    kind: "adapter",
    schema: {
      fields: {
        api_key: {
          label: "API key",
          field_type: "password",
          required: true,
          placeholder: "",
          default: "",
          help_text: "",
          transient: false,
        },
      },
    },
    configured: { api_key: false },
  },
  {
    name: "home-service",
    category: "service",
    kind: "service",
    schema: {
      fields: {
        url: {
          label: "Home Assistant URL",
          field_type: "url",
          required: true,
          placeholder: "",
          default: "http://192.168.1.10:8123",
          help_text: "",
          transient: false,
        },
        token: {
          label: "Access Token",
          field_type: "password",
          required: true,
          placeholder: "",
          default: "",
          help_text: "Long-lived access token from your HA profile page",
          transient: false,
        },
      },
    },
    configured: { url: false, token: false },
  },
];

/**
 * `GET /api/admin/attention`. Three domains the reflex may be trusted with, and
 * the three it may never be: the setup gate must filter those out.
 */
export const attentionFixture: { domains: AttentionDomain[] } = {
  domains: [
    {
      domain: "light",
      members: ["light.hall", "light.kitchen", "light.living_room"],
      seen: [
        "light.hall",
        "light.kitchen",
        "light.living_room",
        "light.study",
        "light.landing",
        "light.porch",
      ],
    },
    {
      domain: "media_player",
      members: ["media_player.tv"],
      seen: ["media_player.tv", "media_player.kitchen"],
    },
    {
      domain: "fan",
      members: [],
      seen: ["fan.bathroom", "fan.study", "switch.desk", "switch.lamp"],
    },
    { domain: "lock", members: [], seen: ["lock.front_door", "lock.back_door"] },
    { domain: "alarm_control_panel", members: [], seen: ["alarm_control_panel.house"] },
    { domain: "cover", members: [], seen: ["cover.garage"] },
  ],
};
```

- [ ] **Step 11: Write the failing SetupGate test**

Create `web/src/gates/SetupGate.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UserEvent } from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { defaultDeviceName, DEVICE_KEY } from "@/lib/auth";
import { hhmm } from "@/lib/format";
import { attentionFixture, integrationsFixture } from "@/test/fixtures";
import { SetupGate } from "./SetupGate";

const { registerPasskeyMock } = vi.hoisted(() => ({ registerPasskeyMock: vi.fn() }));
vi.mock("@/lib/webauthn", () => ({ registerPasskey: registerPasskeyMock }));

interface Call {
  url: string;
  method: string;
  body: unknown;
}

interface Route {
  status?: number;
  body?: unknown;
  /** Never answers — for what the gate does while a write is in flight. */
  pending?: boolean;
}

let calls: Call[] = [];

function stubApi(routes: Record<string, Route>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const route = routes[`${method} ${url}`] ?? routes[url];
      if (!route) return new Response(JSON.stringify({ detail: `unrouted ${method} ${url}` }), { status: 404 });
      if (route.pending) return new Promise<Response>(() => {});
      return new Response(JSON.stringify(route.body ?? {}), { status: route.status ?? 200 });
    }),
  );
}

const HAPPY: Record<string, Route> = {
  "/api/integrations": { body: integrationsFixture },
  "/api/admin/attention": { body: attentionFixture },
  "PUT /api/integrations/home-service/credentials": { body: { status: "ok", pushed: true } },
  "PUT /api/admin/attention/light": { body: { domain: "light", members: [], seen: [] } },
  "PUT /api/admin/attention/fan": { body: { domain: "fan", members: [], seen: [] } },
};

function renderSetup() {
  const onDone = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <SetupGate onDone={onDone} />
    </QueryClientProvider>,
  );
  return { onDone };
}

/** Step 0 → step 1: register the passkey. */
async function register(user: UserEvent): Promise<void> {
  await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));
  await screen.findByRole("heading", { name: "Registered." });
}

beforeEach(() => {
  calls = [];
  registerPasskeyMock.mockReset().mockResolvedValue(undefined);
  vi.stubGlobal("location", { hostname: "alfred.example.com", pathname: "/" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("SetupGate — step 0, the passkey", () => {
  it("greets with the live hostname and the three-step rail", () => {
    stubApi(HAPPY);
    renderSetup();

    expect(screen.getByText("first run · alfred.example.com")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Good evening. I am Alfred." })).toBeInTheDocument();
    expect(
      screen.getByText(
        "This device will hold the only key to the house. There is no password anywhere; a passkey on this phone, unlocked by Face ID, is how you get in.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Connect Home Assistant")).toBeInTheDocument();
    expect(screen.getByText("Choose what the reflex may touch")).toBeInTheDocument();
    expect(
      screen.getByText("The passkey never leaves the phone. Nothing here phones home."),
    ).toBeInTheDocument();
  });

  it("registers with the device's own name, remembers it, and moves on", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();

    await register(user);

    expect(registerPasskeyMock).toHaveBeenCalledWith(defaultDeviceName());
    const remembered = JSON.parse(localStorage.getItem(DEVICE_KEY) ?? "null") as {
      name: string;
      registeredAt: string;
    };
    expect(remembered.name).toBe(defaultDeviceName());
    expect(Number.isNaN(Date.parse(remembered.registeredAt))).toBe(false);
  });

  it("leaves a 403 to the Denied gate: no error line, no advance", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    registerPasskeyMock.mockRejectedValue(new ApiError(403, "Request from untrusted network"));
    renderSetup();

    await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Create passkey with Face ID" })).toBeEnabled(),
    );
    expect(screen.getByRole("heading", { name: "Good evening. I am Alfred." })).toBeInTheDocument();
    expect(screen.queryByText("Request from untrusted network")).toBeNull();
  });

  it("reports a cancelled Face ID in its own words, not WebKit's", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    registerPasskeyMock.mockRejectedValue(
      new DOMException(
        "The operation either timed out or was not allowed. See: https://www.w3.org/TR/webauthn-2/#sctn-privacy-considerations-client.",
        "NotAllowedError",
      ),
    );
    renderSetup();

    await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));

    expect(await screen.findByText("Face ID was cancelled.")).toBeInTheDocument();
    expect(screen.queryByText(/webauthn-2/)).toBeNull();
  });
});

describe("SetupGate — step 1, Home Assistant", () => {
  it("stamps the registration on the rail and moves the current ring on", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();
    await register(user);

    const { registeredAt } = JSON.parse(localStorage.getItem(DEVICE_KEY) ?? "null") as {
      registeredAt: string;
    };
    const registered = screen.getByText(`Register this ${defaultDeviceName()}`).closest("[data-step-state]");
    expect(registered).toHaveAttribute("data-step-state", "done");
    expect(registered).toHaveTextContent(hhmm(registeredAt));
    const connect = screen.getByText("Connect Home Assistant").closest("[data-step-state]");
    expect(connect).toHaveAttribute("data-step-state", "current");
    expect(connect).toHaveAttribute("aria-current", "step");
  });

  it("renders the home-service schema, not the weather adapter's", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();
    await register(user);

    expect(await screen.findByLabelText("Home Assistant URL")).toHaveValue("http://192.168.1.10:8123");
    expect(screen.getByLabelText("Access Token")).toHaveValue("");
    expect(screen.queryByLabelText("API key")).toBeNull();
    expect(
      screen.getByText("Long-lived access token from your HA profile page"),
    ).toBeInTheDocument();
  });

  it("PUTs the edited credentials and advances", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();
    await register(user);

    const token = await screen.findByLabelText("Access Token");
    await user.type(token, "llat-abc123");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByRole("heading", { name: "What may the reflex touch?" });
    const put = calls.find((call) => call.method === "PUT");
    expect(put?.url).toBe("/api/integrations/home-service/credentials");
    expect(put?.body).toEqual({ url: "http://192.168.1.10:8123", token: "llat-abc123" });
  });

  it("advances on a 502 and repeats what the server said", async () => {
    const user = userEvent.setup();
    stubApi({
      ...HAPPY,
      "PUT /api/integrations/home-service/credentials": {
        status: 502,
        body: {
          detail:
            "Credentials stored, but push to home-service failed: connection refused. They will be re-pushed when the service re-registers.",
        },
      },
    });
    renderSetup();
    await register(user);
    await screen.findByLabelText("Access Token");

    await user.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByRole("heading", { name: "What may the reflex touch?" });
    expect(
      screen.getByText(
        "Credentials stored, but push to home-service failed: connection refused. They will be re-pushed when the service re-registers.",
      ),
    ).toBeInTheDocument();
  });

  it("says why the form is empty when the integrations read fails", async () => {
    const user = userEvent.setup();
    stubApi({ ...HAPPY, "/api/integrations": { status: 503, body: { detail: "Keyring locked" } } });
    renderSetup();
    await register(user);

    expect(await screen.findByText("Keyring locked")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  });

  it("holds Do this later while the credentials are being written", async () => {
    const user = userEvent.setup();
    stubApi({ ...HAPPY, "PUT /api/integrations/home-service/credentials": { pending: true } });
    renderSetup();
    await register(user);
    await screen.findByLabelText("Access Token");

    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByRole("button", { name: "Do this later" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  });

  it("skips the write entirely on Do this later", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();
    await register(user);
    await screen.findByLabelText("Access Token");

    await user.click(screen.getByRole("button", { name: "Do this later" }));

    await screen.findByRole("heading", { name: "What may the reflex touch?" });
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });
});

describe("SetupGate — step 2, the attention set", () => {
  async function reachAttention(user: UserEvent): Promise<void> {
    await register(user);
    await screen.findByLabelText("Access Token");
    await user.click(screen.getByRole("button", { name: "Do this later" }));
    await screen.findByRole("heading", { name: "What may the reflex touch?" });
  }

  it("lists what the reflex may be trusted with, and never the locks", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    renderSetup();
    await reachAttention(user);

    expect(screen.getByRole("button", { name: /Light · 6 found/ })).toHaveTextContent("allowed");
    expect(screen.getByRole("button", { name: /Media player · 2 found/ })).toHaveTextContent(
      "allowed",
    );
    expect(screen.getByRole("button", { name: /Fan · 4 found/ })).toHaveTextContent("ask me");

    expect(screen.queryByText(/Lock ·/)).toBeNull();
    expect(screen.queryByText(/Alarm control panel ·/)).toBeNull();
    expect(screen.queryByText(/Cover ·/)).toBeNull();
  });

  it("writes only the rows the user touched, in the right direction", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    const { onDone } = renderSetup();
    await reachAttention(user);

    await user.click(screen.getByRole("button", { name: /Fan · 4 found/ }));
    await user.click(screen.getByRole("button", { name: /Light · 6 found/ }));
    await user.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    const puts = calls.filter((call) => call.url.startsWith("/api/admin/attention/"));
    expect(puts).toHaveLength(2);
    expect(puts.find((call) => call.url.endsWith("/fan"))?.body).toEqual({
      allow: ["fan.bathroom", "fan.study", "switch.desk", "switch.lamp"],
    });
    expect(puts.find((call) => call.url.endsWith("/light"))?.body).toEqual({
      ask: ["light.hall", "light.kitchen", "light.living_room"],
    });
  });

  it("writes nothing for a row tapped back to where it started", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    const { onDone } = renderSetup();
    await reachAttention(user);

    await user.click(screen.getByRole("button", { name: /Light · 6 found/ }));
    await user.click(screen.getByRole("button", { name: /Light · 6 found/ }));
    expect(screen.getByRole("button", { name: /Light · 6 found/ })).toHaveTextContent("allowed");
    await user.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(calls.filter((call) => call.url.startsWith("/api/admin/attention/"))).toHaveLength(0);
  });

  it("pages a long list through the endpoint's 200-entity cap", async () => {
    const user = userEvent.setup();
    const seen = Array.from({ length: 250 }, (_, index) => `sensor.s${index}`);
    stubApi({
      ...HAPPY,
      "/api/admin/attention": {
        body: { domains: [...attentionFixture.domains, { domain: "sensor", members: [], seen }] },
      },
      "PUT /api/admin/attention/sensor": { body: { domain: "sensor", members: [], seen: [] } },
    });
    const { onDone } = renderSetup();
    await reachAttention(user);

    await user.click(screen.getByRole("button", { name: /Sensor · 250 found/ }));
    await user.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    const puts = calls.filter((call) => call.url === "/api/admin/attention/sensor");
    expect(puts.map((call) => (call.body as { allow: string[] }).allow.length)).toEqual([200, 50]);
    expect(puts.flatMap((call) => (call.body as { allow: string[] }).allow)).toEqual(seen);
  });

  it("scrolls a long list under the pinned footer", async () => {
    const user = userEvent.setup();
    const domains = Array.from({ length: 30 }, (_, index) => ({
      domain: `domain_${index}`,
      members: [],
      seen: [`domain_${index}.one`],
    }));
    stubApi({ ...HAPPY, "/api/admin/attention": { body: { domains } } });
    renderSetup();
    await reachAttention(user);

    expect(screen.getAllByRole("button", { name: /· 1 found/ })).toHaveLength(30);
    expect(screen.getByRole("list").parentElement).toHaveClass("overflow-y-auto");
  });

  it("keeps its baseline when the app is refocused before Finish", async () => {
    const user = userEvent.setup();
    const routes = { ...HAPPY };
    stubApi(routes);
    const { onDone } = renderSetup();
    await reachAttention(user);
    await user.click(screen.getByRole("button", { name: /Fan · 4 found/ }));

    // The reflex seeds fan meanwhile; a refetch would now say the row started allowed.
    routes["/api/admin/attention"] = {
      body: {
        domains: attentionFixture.domains.map((row) =>
          row.domain === "fan" ? { ...row, members: ["fan.bathroom"] } : row,
        ),
      },
    };
    window.dispatchEvent(new Event("visibilitychange"));
    await user.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(calls.filter((call) => call.url === "/api/admin/attention")).toHaveLength(1);
    expect(calls.filter((call) => call.url === "/api/admin/attention/fan")).toEqual([
      {
        url: "/api/admin/attention/fan",
        method: "PUT",
        body: { allow: ["fan.bathroom", "fan.study", "switch.desk", "switch.lamp"] },
      },
    ]);
  });

  it("finishes with no writes when nothing was touched", async () => {
    const user = userEvent.setup();
    stubApi(HAPPY);
    const { onDone } = renderSetup();
    await reachAttention(user);

    await user.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(calls.filter((call) => call.url.startsWith("/api/admin/attention/"))).toHaveLength(0);
  });

  it("skips the step when no domain has been seen yet", async () => {
    const user = userEvent.setup();
    stubApi({ ...HAPPY, "/api/admin/attention": { body: { domains: [] } } });
    const { onDone } = renderSetup();
    await register(user);
    await screen.findByLabelText("Access Token");
    await user.click(screen.getByRole("button", { name: "Do this later" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "Finish" })).toBeNull();
  });

  it("skips once, even when the parent hands it a new onDone every render", async () => {
    const user = userEvent.setup();
    stubApi({ ...HAPPY, "/api/admin/attention": { body: { domains: [] } } });
    const spy = vi.fn();
    // What AuthGate does: an inline closure, so `onDone` is new on every render.
    function Parent() {
      const [renders, setRenders] = useState(0);
      return (
        <SetupGate
          onDone={() => {
            spy();
            if (renders < 3) setRenders(renders + 1);
          }}
        />
      );
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <Parent />
      </QueryClientProvider>,
    );
    await register(user);
    await screen.findByLabelText("Access Token");
    await user.click(screen.getByRole("button", { name: "Do this later" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("skips the step when the attention store is down", async () => {
    const user = userEvent.setup();
    stubApi({
      ...HAPPY,
      "/api/admin/attention": { status: 503, body: { detail: "Attention store unavailable" } },
    });
    const { onDone } = renderSetup();
    await register(user);
    await screen.findByLabelText("Access Token");
    await user.click(screen.getByRole("button", { name: "Do this later" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  });

  it("reports a failed write and stays put", async () => {
    const user = userEvent.setup();
    stubApi({
      ...HAPPY,
      "PUT /api/admin/attention/fan": { status: 503, body: { detail: "Attention store unavailable" } },
    });
    const { onDone } = renderSetup();
    await reachAttention(user);

    await user.click(screen.getByRole("button", { name: /Fan · 4 found/ }));
    await user.click(screen.getByRole("button", { name: "Finish" }));

    expect(await screen.findByText("Attention store unavailable")).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 12: Run it to verify it fails**

Run: `npm test -- src/gates/SetupGate.test.tsx`
Expected: FAIL — `Failed to resolve import "./SetupGate"`.

- [ ] **Step 13: Write `web/src/gates/SetupGate.tsx`**

Complete file:

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Gate } from "@/gates/Gate";
import { StepList, type ProgressStep } from "@/gates/StepList";
import { api, ApiError, put } from "@/lib/api";
import { defaultDeviceName, failureText, rememberDevice } from "@/lib/auth";
import { hhmm } from "@/lib/format";
import type { AttentionDomain, IntegrationInfo } from "@/lib/types";
import { registerPasskey } from "@/lib/webauthn";

const HOME_SERVICE = "home-service";

/**
 * "Locks, alarms and the garage are never on this list; those always come to
 * you." Filtered in the client as well as trusted to the backend's risk tiers:
 * an offer to automate a lock is the wrong thing to render, whatever happens next.
 */
const NEVER_AUTOMATIC = new Set(["lock", "alarm_control_panel", "cover"]);

/**
 * `AttentionUpdate` caps `allow` and `ask` at 200 entities (admin_api.py), and a
 * domain's `:seen` set holds every entity that ever changed state — the sensors
 * alone can pass that. Adding is additive, so a long list goes in pages.
 */
const MAX_ENTITIES_PER_PUT = 200;

function pages<T>(list: T[]): T[][] {
  const out: T[][] = [];
  for (let start = 0; start < list.length; start += MAX_ENTITIES_PER_PUT) {
    out.push(list.slice(start, start + MAX_ENTITIES_PER_PUT));
  }
  return out;
}

/** `media_player` → `Media player`. */
function domainLabel(domain: string): string {
  const words = domain.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function progressSteps(step: number, deviceName: string, registeredAt: string | null): ProgressStep[] {
  const labels = [
    `Register this ${deviceName}`,
    "Connect Home Assistant",
    "Choose what the reflex may touch",
  ];
  return labels.map((label, index) => ({
    label,
    meta: index === 0 && registeredAt ? hhmm(registeredAt) : undefined,
    state: index < step ? "done" : index === step ? "current" : "todo",
  }));
}

export interface SetupGateProps {
  /** Called once the last step is answered — AuthGate refetches from here. */
  onDone: () => void;
}

export function SetupGate({ onDone }: SetupGateProps) {
  // Read once, in an initialiser: neither the hostname nor the UA changes while
  // the gate is open, and reading them in render is impure.
  const [deviceName] = useState(() => defaultDeviceName());
  const [hostname] = useState(() => location.hostname);

  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [footOverride, setFootOverride] = useState<string | null>(null);
  const [registeredAt, setRegisteredAt] = useState<string | null>(null);
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [allowed, setAllowed] = useState<Record<string, boolean>>({});

  // Both reads start as soon as the passkey exists, so step 2's skip decision is
  // already settled by the time the user presses Continue.
  const integrations = useQuery<IntegrationInfo[]>({
    queryKey: ["integrations"],
    queryFn: () => api<IntegrationInfo[]>("/api/integrations"),
    enabled: step >= 1,
    // A failure here is shown in the foot line, not retried behind a dead button.
    retry: false,
  });

  const attention = useQuery<{ domains: AttentionDomain[] }>({
    queryKey: ["attention"],
    queryFn: () => api<{ domains: AttentionDomain[] }>("/api/admin/attention"),
    enabled: step >= 1,
    // 503 is "the store is down", which is a skip, not something to retry at.
    retry: false,
    // `finish()` writes the difference between the toggles and this data. A
    // refetch — the app backgrounded and refocused before Finish — would move
    // the baseline under a choice already made, and a grant could go unwritten.
    staleTime: Infinity,
  });

  const homeService = integrations.data?.find((entry) => entry.name === HOME_SERVICE) ?? null;
  const fields = useMemo(
    () => (homeService ? Object.entries(homeService.schema.fields) : []),
    [homeService],
  );

  const rows = useMemo(
    () => (attention.data?.domains ?? []).filter((row) => !NEVER_AUTOMATIC.has(row.domain)),
    [attention.data],
  );

  // `allowed` holds only the rows the user has touched. Until then a domain the
  // reflex already acts in reads as allowed, and everything else asks.
  const startsAllowed = (row: AttentionDomain): boolean => row.members.length > 0;
  const isAllowed = (row: AttentionDomain): boolean => allowed[row.domain] ?? startsAllowed(row);

  // Nothing to choose between (or nothing to choose from): the step does not exist.
  // Latched: the parent's `onDone` is an inline closure that changes identity
  // when it re-renders, and this must not fire again on that account.
  const skipped = useRef(false);
  useEffect(() => {
    if (step !== 2 || attention.isPending || rows.length > 0 || skipped.current) return;
    skipped.current = true;
    onDone();
  }, [step, attention.isPending, rows.length, onDone]);

  async function register(): Promise<void> {
    setBusy(true);
    setFootOverride(null);
    try {
      await registerPasskey(deviceName);
      const at = new Date().toISOString();
      rememberDevice({ name: deviceName, registeredAt: at });
      setRegisteredAt(at);
      setStep(1);
    } catch (error) {
      // 403 = off the house network. api() has already raised the Denied gate over
      // this one; repeating it in the foot would be the same news, twice.
      if (error instanceof ApiError && error.status === 403) return;
      setFootOverride(failureText(error));
    } finally {
      setBusy(false);
    }
  }

  async function saveCredentials(): Promise<void> {
    setBusy(true);
    setFootOverride(null);
    const body: Record<string, string> = {};
    for (const [key, field] of fields) body[key] = credentials[key] ?? field.default;
    try {
      await put(`/api/integrations/${HOME_SERVICE}/credentials`, body);
      setStep(2);
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) return;
      if (error instanceof ApiError && error.status === 502) {
        // Stored in the keyring, but the service was unreachable for the push. The
        // credentials are safe and will be re-pushed on the next registration, so
        // this advances — carrying the server's own sentence forward.
        setFootOverride(error.detail);
        setStep(2);
        return;
      }
      setFootOverride(failureText(error));
    } finally {
      setBusy(false);
    }
  }

  async function finish(): Promise<void> {
    setBusy(true);
    setFootOverride(null);
    try {
      // Only the rows that end up different from how they started are written:
      // a row tapped twice looks untouched, and is.
      for (const row of rows) {
        const allow = isAllowed(row);
        if (allow === startsAllowed(row)) continue;
        // `allow` adds what has been seen; `ask` removes what is a member today —
        // and the removal is sticky, so the YAML seed will not re-add it.
        for (const page of pages(allow ? row.seen : row.members)) {
          await put(`/api/admin/attention/${row.domain}`, allow ? { allow: page } : { ask: page });
        }
      }
      onDone();
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) return;
      setFootOverride(failureText(error));
    } finally {
      setBusy(false);
    }
  }

  function toggleDomain(domain: string): void {
    const row = rows.find((candidate) => candidate.domain === domain);
    if (!row) return;
    setAllowed((current) => ({ ...current, [domain]: !(current[domain] ?? startsAllowed(row)) }));
  }

  if (step === 0) {
    return (
      <Gate
        kicker={`first run · ${hostname}`}
        title="Good evening. I am Alfred."
        body="This device will hold the only key to the house. There is no password anywhere; a passkey on this phone, unlocked by Face ID, is how you get in."
        primary={{ label: "Create passkey with Face ID", onClick: () => void register(), busy }}
        foot={footOverride ?? "The passkey never leaves the phone. Nothing here phones home."}
      >
        <StepList variant="progress" steps={progressSteps(0, deviceName, registeredAt)} />
      </Gate>
    );
  }

  if (step === 1) {
    return (
      <Gate
        kicker="first run · 1 of 3 done"
        title="Registered."
        body="Now the house. Paste a long-lived Home Assistant token; I will read state and, later, act on the devices you allow."
        primary={{
          label: "Continue",
          onClick: () => void saveCredentials(),
          busy,
          disabled: fields.length === 0,
        }}
        secondary={{
          label: "Do this later",
          onClick: () => {
            setFootOverride(null);
            setStep(2);
          },
          // Walking away mid-write would leave the write to land on step 2's foot.
          disabled: busy,
        }}
        foot={
          footOverride ??
          (integrations.isError
            ? failureText(integrations.error)
            : "Stored encrypted at rest on your hardware.")
        }
      >
        <StepList variant="progress" steps={progressSteps(1, deviceName, registeredAt)} />
        <div className="flex flex-col gap-3 pt-2">
          {fields.map(([key, field]) => (
            // The help text sits beside the label, not inside it, so the field's
            // accessible name is exactly `field.label`.
            <div key={key} className="flex flex-col gap-1.5">
              <label className="flex flex-col gap-1.5">
                <span className="t-label">{field.label}</span>
                <input
                  type={
                    field.field_type === "password"
                      ? "password"
                      : field.field_type === "url"
                        ? "url"
                        : "text"
                  }
                  value={credentials[key] ?? field.default}
                  placeholder={field.placeholder}
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  aria-describedby={field.help_text ? `${key}-help` : undefined}
                  onChange={(event) =>
                    setCredentials((current) => ({ ...current, [key]: event.target.value }))
                  }
                  className="h-[50px] rounded-[25px] border px-[18px] text-[15px]"
                  style={{
                    background: "var(--field)",
                    borderColor: "var(--line)",
                    color: "var(--fg)",
                  }}
                />
              </label>
              {field.help_text ? (
                <span id={`${key}-help`} className="t-meta">
                  {field.help_text}
                </span>
              ) : null}
            </div>
          ))}
        </div>
      </Gate>
    );
  }

  // Skipping (see the effect above): nothing to draw while the parent takes over,
  // rather than a one-frame flash of an empty step.
  if (!attention.isPending && rows.length === 0) return null;

  return (
    <Gate
      kicker="first run · 2 of 3 done"
      title="What may the reflex touch?"
      body="The small local model reacts in under half a second. It is only allowed low-risk devices you list here. Locks, alarms and the garage are never on this list; those always come to you."
      primary={{
        label: "Finish",
        onClick: () => void finish(),
        busy,
        disabled: attention.isPending,
      }}
      foot={footOverride ?? "Change this any time under Workshop › System."}
    >
      {/* One row per domain the house has emitted — dozens on a real HA, not the
          handful in the fixture — so the list scrolls under the pinned footer
          rather than growing the page (the gate does not rubber-band, §4.6). */}
      <div className="max-h-[40dvh] overflow-y-auto overscroll-contain">
        <StepList
          variant="toggle"
          onToggle={toggleDomain}
          steps={rows.map((row) => ({
            id: row.domain,
            label: `${domainLabel(row.domain)} · ${row.seen.length} found`,
            allowed: isAllowed(row),
          }))}
        />
      </div>
    </Gate>
  );
}
```

Two notes on the copy. The rail's first row reads `Register this ${deviceName}`, which is the handoff's `Register this iPhone` verbatim on the device the design was drawn for, and honest on anything else. The step-2 foot still points at `Workshop › System`, which ships in Phase 2 — deliberate, per the deviations table.

- [ ] **Step 14: Run the SetupGate test**

Run: `npm test -- src/gates/SetupGate.test.tsx`
Expected: `Test Files  1 passed (1)`, 22 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  16 passed (16)`, 143 tests, no eslint output, `✓ built in …`.

- [ ] **Step 15: Commit**

```bash
git add web/src/gates web/src/test/fixtures.ts web/src/lib
git commit -m "feat(web): first-run setup gate — passkey, Home Assistant, attention"
```

---

### Task 10: Sign-in, expired, denied — and the gate that chooses between them

`AuthGate` is the whole identity routing table in one component:

| `["auth-status"]` | What renders |
|---|---|
| pending | the bare dot field, no copy — there is nothing true to say yet |
| `registered: false` | `SetupGate`, and it stays until `onDone` whatever a later refetch says |
| no data (a first read that errored) | `SignInGate` — fail closed; a failed *refetch* keeps the last good status, and the room |
| `registered, !authenticated` | `SignInGate` |
| `registered, authenticated` | the children (the Room) |

On top of that, two events. `expired` raises `ExpiredGate` **over** the children, so the last-known Room is still behind it; signing in dismisses it and refetches everything. `denied` raises `DeniedGate`, which only offers `Back to the room`. Both event gates render outside the routing table, over whichever branch is up: a 403 is as true on the setup gate as in the room, and `SetupGate` swallows its 403s on the promise that this gate has said it. Three details the tests pin: when setup finishes, the gate publishes `{ registered: true, authenticated: true }` into the cache before refetching, or the render-time latch would re-arm on the stale `registered: false` and setup would never end; the `expired` latch is cleared in render whenever the status says signed-out, so a gate raised over the room cannot outlive the room and reappear after the next sign-in; and only a *first* read that errors fails closed — a failed background refetch keeps the last good status, and the room with it.

**Files:**
- Modify: `web/src/lib/format.ts`, `web/src/lib/format.test.ts` (add `dayMonth`)
- Modify: `web/src/lib/auth.ts`, `web/src/lib/auth.test.ts` (add `deviceFootLine`)
- Create: `web/src/gates/SignInGate.tsx`, `web/src/gates/ExpiredGate.tsx`, `web/src/gates/DeniedGate.tsx`, `web/src/gates/AuthGate.tsx`, `web/src/gates/AuthGate.test.tsx`

- [ ] **Step 1: Write the failing `dayMonth` and `deviceFootLine` tests**

Append to `web/src/lib/format.test.ts` (add `dayMonth` to the import):

```ts
describe("dayMonth", () => {
  it("formats as the design writes it", () => {
    expect(dayMonth(new Date(2026, 7, 12))).toBe("12 Aug");
    expect(dayMonth(new Date(2026, 8, 4))).toBe("4 Sep");
    expect(dayMonth(new Date(2026, 0, 1))).toBe("1 Jan");
  });
});
```

Append to `web/src/lib/auth.test.ts` (add `deviceFootLine` to the import):

```ts
describe("deviceFootLine", () => {
  it("names the remembered passkey and when it was made", () => {
    expect(
      deviceFootLine({ name: "iPhone", registeredAt: new Date(2026, 7, 12, 7, 2).toISOString() }),
    ).toBe("Passkey · iPhone · registered 12 Aug");
  });

  it("says as little as it knows when nothing is remembered", () => {
    expect(deviceFootLine(null)).toBe("Passkey · this phone");
  });

  it("drops the date rather than printing an invalid one", () => {
    expect(deviceFootLine({ name: "iPad", registeredAt: "whenever" })).toBe("Passkey · iPad");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npm test -- src/lib/format.test.ts src/lib/auth.test.ts`
Expected: FAIL — `TypeError: dayMonth is not a function` and `TypeError: deviceFootLine is not a function` (4 failed): Vitest surfaces a missing named export as `undefined` at call time, not as a module error.

- [ ] **Step 3: Add the two helpers**

Insert into `web/src/lib/format.ts`, directly below `hhmm`:

```ts
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * `12 Aug`. Written out rather than `toLocaleDateString("en-GB")`, whose short
 * September became "Sept" in CLDR 42 — the design says `4 Sep`, on every ICU.
 */
export function dayMonth(date: Date): string {
  return `${date.getDate()} ${MONTHS[date.getMonth()]}`;
}
```

Append to `web/src/lib/auth.ts`, and add `import { dayMonth } from "./format";` after the `./api` import at the top:

```ts
/**
 * The sign-in gate's foot line. `GET /api/auth/status` says nothing about the
 * device before you are authenticated — correct on a public host — so this is
 * the client's own memory, and it says so vaguely when it has none.
 */
export function deviceFootLine(device: RememberedDevice | null): string {
  if (!device) return "Passkey · this phone";
  const at = new Date(device.registeredAt);
  if (Number.isNaN(at.getTime())) return `Passkey · ${device.name}`;
  return `Passkey · ${device.name} · registered ${dayMonth(at)}`;
}
```

with `import { dayMonth } from "./format";` added at the top of `auth.ts`.

Run: `npm test -- src/lib/format.test.ts src/lib/auth.test.ts`
Expected: both pass — 13 and 18 tests (the Task 1 review added one `summarize()` case to `format.test.ts`; Tasks 7 and 9 grew `auth.test.ts`).

- [ ] **Step 4: Write the failing AuthGate test**

Create `web/src/gates/AuthGate.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { authEvents } from "@/lib/auth-events";
import { DEVICE_KEY } from "@/lib/auth";
import type { AuthStatus } from "@/lib/types";
import { AuthGate } from "./AuthGate";

const { loginPasskeyMock, registerPasskeyMock } = vi.hoisted(() => ({
  loginPasskeyMock: vi.fn(),
  registerPasskeyMock: vi.fn(),
}));
vi.mock("@/lib/webauthn", () => ({
  loginPasskey: loginPasskeyMock,
  registerPasskey: registerPasskeyMock,
}));

let status: AuthStatus = { registered: true, authenticated: true };
/** When set, the status read 500s — the store behind it is down. */
let statusDown = false;

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/auth/status") {
        if (statusDown) return new Response('{"detail":"redis is down"}', { status: 500 });
        return new Response(JSON.stringify(status), { status: 200 });
      }
      if (url === "/api/integrations") return new Response("[]", { status: 200 });
      if (url === "/api/admin/attention") return new Response('{"domains":[]}', { status: 200 });
      return new Response("{}", { status: 200 });
    }),
  );
}

function renderGate() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <AuthGate>
        <main>the room</main>
      </AuthGate>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

beforeEach(() => {
  status = { registered: true, authenticated: true };
  statusDown = false;
  loginPasskeyMock.mockReset().mockResolvedValue(undefined);
  registerPasskeyMock.mockReset().mockResolvedValue(undefined);
  vi.stubGlobal("location", { hostname: "alfred.example.com", pathname: "/" });
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("AuthGate routing", () => {
  it("shows a bare field, and no copy, while the status is unknown", () => {
    const { container } = renderGate();
    expect(container.querySelector(".gate-field")).not.toBeNull();
    expect(screen.queryByText("the room")).toBeNull();
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("sends an unregistered house to setup", async () => {
    status = { registered: false, authenticated: false };
    renderGate();
    expect(
      await screen.findByRole("heading", { name: "Good evening. I am Alfred." }),
    ).toBeInTheDocument();
  });

  it("lets a finished setup through to the room", async () => {
    const user = userEvent.setup();
    status = { registered: false, authenticated: false };
    renderGate();
    await screen.findByRole("heading", { name: "Good evening. I am Alfred." });

    // Registering is what makes the server say so; the cached status still says
    // unregistered until the gate publishes the new truth.
    status = { registered: true, authenticated: true };
    await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));
    await screen.findByRole("heading", { name: "Registered." });
    // No integrations to fill in and no attention rows: setup skips straight out.
    await user.click(screen.getByRole("button", { name: "Do this later" }));

    expect(await screen.findByText("the room")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Good evening. I am Alfred." })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Welcome back, sir." })).toBeNull();
  });

  it("raises the denied gate over setup when registration is off-network", async () => {
    const user = userEvent.setup();
    status = { registered: false, authenticated: false };
    // What `api()` does with a 403: says so on the bus, then throws.
    registerPasskeyMock.mockImplementation(async () => {
      authEvents.emit("denied");
      throw new ApiError(403, "Not from here");
    });
    renderGate();
    await screen.findByRole("heading", { name: "Good evening. I am Alfred." });

    await user.click(screen.getByRole("button", { name: "Create passkey with Face ID" }));

    expect(await screen.findByRole("heading", { name: "Not from here." })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Back to the room" }));
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Not from here." })).toBeNull(),
    );
    // Setup is still where it was, and did not repeat the news in its foot line.
    expect(screen.getByRole("heading", { name: "Good evening. I am Alfred." })).toBeInTheDocument();
    expect(screen.queryByText("Not from here")).toBeNull();
  });

  it("sends a registered but signed-out device to sign-in", async () => {
    status = { registered: true, authenticated: false };
    localStorage.setItem(
      DEVICE_KEY,
      JSON.stringify({ name: "iPhone", registeredAt: new Date(2026, 7, 12).toISOString() }),
    );
    renderGate();

    expect(await screen.findByRole("heading", { name: "Welcome back, sir." })).toBeInTheDocument();
    expect(screen.getByText("alfred.example.com · signed out")).toBeInTheDocument();
    expect(screen.getByText("Passkey · iPhone · registered 12 Aug")).toBeInTheDocument();
    expect(screen.queryByText("the room")).toBeNull();
  });

  it("falls back to a vague foot line with no remembered device", async () => {
    status = { registered: true, authenticated: false };
    renderGate();
    expect(await screen.findByText("Passkey · this phone")).toBeInTheDocument();
  });

  it("lets an authenticated device through to the room", async () => {
    renderGate();
    expect(await screen.findByText("the room")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Welcome back, sir." })).toBeNull();
  });

  it("fails closed to sign-in when the status read errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"detail":"redis is down"}', { status: 500 })),
    );
    renderGate();
    expect(await screen.findByRole("heading", { name: "Welcome back, sir." })).toBeInTheDocument();
  });

  it("keeps the room when a background read of the status fails", async () => {
    const { client } = renderGate();
    await screen.findByText("the room");

    statusDown = true;
    await act(() => client.invalidateQueries({ queryKey: ["auth-status"] }));
    await waitFor(() => expect(client.getQueryState(["auth-status"])?.status).toBe("error"));

    // Nothing should change on screen, so give the observer its tick and look.
    await act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));
    expect(screen.getByText("the room")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Welcome back, sir." })).toBeNull();
  });

  it("signs in with the passkey and reveals the room", async () => {
    const user = userEvent.setup();
    status = { registered: true, authenticated: false };
    renderGate();
    await screen.findByRole("heading", { name: "Welcome back, sir." });

    status = { registered: true, authenticated: true };
    await user.click(screen.getByRole("button", { name: "Sign in with Face ID" }));

    expect(loginPasskeyMock).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("the room")).toBeInTheDocument();
  });

  it("reports a failed sign-in in the foot line, in its own words", async () => {
    const user = userEvent.setup();
    status = { registered: true, authenticated: false };
    loginPasskeyMock.mockRejectedValue(
      new DOMException("The operation either timed out or was not allowed.", "NotAllowedError"),
    );
    renderGate();
    await screen.findByRole("heading", { name: "Welcome back, sir." });

    await user.click(screen.getByRole("button", { name: "Sign in with Face ID" }));

    expect(await screen.findByText("Face ID was cancelled.")).toBeInTheDocument();
  });
});

describe("AuthGate events", () => {
  it("raises the expired gate over the room, with the real eight-hour TTL", async () => {
    renderGate();
    await screen.findByText("the room");

    act(() => authEvents.emit("expired"));

    expect(screen.getByRole("heading", { name: "Your session lapsed." })).toBeInTheDocument();
    expect(
      screen.getByText(
        "The passkey session on this phone ran out after eight hours. Anything below is last-known until you sign in again.",
      ),
    ).toBeInTheDocument();
    // The room is still mounted underneath — that is the whole point.
    expect(screen.getByText("the room")).toBeInTheDocument();
  });

  it("dismisses the expired gate once the passkey signs in again", async () => {
    const user = userEvent.setup();
    renderGate();
    await screen.findByText("the room");
    act(() => authEvents.emit("expired"));

    await user.click(screen.getByRole("button", { name: "Sign in with Face ID" }));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Your session lapsed." })).toBeNull(),
    );
  });

  it("drops the expired gate when the status says signed out, for good", async () => {
    const user = userEvent.setup();
    const { client } = renderGate();
    await screen.findByText("the room");
    act(() => authEvents.emit("expired"));
    expect(screen.getByRole("heading", { name: "Your session lapsed." })).toBeInTheDocument();

    // A refetch (the app refocused) confirms it: the sign-in gate takes over.
    status = { registered: true, authenticated: false };
    await act(() => client.invalidateQueries({ queryKey: ["auth-status"] }));
    expect(await screen.findByRole("heading", { name: "Welcome back, sir." })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Your session lapsed." })).toBeNull(),
    );

    status = { registered: true, authenticated: true };
    await user.click(screen.getByRole("button", { name: "Sign in with Face ID" }));

    expect(await screen.findByText("the room")).toBeInTheDocument();
    // The gate stays down: the latch went with the room it was raised over.
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Your session lapsed." })).toBeNull(),
    );
  });

  it("raises the denied gate, which only offers a way back", async () => {
    const user = userEvent.setup();
    renderGate();
    await screen.findByText("the room");

    act(() => authEvents.emit("denied"));

    expect(screen.getByRole("heading", { name: "Not from here." })).toBeInTheDocument();
    expect(screen.getByText("403 · off-network")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign in with Face ID" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Back to the room" }));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Not from here." })).toBeNull(),
    );
    expect(screen.getByText("the room")).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: Run it to verify it fails**

Run: `npm test -- src/gates/AuthGate.test.tsx`
Expected: FAIL — `Failed to resolve import "./AuthGate"`.

- [ ] **Step 6: Write the three gates**

Complete `web/src/gates/SignInGate.tsx`:

```tsx
import { useState } from "react";
import { Gate } from "@/gates/Gate";
import { deviceFootLine, failureText, rememberedDevice } from "@/lib/auth";
import { loginPasskey } from "@/lib/webauthn";

export interface SignInGateProps {
  onSignedIn: () => void;
}

export function SignInGate({ onSignedIn }: SignInGateProps) {
  const [hostname] = useState(() => location.hostname);
  const [device] = useState(() => rememberedDevice());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signIn(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await loginPasskey();
      onSignedIn();
    } catch (caught) {
      // Login is deliberately *not* network-gated (spec §3.1): enrol at home, sign
      // in from anywhere. So a failure here is a cancelled Face ID or a real 4xx,
      // and the server's own words are the most useful thing to show.
      setError(failureText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Gate
      kicker={`${hostname} · signed out`}
      title="Welcome back, sir."
      body="Face ID unlocks the passkey on this phone. Nothing is sent but the signed challenge."
      primary={{ label: "Sign in with Face ID", onClick: () => void signIn(), busy }}
      foot={error ?? deviceFootLine(device)}
    />
  );
}
```

Complete `web/src/gates/ExpiredGate.tsx`:

```tsx
import { useState } from "react";
import { Gate } from "@/gates/Gate";
import { failureText } from "@/lib/auth";
import { loginPasskey } from "@/lib/webauthn";

export interface ExpiredGateProps {
  onSignedIn: () => void;
}

/**
 * 401. Raised over whatever was on screen, never instead of it — the room behind
 * this gate is still the last thing that was true.
 *
 * The body deviates from the handoff on purpose: the prototype says "30 days",
 * and phase 0 cut the session TTL to eight hours (`_AUTH_SESSION_TTL`).
 */
export function ExpiredGate({ onSignedIn }: ExpiredGateProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signIn(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await loginPasskey();
      onSignedIn();
    } catch (caught) {
      setError(failureText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Gate
      kicker="401 · session lapsed"
      title="Your session lapsed."
      body="The passkey session on this phone ran out after eight hours. Anything below is last-known until you sign in again."
      primary={{ label: "Sign in with Face ID", onClick: () => void signIn(), busy }}
      foot={error ?? "Conversations and settings are on the server; nothing is lost."}
    />
  );
}
```

Complete `web/src/gates/DeniedGate.tsx`:

```tsx
import { Gate } from "@/gates/Gate";
import { hhmm } from "@/lib/format";

export interface DeniedGateProps {
  /** When the house was last reachable; omitted before any connection has been made. */
  lastTrue?: Date | null;
  onDismiss: () => void;
}

/**
 * 403. Registration and credential writes are home-network-only (spec §3.1), so
 * this is what "you are not at home" looks like. There is nothing to retry and
 * nothing to sign in to — only a way back to what is already on screen.
 */
export function DeniedGate({ lastTrue, onDismiss }: DeniedGateProps) {
  const foot = lastTrue
    ? `Last true ${hhmm(lastTrue)} · everything shown behind this is last-known`
    : "Everything shown behind this is last-known";

  return (
    <Gate
      kicker="403 · off-network"
      title="Not from here."
      body="Alfred only answers requests from inside the house network or over its own tunnel. This connection is neither, so the house declined it. Nothing was sent."
      secondary={{ label: "Back to the room", onClick: onDismiss }}
      foot={foot}
    />
  );
}
```

- [ ] **Step 7: Write `web/src/gates/AuthGate.tsx`**

Complete file:

```tsx
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DeniedGate } from "@/gates/DeniedGate";
import { ExpiredGate } from "@/gates/ExpiredGate";
import { GateField } from "@/gates/GateField";
import { SetupGate } from "@/gates/SetupGate";
import { SignInGate } from "@/gates/SignInGate";
import { fetchAuthStatus } from "@/lib/auth";
import { authEvents } from "@/lib/auth-events";
import type { AuthStatus } from "@/lib/types";
import { Layer } from "@/shell/Layer";

export function AuthGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({
    queryKey: ["auth-status"],
    queryFn: fetchAuthStatus,
  });

  const [setupActive, setSetupActive] = useState(false);
  const [expired, setExpired] = useState(false);
  const [denied, setDenied] = useState(false);

  useEffect(() => authEvents.on("expired", () => setExpired(true)), []);
  useEffect(() => authEvents.on("denied", () => setDenied(true)), []);

  const needsSetup = data !== undefined && !data.registered;

  // Registration authenticates the session, so the next refetch of auth-status
  // says "registered, authenticated" while the user is still on step 1. Latching
  // keeps the gate mounted until it says it is finished. Set during render, the
  // way react.dev documents for state that remembers a previous render.
  if (needsSetup && !setupActive) setSetupActive(true);

  // A gate raised over the room must not outlive the room: if the status comes
  // back signed-out while the expired gate is up, the sign-in gate takes over,
  // and a latch left set here would cover the room again the moment it returned.
  const authenticated = data?.authenticated === true;
  if (expired && !authenticated) setExpired(false);

  const refetchEverything = useCallback(() => {
    void queryClient.invalidateQueries();
  }, [queryClient]);

  let body: ReactNode;
  if (isPending) {
    // Nothing true to say yet. The field, and no copy.
    body = (
      <div
        className="relative flex flex-1 flex-col"
        style={{ background: "var(--bg)", color: "var(--fg)" }}
      >
        <GateField />
      </div>
    );
  } else if (needsSetup || setupActive) {
    body = (
      <SetupGate
        onDone={() => {
          setSetupActive(false);
          // Registration authenticated this session. Say so now: the cached
          // status still reads `registered: false`, and the latch above would
          // re-arm on it before the refetch landed — setup for ever.
          queryClient.setQueryData<AuthStatus>(["auth-status"], {
            registered: true,
            authenticated: true,
          });
          refetchEverything();
        }}
      />
    );
  } else if (!authenticated) {
    // Fail closed: a first read that errors leaves `data` undefined, and mounting
    // the room with unknown auth state would show an empty house as if it were
    // the truth. A failed *refetch* keeps the last good data, and the room with
    // it — a session that has really lapsed arrives as the `expired` event.
    body = <SignInGate onSignedIn={refetchEverything} />;
  } else {
    body = children;
  }

  // The two event gates sit outside the routing: `denied` means "not from this
  // network", which is as true on the setup and sign-in gates as in the room —
  // and setup swallows its 403s on the promise that this gate has already said it.
  return (
    <>
      {body}
      <Layer open={expired} label="Session lapsed" level="gate" durationMs={400}>
        <ExpiredGate
          onSignedIn={() => {
            setExpired(false);
            refetchEverything();
          }}
        />
      </Layer>
      <Layer open={denied} label="Not from here" level="gate" durationMs={400}>
        <DeniedGate onDismiss={() => setDenied(false)} />
      </Layer>
    </>
  );
}
```

- [ ] **Step 8: Run the AuthGate test and the suite**

Run: `npm test -- src/gates/AuthGate.test.tsx`
Expected: `Test Files  1 passed (1)`, 15 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  17 passed (17)`, 162 tests, no eslint output, `✓ built in …`.

- [ ] **Step 9: Commit**

```bash
git add web/src/gates web/src/lib/auth.ts web/src/lib/auth.test.ts web/src/lib/format.ts web/src/lib/format.test.ts
git commit -m "feat(web): sign-in, expired and denied gates behind one auth router"
```

---

### Task 11: Keepalive on both sockets

Spec §3.6: Cloudflare closes a proxied WebSocket after ~100 s idle, and the chat socket idles for hours. Without an application-level ping the client reconnects for ever. `ReconnectingSocket` gains a 30 s `{"type":"ping"}` (both `/ws` and `/ws/telemetry` answer `{"type":"pong"}` since phase 0b) and a `lastMessageAt` stamp. `pong` must never reach a listener — it is plumbing, not conversation.

**Files:**
- Modify: `web/src/lib/ws.ts`, `web/src/lib/ws.test.ts`
- Modify: `web/src/lib/chat-socket.ts`, `web/src/lib/chat-socket.test.ts`
- Unchanged: `web/src/lib/telemetry-socket.ts`

- [ ] **Step 1: Write the failing keepalive tests**

Append to the `describe("ReconnectingSocket", …)` block in `web/src/lib/ws.test.ts`:

```ts
  it("pings every 30 s while open and stops once closed", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    vi.advanceTimersByTime(30_000);
    expect(ws.sent.map((s) => JSON.parse(s))).toEqual([{ type: "ping" }]);

    vi.advanceTimersByTime(30_000);
    expect(ws.sent).toHaveLength(2);

    sock.close();
    // The interval is gone, not merely quiet: a closed fake socket refuses
    // sends, so `sent` staying flat would not prove the timer was cleared.
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(120_000);
    expect(ws.sent).toHaveLength(2);
  });

  it("does not ping before the socket is open", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    vi.advanceTimersByTime(120_000);
    expect(FakeWebSocket.instances[0].sent).toHaveLength(0);
  });

  it("takes a custom interval", () => {
    const sock = new ReconnectingSocket("/ws/test", { pingIntervalMs: 1000 });
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    vi.advanceTimersByTime(3000);

    expect(ws.sent).toHaveLength(3);
    sock.close();
  });

  it("stops pinging a socket the server dropped", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.emitClose(1006);

    vi.advanceTimersByTime(30_000);

    expect(ws.sent).toHaveLength(0);
    // The 500 ms reconnect has fired by now; the only timer left would be a
    // leaked ping interval.
    expect(vi.getTimerCount()).toBe(0);
    sock.close();
  });

  it("stamps when the last frame arrived", () => {
    const sock = new ReconnectingSocket("/ws/test");
    expect(sock.lastMessageAt).toBeNull();
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    expect(sock.lastMessageAt).toBeNull(); // opening is not a frame

    ws.onmessage?.({ data: '{"type":"pong"}' });

    expect(sock.lastMessageAt).toBeGreaterThan(0);
    sock.close();
  });
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npm test -- src/lib/ws.test.ts`
Expected: FAIL — the ping tests find `sent` empty (nothing pings yet), and `lastMessageAt` is `undefined`, not `null`. The two "stops pinging" tests pass vacuously until Step 3 (nothing is scheduled yet, so `vi.getTimerCount()` is already 0).

- [ ] **Step 3: Rewrite `web/src/lib/ws.ts`**

Complete file:

```ts
export type SocketStatus = "connecting" | "online" | "reconnecting" | "offline" | "unauthorized";

const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 8000;
/** Cloudflare closes an idle proxied socket at ~100 s; 30 s keeps it comfortably alive. */
const DEFAULT_PING_MS = 30_000;

export interface SocketOptions {
  pingIntervalMs?: number;
}

export class ReconnectingSocket {
  private path: string;
  private ws: WebSocket | null = null;
  private attempts = 0;
  private stopped = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingIntervalMs: number;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  /** `Date.now()` of the last frame from the server, keepalive pongs included. */
  lastMessageAt: number | null = null;

  onmessage: (data: unknown) => void = () => {};
  onstatus: (status: SocketStatus) => void = () => {};
  onopen: () => void = () => {};

  constructor(path: string, options: SocketOptions = {}) {
    this.path = path;
    this.pingIntervalMs = options.pingIntervalMs ?? DEFAULT_PING_MS;
  }

  private url(): string {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}${this.path}`;
  }

  private startPing(ws: WebSocket): void {
    this.stopPing();
    if (this.pingIntervalMs <= 0) return;
    this.pingTimer = setInterval(() => {
      if (this.ws !== ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ type: "ping" }));
    }, this.pingIntervalMs);
  }

  private stopPing(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  connect(): void {
    // Bail if a socket is already live. Without this, a second connect() (React
    // StrictMode's setup→cleanup→setup, or a fast logout/login) overwrites this.ws
    // while the previous socket's onclose still fires and spawns a duplicate,
    // permanently-reconnecting connection. Every handler is also pinned to its own
    // socket so a stale socket can never mutate state after being replaced.
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)
    ) {
      return;
    }
    this.stopped = false;
    this.onstatus(this.attempts === 0 ? "connecting" : "reconnecting");
    const ws = new WebSocket(this.url());
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.attempts = 0;
      this.startPing(ws);
      this.onstatus("online");
      this.onopen();
    };
    ws.onmessage = (e) => {
      if (this.ws !== ws) return;
      this.lastMessageAt = Date.now();
      try {
        this.onmessage(JSON.parse(e.data as string));
      } catch {
        /* non-JSON frame */
      }
    };
    ws.onclose = (e) => {
      if (this.ws !== ws) return; // superseded socket — ignore its close
      this.stopPing();
      if (e.code === 4001) {
        this.onstatus("unauthorized");
        return;
      }
      if (this.stopped) {
        this.onstatus("offline");
        return;
      }
      this.onstatus("reconnecting");
      const delay = Math.min(BASE_DELAY_MS * 2 ** this.attempts, MAX_DELAY_MS);
      this.attempts += 1;
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        if (!this.stopped) this.connect();
      }, delay);
    };
  }

  send(payload: unknown): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
      return true;
    }
    return false;
  }

  close(): void {
    this.stopped = true;
    this.stopPing();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
  }
}
```

- [ ] **Step 4: Run the socket tests**

Run: `npm test -- src/lib/ws.test.ts`
Expected: `Test Files  1 passed (1)`, 10 tests (the five carried over plus the five new).

- [ ] **Step 5: Replace `web/src/lib/chat-socket.test.ts`**

Complete file:

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";

const { sent, sockets } = vi.hoisted(() => ({
  sent: [] as Record<string, unknown>[],
  sockets: [] as { onmessage: (data: unknown) => void; onstatus: (s: string) => void }[],
}));

vi.mock("./ws", () => {
  class ReconnectingSocket {
    onstatus: (s: string) => void = () => {};
    onopen: () => void = () => {};
    onmessage: (data: unknown) => void = () => {};
    lastMessageAt: number | null = null;
    constructor() {
      sockets.push(this);
    }
    connect(): void {}
    close(): void {}
    send(payload: Record<string, unknown>): boolean {
      sent.push(payload);
      return true;
    }
  }
  return { ReconnectingSocket };
});

import { ChatSocket } from "./chat-socket";
import type { ChatServerMessage } from "./types";

beforeEach(() => {
  sent.length = 0;
  sockets.length = 0;
  localStorage.clear();
});

describe("ChatSocket payloads", () => {
  it("include the client IANA timezone and the pwa channel", () => {
    const socket = new ChatSocket();
    socket.sendText("hello");
    const body = sent.at(-1)!;
    expect(body.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
    expect(body.channel).toBe("web_pwa");
    expect(body.type).toBe("text");
    expect(body.content).toBe("hello");
  });

  it("carry a stored session id on the first message only", () => {
    localStorage.setItem("alfred_session_id", "s_9f2");
    const socket = new ChatSocket();

    socket.sendText("first");
    socket.sendText("second");

    expect(sent[0].session_id).toBe("s_9f2");
    expect(sent[1].session_id).toBeUndefined();
  });

  it("send audio as a data URL", () => {
    const socket = new ChatSocket();
    socket.sendAudio("data:audio/mp4;base64,AAAA");
    expect(sent.at(-1)).toMatchObject({ type: "audio", content: "data:audio/mp4;base64,AAAA" });
  });
});

describe("ChatSocket frames", () => {
  it("adopts the server's session id when it has none", () => {
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_new" });
    expect(socket.sessionId).toBe("s_new");
    expect(localStorage.getItem("alfred_session_id")).toBe("s_new");
  });

  it("keeps a pong to itself", () => {
    const socket = new ChatSocket();
    const seen: ChatServerMessage[] = [];
    socket.listen((msg) => seen.push(msg));

    sockets[0].onmessage({ type: "pong" });
    sockets[0].onmessage({ type: "error", text: "nope" });

    expect(seen).toEqual([{ type: "error", text: "nope" }]);
  });

  it("forwards socket status", () => {
    const socket = new ChatSocket();
    const seen: string[] = [];
    socket.onstatus = (s) => seen.push(s);
    sockets[0].onstatus("unauthorized");
    expect(seen).toEqual(["unauthorized"]);
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npm test -- src/lib/chat-socket.test.ts`
Expected: FAIL — "keeps a pong to itself" receives both frames; every listener sees the keepalive today.

- [ ] **Step 7: Filter `pong` in `web/src/lib/chat-socket.ts`**

In the `onmessage` handler, add the guard as its first statement:

```ts
    this.socket.onmessage = (data) => {
      const msg = data as ChatServerMessage;
      // Keepalive plumbing. `lastMessageAt` on the socket already recorded it;
      // nothing above this layer should have to skip it.
      if (msg.type === "pong") return;
      if (msg.type === "session") {
        // Server assigns; we may override with our stored id on first send.
        if (!this.sessionId) {
          this.sessionId = msg.session_id;
          localStorage.setItem(SESSION_KEY, msg.session_id);
        }
      }
      for (const fn of this.listeners) fn(msg);
    };
```

Nothing else in `chat-socket.ts` changes, and `telemetry-socket.ts` is untouched — its listeners switch on `msg.type` and `"pong"` is already in `TelemetryMessage`.

- [ ] **Step 8: Run the tests and prove the carry-over**

Run: `npm test -- src/lib/chat-socket.test.ts`
Expected: `Test Files  1 passed (1)`, 6 tests.

```bash
git diff --stat origin/master -- web/src/lib/telemetry-socket.ts
```

Expected: no output.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  17 passed (17)`, 172 tests, no eslint output, `✓ built in …`.

- [ ] **Step 9: Commit**

```bash
git add web/src/lib/ws.ts web/src/lib/ws.test.ts web/src/lib/chat-socket.ts web/src/lib/chat-socket.test.ts
git commit -m "feat(web): 30 s socket keepalive so proxied sockets stop churning"
```

---

### Task 12: The connection, and coming back from the dead

Constraint §4.10: iOS suspends and kills standalone PWAs aggressively, and the telemetry socket starts at `$` and replays nothing. So returning to the foreground must reconnect both sockets *and* re-read what the feed missed. `ConnectionProvider` owns the two socket singletons, the `online` flag the whole design branches on, and the `lastTrueAt` stamp behind every "last true HH:MM" in the UI. It also owns `reconnect()`, which the auth gate calls after every sign-in: while there is no session the server closes both sockets with 4001, and a 4001 is never retried on its own, so without that call a signed-out cold start would leave the Room offline for ever. And because a socket that died under a suspended app can still read OPEN, `connect()` learns to replace one that has not answered a keepalive in two rounds.

**Files:**
- Create: `web/src/lib/lifecycle.ts`, `web/src/lib/lifecycle.test.ts`
- Create: `web/src/shell/ConnectionProvider.tsx`, `web/src/shell/ConnectionProvider.test.tsx`
- Modify: `web/src/gates/AuthGate.tsx`, `web/src/gates/AuthGate.test.tsx`
- Modify: `web/src/lib/ws.ts`, `web/src/lib/ws.test.ts`

- [ ] **Step 1: Write the failing lifecycle test**

Create `web/src/lib/lifecycle.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { onVisible } from "./lifecycle";

function setVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
  document.dispatchEvent(new Event("visibilitychange"));
}

function pageShow(persisted: boolean): void {
  const event = new Event("pageshow");
  Object.defineProperty(event, "persisted", { value: persisted });
  window.dispatchEvent(event);
}

describe("onVisible", () => {
  it("fires when the document becomes visible, not when it hides", () => {
    const fn = vi.fn();
    const off = onVisible(fn);

    setVisibility("hidden");
    expect(fn).not.toHaveBeenCalled();

    setVisibility("visible");
    expect(fn).toHaveBeenCalledTimes(1);

    off();
  });

  it("fires on a restore from the back-forward cache", () => {
    const fn = vi.fn();
    const off = onVisible(fn);

    pageShow(false);
    expect(fn).not.toHaveBeenCalled();

    pageShow(true);
    expect(fn).toHaveBeenCalledTimes(1);

    off();
  });

  it("stops once unsubscribed", () => {
    const fn = vi.fn();
    onVisible(fn)();

    setVisibility("visible");
    pageShow(true);

    expect(fn).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/lib/lifecycle.test.ts`
Expected: FAIL — `Failed to resolve import "./lifecycle"`.

- [ ] **Step 3: Write `web/src/lib/lifecycle.ts`**

Complete file:

```ts
/**
 * Call `fn` whenever the app comes back to the foreground.
 *
 * Two events, because iOS uses both: `visibilitychange` for an app switch, and
 * `pageshow` with `persisted` for a restore out of the back-forward cache, which
 * fires no visibility change at all.
 */
export function onVisible(fn: () => void): () => void {
  const onVisibility = () => {
    if (document.visibilityState === "visible") fn();
  };
  const onPageShow = (event: Event) => {
    if ((event as PageTransitionEvent).persisted) fn();
  };

  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("pageshow", onPageShow);

  return () => {
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("pageshow", onPageShow);
  };
}
```

Run: `npm test -- src/lib/lifecycle.test.ts`
Expected: `Test Files  1 passed (1)`, 3 tests.

- [ ] **Step 4: Write the failing ConnectionProvider test**

Create `web/src/shell/ConnectionProvider.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authEvents } from "@/lib/auth-events";
import type { SocketStatus } from "@/lib/ws";
import { ConnectionProvider, useConnection } from "./ConnectionProvider";

interface FakeSocket {
  onstatus: (status: SocketStatus) => void;
  connect: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  deliver: (msg: unknown) => void;
}

const { chats, telemetries } = vi.hoisted(() => ({
  chats: [] as unknown[],
  telemetries: [] as unknown[],
}));

vi.mock("@/lib/chat-socket", () => {
  class ChatSocket {
    onstatus: (status: string) => void = () => {};
    listeners = new Set<(msg: unknown) => void>();
    connect = vi.fn();
    close = vi.fn();
    constructor() {
      chats.push(this);
    }
    listen(fn: (msg: unknown) => void): () => void {
      this.listeners.add(fn);
      return () => void this.listeners.delete(fn);
    }
    deliver(msg: unknown): void {
      for (const fn of this.listeners) fn(msg);
    }
  }
  return { ChatSocket };
});

vi.mock("@/lib/telemetry-socket", () => {
  class TelemetrySocket {
    onstatus: (status: string) => void = () => {};
    connect = vi.fn();
    close = vi.fn();
    subscribe = vi.fn();
    constructor() {
      telemetries.push(this);
    }
    listen(): () => void {
      return () => {};
    }
  }
  return { TelemetrySocket };
});

function Probe() {
  const { online, lastTrueAt, chatStatus, reconnect } = useConnection();
  return (
    <div>
      <span data-testid="online">{String(online)}</span>
      <span data-testid="status">{chatStatus}</span>
      <span data-testid="last-true">{lastTrueAt ? lastTrueAt.toISOString() : "none"}</span>
      <button onClick={reconnect}>reconnect</button>
    </div>
  );
}

function Subscriber({ onOnline }: { onOnline: () => void }) {
  const { subscribeOnline } = useConnection();
  useEffect(() => subscribeOnline(onOnline), [subscribeOnline, onOnline]);
  return null;
}

function renderProvider() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const utils = render(
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <Probe />
      </ConnectionProvider>
    </QueryClientProvider>,
  );
  return {
    ...utils,
    invalidate,
    chat: chats[0] as FakeSocket,
    telemetry: telemetries[0] as FakeSocket,
  };
}

/** The app comes back to the foreground. */
function comeBack(): void {
  act(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

beforeEach(() => {
  // One module load means one pair of singletons for the whole file — which is the
  // behaviour under test, so reset their spies rather than expecting new instances.
  for (const socket of [chats[0], telemetries[0]] as (FakeSocket | undefined)[]) {
    socket?.connect.mockClear();
    socket?.close.mockClear();
  }
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ConnectionProvider", () => {
  it("connects both sockets on mount", () => {
    const { chat, telemetry } = renderProvider();
    expect(chat.connect).toHaveBeenCalled();
    expect(telemetry.connect).toHaveBeenCalled();
  });

  it("is online only while the chat socket is", () => {
    const { chat } = renderProvider();
    expect(screen.getByTestId("online")).toHaveTextContent("false");

    // `lastTrue` is module state and an earlier test may have stamped it, so
    // prove the stamp moved rather than that it exists.
    const at = new Date("2031-05-04T09:41:00Z");
    vi.setSystemTime(at);
    act(() => chat.onstatus("online"));

    expect(screen.getByTestId("online")).toHaveTextContent("true");
    expect(screen.getByTestId("status")).toHaveTextContent("online");
    expect(screen.getByTestId("last-true")).toHaveTextContent(at.toISOString());

    act(() => chat.onstatus("reconnecting"));
    expect(screen.getByTestId("online")).toHaveTextContent("false");
  });

  it("stamps last-true on every frame from the house", () => {
    const { chat } = renderProvider();
    const at = new Date("2031-05-04T09:42:00Z");
    vi.setSystemTime(at);

    act(() => chat.deliver({ type: "response", text: "Quite so, sir.", session_id: "s_1" }));

    expect(screen.getByTestId("last-true")).toHaveTextContent(at.toISOString());
  });

  it("turns a 4001 close on either socket into the expired gate", () => {
    const expired = vi.fn();
    const off = authEvents.on("expired", expired);
    const { chat, telemetry } = renderProvider();

    act(() => chat.onstatus("unauthorized"));
    expect(expired).toHaveBeenCalledTimes(1);

    // The chat socket has since been reopened; the telemetry socket says so on its own.
    act(() => chat.onstatus("connecting"));
    act(() => telemetry.onstatus("unauthorized"));
    expect(expired).toHaveBeenCalledTimes(2);
    off();
  });

  it("reconnects both sockets and re-reads everything on the way back from the background", () => {
    const { invalidate, chat, telemetry } = renderProvider();
    chat.connect.mockClear();
    telemetry.connect.mockClear();
    invalidate.mockClear();

    comeBack();

    expect(chat.connect).toHaveBeenCalledTimes(1);
    expect(telemetry.connect).toHaveBeenCalledTimes(1);
    const keys = invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toEqual([
      '["overview"]',
      '["room-history"]',
      '["pending-actions"]',
      '["deferred"]',
    ]);
  });

  it("reopens both sockets on reconnect()", () => {
    const { chat, telemetry } = renderProvider();
    chat.connect.mockClear();
    telemetry.connect.mockClear();

    fireEvent.click(screen.getByRole("button", { name: "reconnect" }));

    expect(chat.connect).toHaveBeenCalledTimes(1);
    expect(telemetry.connect).toHaveBeenCalledTimes(1);
  });

  it("tells a subscriber about every open, including one before it subscribed", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const early = vi.fn();
    const late = vi.fn();
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <ConnectionProvider>
          <Subscriber onOnline={early} />
        </ConnectionProvider>
      </QueryClientProvider>,
    );
    const chat = chats[0] as FakeSocket;
    expect(early).not.toHaveBeenCalled();

    act(() => chat.onstatus("online"));
    expect(early).toHaveBeenCalledTimes(1);

    // The Room mounts behind a gate, which can resolve after the handshake.
    rerender(
      <QueryClientProvider client={client}>
        <ConnectionProvider>
          <Subscriber onOnline={early} />
          <Subscriber onOnline={late} />
        </ConnectionProvider>
      </QueryClientProvider>,
    );
    expect(late).toHaveBeenCalledTimes(1);

    act(() => chat.onstatus("reconnecting"));
    act(() => chat.onstatus("online"));
    expect(early).toHaveBeenCalledTimes(2);
    expect(late).toHaveBeenCalledTimes(2);
  });

  it("leaves nothing of itself on the singletons when it unmounts", () => {
    const { invalidate, chat, telemetry, unmount } = renderProvider();
    act(() => chat.onstatus("online"));

    unmount();

    expect(chat.close).toHaveBeenCalledTimes(1);
    expect(telemetry.close).toHaveBeenCalledTimes(1);

    // A foreground return after the unmount is nobody's business now.
    chat.connect.mockClear();
    invalidate.mockClear();
    comeBack();
    expect(chat.connect).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();

    // The close event `close()` produces lands after the cleanup. It must not
    // reach the old callbacks — the next mount starts offline, whatever a stale
    // status says.
    act(() => chat.onstatus("online"));
    const late = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ConnectionProvider>
          <Subscriber onOnline={late} />
        </ConnectionProvider>
      </QueryClientProvider>,
    );
    expect(late).not.toHaveBeenCalled();
  });

  it("refuses to be used outside the provider", () => {
    expect(() => render(<Probe />)).toThrow("useConnection outside ConnectionProvider");
  });
});
```

- [ ] **Step 5: Run it to verify it fails**

Run: `npm test -- src/shell/ConnectionProvider.test.tsx`
Expected: FAIL — `Failed to resolve import "./ConnectionProvider"`.

- [ ] **Step 6: Write `web/src/shell/ConnectionProvider.tsx`**

Complete file:

```tsx
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { authEvents } from "@/lib/auth-events";
import { ChatSocket } from "@/lib/chat-socket";
import { onVisible } from "@/lib/lifecycle";
import { TelemetrySocket } from "@/lib/telemetry-socket";
import type { SocketStatus } from "@/lib/ws";

// Module-level singletons, as the outgoing AlfredProvider had them: one instance
// per module load, which is exactly one per app. Keeps them out of refs and out
// of the react-hooks immutability rule.
const chat = new ChatSocket();
const telemetry = new TelemetrySocket();

/**
 * Make sure both sockets are live. `connect()` reopens a closed socket, replaces
 * one that has gone quiet and leaves a healthy one alone, so this is safe on
 * every return to the foreground — and necessary after every sign-in: the server
 * closes both sockets with 4001 while there is no session, and a 4001 is never
 * retried.
 */
function reconnect(): void {
  chat.connect();
  telemetry.connect();
}

let lastTrue: Date | null = null;
const lastTrueListeners = new Set<(at: Date) => void>();

/**
 * Record that the house answered just now. Called from here on every chat frame
 * and every socket open, and from `useOverview` on every successful poll — the
 * two independent proofs that anything on screen is current.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function markTrue(at: Date = new Date()): void {
  lastTrue = at;
  for (const fn of [...lastTrueListeners]) fn(at);
}

function subscribeTrue(fn: (at: Date) => void): () => void {
  lastTrueListeners.add(fn);
  return () => void lastTrueListeners.delete(fn);
}

let chatOnline = false;
const onlineListeners = new Set<() => void>();

/**
 * Run `fn` each time the chat socket (re)opens, and at once if it is open now.
 * A subscriber that arrives after the open — the Room, behind a gate that took
 * longer than the handshake — must not wait for the next reconnect.
 */
function subscribeOnline(fn: () => void): () => void {
  onlineListeners.add(fn);
  if (chatOnline) fn();
  return () => void onlineListeners.delete(fn);
}

/** What a suspended PWA has to re-read on return; the telemetry socket replays nothing. */
const REHYDRATE_KEYS = [["overview"], ["room-history"], ["pending-actions"], ["deferred"]];

export interface ConnectionValue {
  chat: ChatSocket;
  telemetry: TelemetrySocket;
  chatStatus: SocketStatus;
  telemetryStatus: SocketStatus;
  online: boolean;
  lastTrueAt: Date | null;
  /** The moment a queued send can succeed. See `subscribeOnline` above. */
  subscribeOnline: (fn: () => void) => () => void;
  /** Reopen whatever is closed. The gates call it after a sign-in. See `reconnect` above. */
  reconnect: () => void;
}

const ConnectionContext = createContext<ConnectionValue | null>(null);

export function ConnectionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [chatStatus, setChatStatus] = useState<SocketStatus>("connecting");
  const [telemetryStatus, setTelemetryStatus] = useState<SocketStatus>("connecting");
  const [lastTrueAt, setLastTrueAt] = useState<Date | null>(lastTrue);

  useEffect(() => {
    chat.onstatus = (status) => {
      chatOnline = status === "online";
      setChatStatus(status);
      if (chatOnline) {
        markTrue();
        for (const fn of [...onlineListeners]) fn();
      }
    };
    telemetry.onstatus = setTelemetryStatus;

    const stopListening = chat.listen(() => markTrue());
    const stopTrue = subscribeTrue(setLastTrueAt);

    reconnect();

    const stopVisible = onVisible(() => {
      reconnect();
      for (const queryKey of REHYDRATE_KEYS) void queryClient.invalidateQueries({ queryKey });
    });

    return () => {
      // The singletons outlive this mount; leave nothing of it on them. The
      // `close()` calls below still produce a close event each, and it must not
      // reach a setState on an unmounted tree or flip `chatOnline` under the next
      // mount.
      chat.onstatus = () => {};
      telemetry.onstatus = () => {};
      stopListening();
      stopTrue();
      stopVisible();
      chat.close();
      telemetry.close();
      chatOnline = false;
    };
  }, [queryClient]);

  // Either socket closing with 4001 means the cookie is gone. Same news as a 401
  // from the API, same gate.
  useEffect(() => {
    if (chatStatus === "unauthorized" || telemetryStatus === "unauthorized") {
      authEvents.emit("expired");
    }
  }, [chatStatus, telemetryStatus]);

  const value = useMemo<ConnectionValue>(
    () => ({
      chat,
      telemetry,
      chatStatus,
      telemetryStatus,
      // The telemetry socket can be down while the butler still answers; only the
      // chat socket decides whether the app calls itself online.
      online: chatStatus === "online",
      lastTrueAt,
      subscribeOnline,
      reconnect,
    }),
    [chatStatus, telemetryStatus, lastTrueAt],
  );

  return <ConnectionContext.Provider value={value}>{children}</ConnectionContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useConnection(): ConnectionValue {
  const ctx = useContext(ConnectionContext);
  if (!ctx) throw new Error("useConnection outside ConnectionProvider");
  return ctx;
}
```

- [ ] **Step 7: Wire the connection into the auth gate**

`AuthGate` now has a `ConnectionProvider` above it (Task 14 composes them in that order), so the 403 gate can say when the house was last reachable — and the gate can do the one thing the sockets cannot do for themselves. In `web/src/gates/AuthGate.tsx`, add the import and use it:

```tsx
import { useConnection } from "@/shell/ConnectionProvider";
```

```tsx
export function AuthGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { lastTrueAt, reconnect } = useConnection();
```

```tsx
  // A sign-in is the one moment the sockets need a push: the server closed them
  // with 4001 while there was no session, and a 4001 is never retried.
  const refetchEverything = useCallback(() => {
    reconnect();
    void queryClient.invalidateQueries();
  }, [queryClient, reconnect]);
```

```tsx
      <Layer open={denied} label="Not from here" level="gate" durationMs={400}>
        <DeniedGate lastTrue={lastTrueAt} onDismiss={() => setDenied(false)} />
      </Layer>
```

`AuthGate.test.tsx` must now render inside a `ConnectionProvider` too. Wrap the tree in `renderGate()`:

```tsx
  const utils = render(
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <AuthGate>
          <main>the room</main>
        </AuthGate>
      </ConnectionProvider>
    </QueryClientProvider>,
  );
```

The real sockets are constructed but must never open in jsdom, so the file starts with mocks of both — the same shape as `ConnectionProvider.test.tsx`'s, minus the instance arrays, plus a record of every `connect()` for the reopen test below. Put this above every other import, with `markTrue` joining the provider import:

```tsx
import { ConnectionProvider, markTrue } from "@/shell/ConnectionProvider";
/** Which sockets `connect()` was asked of, in order. */
const { socketConnects } = vi.hoisted(() => ({ socketConnects: [] as string[] }));
vi.mock("@/lib/chat-socket", () => ({
  ChatSocket: class {
    onstatus = () => {};
    connect() {
      socketConnects.push("chat");
    }
    close() {}
    listen() {
      return () => {};
    }
  },
}));
vi.mock("@/lib/telemetry-socket", () => ({
  TelemetrySocket: class {
    onstatus = () => {};
    connect() {
      socketConnects.push("telemetry");
    }
    close() {}
    subscribe() {}
    listen() {
      return () => {};
    }
  },
}));
```

Add `import { hhmm } from "@/lib/format";` under the `DEVICE_KEY` import, and `socketConnects.length = 0;` to `beforeEach` after `statusDown = false;`. Then two tests. After "signs in with the passkey and reveals the room":

```tsx
  it("reopens both sockets once signed in: a 4001 is never retried on its own", async () => {
    const user = userEvent.setup();
    status = { registered: true, authenticated: false };
    renderGate();
    await screen.findByRole("heading", { name: "Welcome back, sir." });
    expect(socketConnects).toEqual(["chat", "telemetry"]); // the mount, and nothing since
    socketConnects.length = 0;

    status = { registered: true, authenticated: true };
    await user.click(screen.getByRole("button", { name: "Sign in with Face ID" }));

    await screen.findByText("the room");
    expect(socketConnects).toEqual(["chat", "telemetry"]);
  });
```

And the denied-gate test grows the foot line, which is the whole point of the wiring:

```tsx
  it("raises the denied gate, which only offers a way back", async () => {
    const user = userEvent.setup();
    renderGate();
    await screen.findByText("the room");

    // The house answered at 21:15; the gate says so, since what is behind it dates from then.
    const at = new Date(2026, 8, 8, 21, 15);
    act(() => markTrue(at));
    act(() => authEvents.emit("denied"));

    expect(screen.getByRole("heading", { name: "Not from here." })).toBeInTheDocument();
    expect(screen.getByText("403 · off-network")).toBeInTheDocument();
    expect(
      screen.getByText(`Last true ${hhmm(at)} · everything shown behind this is last-known`),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign in with Face ID" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Back to the room" }));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Not from here." })).toBeNull(),
    );
    expect(screen.getByText("the room")).toBeInTheDocument();
  });
```

- [ ] **Step 8: Replace a socket that has gone quiet**

`reconnect()` calls `connect()`, and `connect()` leaves an OPEN socket alone — which is wrong for exactly the socket §4.10 is about. A connection the OS dropped while the PWA was suspended still reads OPEN: no close event ever arrives for it, and `send()` on it does not throw, so the keepalive keeps "working". The pongs that stopped coming are the only evidence, and Task 11's `lastMessageAt` is where they are counted. How long a silence counts is the delicate part: on this server the pong rides the same serial receive loop as chat turns (`core/channels/web_server.py`, the ping handler), so it can lag a whole 60 s conscious-engine turn, and the last pong can predate that turn by up to a ping interval — pong latency is not a liveness signal. Two keepalive rounds would supersede a live socket mid-answer, so the window is four. In `web/src/lib/ws.ts`, name it under the ping interval:

```ts
/** Cloudflare closes an idle proxied socket at ~100 s; 30 s keeps it comfortably alive. */
const DEFAULT_PING_MS = 30_000;
/**
 * How many keepalive rounds of silence make an open socket "quiet". Pong latency
 * is not a liveness signal on this server: the pong rides the same serial receive
 * loop as chat turns, so it can lag a whole conscious-engine turn (60 s, see
 * `core/channels/web_server.py`, the ping handler) — and the last pong can predate
 * that turn by up to a ping interval. Four rounds (120 s at the default) clears a
 * full turn with room to spare; a socket the OS dropped is still caught on the
 * next foreground return, which is the only time this is checked.
 */
const QUIET_AFTER_PINGS = 4;
```

add a private stamp next to `lastMessageAt`:

```ts
  /** `Date.now()` of the last frame from the server, keepalive pongs included. */
  lastMessageAt: number | null = null;
  private openedAt = 0;
```

set it in `onopen`:

```ts
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.attempts = 0;
      this.openedAt = Date.now();
      this.startPing(ws);
```

and open `connect()` with the check:

```ts
  /**
   * Whether the open socket has gone silent for QUIET_AFTER_PINGS keepalive
   * rounds. A socket that died while the PWA was suspended can still read OPEN —
   * no close event arrives for a connection the OS dropped, and `send()` on it
   * does not throw — so the pongs that stopped coming are the only evidence.
   */
  private quiet(ws: WebSocket): boolean {
    if (this.pingIntervalMs <= 0 || ws.readyState !== WebSocket.OPEN) return false;
    const lastSeen = Math.max(this.openedAt, this.lastMessageAt ?? 0);
    return Date.now() - lastSeen > QUIET_AFTER_PINGS * this.pingIntervalMs;
  }

  connect(): void {
    // A quiet socket is replaced, not kept: `this.ws` moves on first, so the
    // close event the old one produces is ignored below as a superseded socket's.
    if (this.ws && this.quiet(this.ws)) {
      const dead = this.ws;
      this.ws = null;
      this.stopPing();
      dead.close();
    }
    // Bail if a socket is already live. Without this, a second connect() (React
```

Then five tests, appended after "stamps when the last frame arrived" in `web/src/lib/ws.test.ts`. The two "keeps" cases in the middle pin both edges of the window: opening counts as being heard from (a fresh socket must survive the sign-in `reconnect()` that lands before its first pong), and a turn's worth of silence after a pong is not quiet.

```ts
  it("replaces an open socket that has gone quiet", () => {
    const statuses: string[] = [];
    const sock = new ReconnectingSocket("/ws/test");
    sock.onstatus = (s) => statuses.push(s);
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    // Four keepalive rounds, no pong: the connection died under a suspended app.
    vi.advanceTimersByTime(121_000);
    sock.connect();

    expect(ws.readyState).toBe(3);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(statuses).toEqual(["connecting", "online", "connecting"]);
    sock.close();
  });

  it("keeps an open socket that answered recently", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    vi.advanceTimersByTime(50_000);
    ws.onmessage?.({ data: '{"type":"pong"}' });
    vi.advanceTimersByTime(20_000);
    sock.connect();

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(ws.readyState).toBe(1);
    sock.close();
  });

  it("keeps a socket that opened recently and has not spoken yet", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    // No frame at all yet — the first pong is still on its way. Opening counts.
    vi.advanceTimersByTime(50_000);
    sock.connect();

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(ws.readyState).toBe(1);
    sock.close();
  });

  it("keeps a live socket through a long turn: pong latency is not liveness", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    // One pong, then silence for a full conscious-engine turn (60 s), plus the
    // ping interval the last pong can predate it by, plus slack for the round trip.
    vi.advanceTimersByTime(30_000);
    ws.onmessage?.({ data: '{"type":"pong"}' });
    vi.advanceTimersByTime(100_000);
    sock.connect();

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(ws.readyState).toBe(1);
    sock.close();
  });

  it("reopens a socket the server refused, once asked", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    FakeWebSocket.instances[0].emitClose(4001);

    sock.connect();

    expect(FakeWebSocket.instances).toHaveLength(2);
    sock.close();
  });
```

Run: `npm test -- src/lib/ws.test.ts`
Expected: `Test Files  1 passed (1)`, 15 tests.

- [ ] **Step 9: Run everything**

Run: `npm test -- src/shell/ConnectionProvider.test.tsx src/gates/AuthGate.test.tsx`
Expected: `Test Files  2 passed (2)` — 9 and 16 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  19 passed (19)`, 190 tests, no eslint output, `✓ built in …`.

- [ ] **Step 10: Commit**

```bash
git add web/src/lib/lifecycle.ts web/src/lib/lifecycle.test.ts web/src/shell/ConnectionProvider.tsx web/src/shell/ConnectionProvider.test.tsx web/src/gates/AuthGate.tsx web/src/gates/AuthGate.test.tsx web/src/lib/ws.ts web/src/lib/ws.test.ts
git commit -m "feat(web): socket singletons, online state and rehydration on return"
```

---

### Task 13: The status line, and the formatters the whole client shares

One mono line under the headline, in four states, and it is the design's main instrument for "live is not last-known" (spec §5.2):

| State | Line |
|---|---|
| online | `21:14 · cloud 1.42 / 5.00 · reflex 380 ms · 2.1 ev/s` |
| offline | `last true 21:14 · cloud 1.42 / 5.00 · reflex ok` |
| first run (every stream empty) | `first run · cloud 0.00 / 5.00 · reflex ok · 0 ev/s` |
| no cost record yet | `… · cloud — · …` |

`reflex ok` is what it says whenever there is no measured latency to quote. The rest of the formatters land here too, because 1b's rows, tombstones and Door all read from them.

**Files:**
- Modify: `web/src/lib/format.ts`, `web/src/lib/format.test.ts`
- Modify: `web/src/test/fixtures.ts`
- Create: `web/src/room/useOverview.ts`, `web/src/room/useOverview.test.tsx`, `web/src/room/StatusLine.tsx`, `web/src/room/StatusLine.test.tsx`

- [ ] **Step 1: Write the failing formatter tests**

Append to `web/src/lib/format.test.ts` (extend the import with `dayLabel, evs, humaniseTool, mmss, rawCall, shortId, usd`, and add `import type { StreamSummary } from "./types";` below it — the `evs` guard test hands it a summary with no `rate_5m`, which the type does not admit without a cast):

```ts
describe("mmss", () => {
  it("formats a fuse", () => {
    expect(mmss(252)).toBe("4:12");
    expect(mmss(300)).toBe("5:00");
    expect(mmss(9)).toBe("0:09");
    expect(mmss(0)).toBe("0:00");
  });
  it("never counts below zero", () => {
    expect(mmss(-5)).toBe("0:00");
  });
});

describe("usd", () => {
  it("is always two decimals", () => {
    expect(usd(1.42)).toBe("1.42");
    expect(usd(5)).toBe("5.00");
    expect(usd(0)).toBe("0.00");
  });
});

describe("evs", () => {
  it("sums the five-minute rates to one decimal", () => {
    expect(
      evs({
        events: { length: 1, last_id: null, last_ts: null, rate_5m: 1.4 },
        user_requests: { length: 1, last_id: null, last_ts: null, rate_5m: 0.2 },
        reflex_observations: { length: 1, last_id: null, last_ts: null, rate_5m: 0.5 },
      }),
    ).toBe("2.1");
  });
  it("counts a summary with no rate as 0, not NaN", () => {
    const unrated = { length: 3, last_id: null, last_ts: null } as StreamSummary;
    expect(evs({ events: unrated })).toBe("0");
    const rated = { length: 1, last_id: null, last_ts: null, rate_5m: 0.2 };
    expect(evs({ events: unrated, user_requests: rated })).toBe("0.2");
  });
  it("says a bare 0 when nothing is flowing", () => {
    expect(evs({})).toBe("0");
    expect(evs({ events: { length: 0, last_id: null, last_ts: null, rate_5m: 0 } })).toBe("0");
  });
});

describe("shortId", () => {
  it("is the first four characters", () => {
    expect(shortId("a91f3c2e-0b1d-4f8a")).toBe("a91f");
    expect(shortId("ab")).toBe("ab");
  });
});

describe("humaniseTool", () => {
  it("reads the last segment as a sentence", () => {
    expect(humaniseTool("home.lock_unlock")).toBe("Lock unlock");
    expect(humaniseTool("home.light_set")).toBe("Light set");
    expect(humaniseTool("speak")).toBe("Speak");
  });
  it("has something to say about nothing", () => {
    expect(humaniseTool("")).toBe("Action");
  });
});

describe("rawCall", () => {
  it("renders the call exactly as the Door shows it", () => {
    expect(
      rawCall("home.lock_unlock", { entity_id: "lock.front_door", action: "unlock" }),
    ).toBe('home.lock_unlock { entity_id: "lock.front_door", action: "unlock" }');
  });
  it("keeps non-string values as JSON", () => {
    expect(rawCall("home.light_set", { brightness_pct: 30, on: true })).toBe(
      "home.light_set { brightness_pct: 30, on: true }",
    );
  });
  it("renders an empty call", () => {
    expect(rawCall("home.ping", {})).toBe("home.ping {}");
  });
});

describe("dayLabel", () => {
  const now = new Date(2026, 8, 7, 21, 14);
  it("names today, yesterday and everything before", () => {
    expect(dayLabel(new Date(2026, 8, 7, 7, 2), now)).toBe("earlier today");
    expect(dayLabel(new Date(2026, 8, 6, 23, 59), now)).toBe("yesterday");
    expect(dayLabel(new Date(2026, 8, 4, 12, 0), now)).toBe("4 Sep");
  });
  it("calls a timestamp from a skewed clock today, not a day in the future", () => {
    expect(dayLabel(new Date(2026, 8, 8, 9, 0), now)).toBe("earlier today");
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npm test -- src/lib/format.test.ts`
Expected: FAIL — no exports named `mmss`, `usd`, `evs`, `shortId`, `humaniseTool`, `rawCall`, `dayLabel`.

- [ ] **Step 3: Add the formatters to `web/src/lib/format.ts`**

Insert below `dayMonth`, and add `import type { StreamSummary } from "./types";` at the top of the file:

```ts
/** `4:12` — a fuse, counted down. Never negative: a lapsed fuse reads `0:00`. */
export function mmss(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** `1.42`. The currency symbol belongs to the sentence around it, not here. */
export function usd(value: number): string {
  return value.toFixed(2);
}

/** `2.1` — the summed five-minute event rate. A silent house reads a bare `0`. */
export function evs(streams: Record<string, StreamSummary>): string {
  const total = Object.values(streams).reduce((sum, stream) => sum + (stream.rate_5m ?? 0), 0);
  return total === 0 ? "0" : total.toFixed(1);
}

/** `a91f` — enough of a request id to match one line against another. */
export function shortId(id: string): string {
  return id.slice(0, 4);
}

/** `home.lock_unlock` → `Lock unlock`. The Door's title. */
export function humaniseTool(toolName: string): string {
  const last = toolName.split(".").pop() ?? "";
  const words = last.replace(/_/g, " ").trim();
  if (!words) return "Action";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** `home.lock_unlock { entity_id: "lock.front_door", action: "unlock" }` — one mono line. */
export function rawCall(toolName: string, params: Record<string, unknown>): string {
  const entries = Object.entries(params).map(([key, value]) => `${key}: ${JSON.stringify(value)}`);
  return entries.length === 0 ? `${toolName} {}` : `${toolName} { ${entries.join(", ")} }`;
}

/** Timeline day divider: `earlier today` · `yesterday` · `4 Sep`. */
export function dayLabel(date: Date, now: Date): string {
  const startOfDay = (value: Date) =>
    new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (days <= 0) return "earlier today";
  if (days === 1) return "yesterday";
  return dayMonth(date);
}
```

Run: `npm test -- src/lib/format.test.ts`
Expected: `Test Files  1 passed (1)`, 27 tests (the Task 1 review added one `summarize()` case to this file).

- [ ] **Step 4: Add the overview fixtures**

Append to `web/src/test/fixtures.ts` (extend the type import with `Overview`). The stream stamps are 2026-09-07 UTC, the same day as the cost record and the test clocks; the first-morning house overrides everything that "running a while" implies, not just the streams:

```ts
/** A house that has been running a while: 2.1 ev/s, $1.42 of the $5 cap, reflex at 380 ms. */
export const overviewFixture: Overview = {
  redis: { connected: true },
  cost: { date: "2026-09-07", spend_usd: 1.42, cap_usd: 5, request_count: 38, avg_usd: 0.037 },
  dnd: { active: false },
  counts: { sessions: 1, devices: 1, deferred: 0, triggers: 6 },
  streams: {
    events: { length: 412, last_id: "1788814440000-0", last_ts: 1788814440000, rate_5m: 1.4 },
    user_requests: { length: 18, last_id: "1788814380000-0", last_ts: 1788814380000, rate_5m: 0.2 },
    reflex_observations: {
      length: 96,
      last_id: "1788814420000-0",
      last_ts: 1788814420000,
      rate_5m: 0.5,
    },
  },
  inference: { ollama: true, lmstudio: false },
  reflex: { model: "reflex-3b", last_ms: 380, p50_ms: 372 },
  librarian: {
    last_run_at: "2026-09-07T03:00:00Z",
    reviewed: 42,
    next_run_at: "2026-09-08T03:00:00Z",
  },
};

/**
 * The same house on its first morning: every stream empty, nothing spent, nothing
 * measured, no triggers written, the Librarian yet to run for the first time.
 */
export const firstRunOverviewFixture: Overview = {
  ...overviewFixture,
  cost: { date: "2026-09-07", spend_usd: 0, cap_usd: 5, request_count: 0 },
  counts: { sessions: 1, devices: 1, deferred: 0, triggers: 0 },
  streams: {
    events: { length: 0, last_id: null, last_ts: null, rate_5m: 0 },
    user_requests: { length: 0, last_id: null, last_ts: null, rate_5m: 0 },
    reflex_observations: { length: 0, last_id: null, last_ts: null, rate_5m: 0 },
  },
  reflex: { model: "reflex-3b", last_ms: null, p50_ms: null },
  librarian: { last_run_at: null, reviewed: null, next_run_at: "2026-09-08T03:00:00Z" },
};
```

- [ ] **Step 5: Write the failing StatusLine test**

Create `web/src/room/StatusLine.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { firstRunOverviewFixture, overviewFixture } from "@/test/fixtures";
import { StatusLine } from "./StatusLine";

const at2114 = new Date(2026, 8, 7, 21, 14);

describe("StatusLine", () => {
  it("reads the clock, the cap, the reflex and the rate while online", () => {
    render(<StatusLine overview={overviewFixture} online lastTrueAt={at2114} />);
    expect(
      screen.getByText("21:14 · cloud 1.42 / 5.00 · reflex 380 ms · 2.1 ev/s"),
    ).toBeInTheDocument();
  });

  it("stamps the last-known time and drops the rate while offline", () => {
    render(<StatusLine overview={overviewFixture} online={false} lastTrueAt={at2114} />);
    expect(screen.getByText("last true 21:14 · cloud 1.42 / 5.00 · reflex ok")).toBeInTheDocument();
  });

  it("says first run while every stream is empty", () => {
    render(<StatusLine overview={firstRunOverviewFixture} online lastTrueAt={at2114} />);
    expect(
      screen.getByText("first run · cloud 0.00 / 5.00 · reflex ok · 0 ev/s"),
    ).toBeInTheDocument();
  });

  it("stamps last true on a first run that is offline", () => {
    render(<StatusLine overview={firstRunOverviewFixture} online={false} lastTrueAt={at2114} />);
    expect(screen.getByText("last true 21:14 · cloud 0.00 / 5.00 · reflex ok")).toBeInTheDocument();
  });

  it("says reflex ok on a first run whatever the reflex reports", () => {
    render(
      <StatusLine
        overview={{
          ...firstRunOverviewFixture,
          reflex: { model: "reflex-3b", last_ms: 380, p50_ms: 372 },
        }}
        online
        lastTrueAt={at2114}
      />,
    );
    expect(
      screen.getByText("first run · cloud 0.00 / 5.00 · reflex ok · 0 ev/s"),
    ).toBeInTheDocument();
  });

  it("says reflex ok when nothing has been measured", () => {
    render(
      <StatusLine
        overview={{ ...overviewFixture, reflex: { model: null, last_ms: null, p50_ms: null } }}
        online
        lastTrueAt={at2114}
      />,
    );
    expect(screen.getByText(/reflex ok/)).toBeInTheDocument();
  });

  it("says cloud — when there is no cost record for today", () => {
    render(<StatusLine overview={{ ...overviewFixture, cost: null }} online lastTrueAt={at2114} />);
    expect(screen.getByText(/cloud — /)).toBeInTheDocument();
  });

  it("shows an unknown clock rather than a made-up one", () => {
    render(<StatusLine overview={undefined} online={false} lastTrueAt={null} />);
    expect(screen.getByText("last true --:-- · cloud — · reflex ok")).toBeInTheDocument();
  });

  it("does not call a degraded overview a first run", () => {
    // redis down: the shape is complete but `streams` is empty. That is unknown,
    // not "nothing has ever happened".
    render(
      <StatusLine
        overview={{ ...overviewFixture, streams: {}, cost: null }}
        online
        lastTrueAt={at2114}
      />,
    );
    expect(screen.getByText("21:14 · cloud — · reflex 380 ms · 0 ev/s")).toBeInTheDocument();
  });
});
```

Create `web/src/room/useOverview.test.tsx`. The hook is one of the two independent proofs that anything on screen is current (the other is a socket frame), so the stamp is what its test is about:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { overviewFixture } from "@/test/fixtures";
import { useOverview } from "./useOverview";

const { markTrueMock } = vi.hoisted(() => ({ markTrueMock: vi.fn() }));
vi.mock("@/shell/ConnectionProvider", () => ({ markTrue: markTrueMock }));

/** When set, the overview read 500s — Redis is down behind it. */
let overviewDown = false;

function renderOverview() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useOverview(), { wrapper });
}

describe("useOverview", () => {
  beforeEach(() => {
    overviewDown = false;
    markTrueMock.mockClear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        expect(String(input)).toBe("/api/admin/overview");
        if (overviewDown) return new Response('{"detail":"redis is down"}', { status: 500 });
        return new Response(JSON.stringify(overviewFixture), { status: 200 });
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("stamps last-true on every successful read: the house answered", async () => {
    const { result } = renderOverview();
    await waitFor(() => expect(result.current.data).toEqual(overviewFixture));
    expect(markTrueMock).toHaveBeenCalledTimes(1);
  });

  it("leaves last-true alone when the read fails", async () => {
    overviewDown = true;
    const { result } = renderOverview();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(markTrueMock).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 6: Run them to verify they fail**

Run: `npm test -- src/room`
Expected: FAIL — `Failed to resolve import "./StatusLine"` and `Failed to resolve import "./useOverview"`.

- [ ] **Step 7: Write the hook and the component**

Complete `web/src/room/useOverview.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Overview } from "@/lib/types";
import { markTrue } from "@/shell/ConnectionProvider";

/**
 * The Room's vitals. Polled rather than pushed: the overview aggregates Redis
 * reads that no stream announces. A successful read is also proof the house is
 * reachable, so it stamps last-true.
 */
export function useOverview() {
  return useQuery<Overview>({
    queryKey: ["overview"],
    queryFn: async () => {
      const overview = await api<Overview>("/api/admin/overview");
      markTrue();
      return overview;
    },
    refetchInterval: 30_000,
  });
}
```

Complete `web/src/room/StatusLine.tsx`:

```tsx
import { evs, hhmm, usd } from "@/lib/format";
import type { Overview } from "@/lib/types";

export interface StatusLineProps {
  overview: Overview | undefined;
  online: boolean;
  lastTrueAt: Date | null;
}

export function StatusLine({ overview, online, lastTrueAt }: StatusLineProps) {
  const streams = overview?.streams ?? {};
  // Every catalogued stream empty means nothing has ever happened here. An *absent*
  // streams map means Redis is down, which is a different thing entirely.
  const firstRun =
    Object.keys(streams).length > 0 && Object.values(streams).every((s) => s.length === 0);

  const cost = overview?.cost;
  const cloud = cost ? `cloud ${usd(cost.spend_usd)} / ${usd(cost.cap_usd)}` : "cloud —";

  const lastMs = overview?.reflex?.last_ms;
  const reflex =
    online && !firstRun && lastMs != null ? `reflex ${Math.round(lastMs)} ms` : "reflex ok";

  // Never a clock read during render: `lastTrueAt` is stamped on every socket open
  // and every successful poll, and an unknown clock says so.
  const stamp = lastTrueAt ? hhmm(lastTrueAt) : "--:--";

  // Offline outranks first run: §5.2's "live is not last-known" has no exception
  // for a house where nothing has happened yet.
  const parts = online
    ? [firstRun ? "first run" : stamp, cloud, reflex, `${evs(streams)} ev/s`]
    : [`last true ${stamp}`, cloud, reflex];

  return <div className="t-status">{parts.join(" · ")}</div>;
}
```

- [ ] **Step 8: Run the tests**

Run: `npm test -- src/room`
Expected: `Test Files  2 passed (2)` — 9 StatusLine tests and 2 for the hook.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  21 passed (21)`, `Tests  215 passed (215)`, no eslint output, `✓ built in …`.

- [ ] **Step 9: Commit**

```bash
git add web/src/lib/format.ts web/src/lib/format.test.ts web/src/room web/src/test/fixtures.ts
git commit -m "feat(web): the status line and the formatters it reads from"
```

---

### Task 14: Composition — the app boots into a Room

Everything built so far is assembled in the order the providers depend on each other: the query client outermost (`ConnectionProvider` and `AuthGate` both use it), then the theme, then the connection (so `AuthGate` can stamp the Denied gate), then the router, then the gate, then the routes. Both routes render the Room; `/actions/:id` differs only in what plan 1b opens on top of it.

The Room in this plan is a frame, not the Room: header, theme toggle, the greeting headline and the live status line. Plan 1b replaces the file.

**Files:**
- Create: `web/src/shell/QueryProvider.tsx`, `web/src/room/Room.tsx`, `web/src/App.test.tsx`
- Rewrite: `web/src/App.tsx`, `web/src/main.tsx`

- [ ] **Step 1: Write the failing composition test**

Create `web/src/App.test.tsx`. Four tests, each proving something only the composition can: the gate wraps the routes (a signed-out device stops at *Welcome back*), the stored theme reaches the document through the whole tree (the clock is pinned to 23:00, so nothing but the stored choice can make it light), and the deep link keeps its address (the catch-all route lands in the room too, so the heading alone would prove nothing):

```tsx
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY } from "@/lib/theme";
import { overviewFixture } from "@/test/fixtures";
import App from "./App";

vi.mock("@/lib/chat-socket", () => ({
  ChatSocket: class {
    onstatus = () => {};
    connect() {}
    close() {}
    listen() {
      return () => {};
    }
  },
}));
vi.mock("@/lib/telemetry-socket", () => ({
  TelemetrySocket: class {
    onstatus = () => {};
    connect() {}
    close() {}
    subscribe() {}
    listen() {
      return () => {};
    }
  },
}));

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/auth/status")
        return new Response('{"registered":true,"authenticated":true}', { status: 200 });
      if (url === "/api/admin/overview")
        return new Response(JSON.stringify(overviewFixture), { status: 200 });
      return new Response("{}", { status: 200 });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  window.history.pushState({}, "", "/");
  vi.useRealTimers();
});

describe("App", () => {
  it("boots an authenticated device straight into the room", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Listening, sir." })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Switch theme" })).toBeInTheDocument();
    expect(await screen.findByText(/cloud 1.42 \/ 5.00/)).toBeInTheDocument();
  });

  it("holds a signed-out device at the gate, short of the room", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"registered":true,"authenticated":false}', { status: 200 })),
    );
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Welcome back, sir." })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Listening, sir." })).not.toBeInTheDocument();
  });

  it("applies the stored theme to the document as it mounts", async () => {
    // Late at night, when the clock alone would say dark: only the stored choice
    // can make this light.
    vi.setSystemTime(new Date(2026, 8, 7, 23, 0));
    localStorage.setItem(THEME_KEY, "light");
    render(<App />);
    await screen.findByRole("heading", { name: "Listening, sir." });
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("renders the room on the action deep link, and keeps the address", async () => {
    window.history.pushState({}, "", "/actions/a91f");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Listening, sir." })).toBeInTheDocument();
    // The catch-all route also lands in the room; only the path tells them apart.
    expect(window.location.pathname).toBe("/actions/a91f");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/App.test.tsx`
Expected: FAIL — `App` renders `<main>Alfred</main>` (the Task 1 placeholder), so nothing the four tests look for is rendered.

- [ ] **Step 3: Write `web/src/shell/QueryProvider.tsx`**

Complete file:

```tsx
import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // One retry, and never for a 4xx: a 401, 403 or 404 is an answer, not a
        // failure to reach the house, and repeating it only doubles the noise.
        retry: (failureCount, error) =>
          !(error instanceof ApiError && error.status < 500) && failureCount < 1,
        staleTime: 10_000,
        refetchOnWindowFocus: true,
      },
    },
  });
}

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(makeClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
```

- [ ] **Step 4: Write `web/src/room/Room.tsx`** (plan 1b replaces this file wholesale)

Complete file:

```tsx
import { StatusLine } from "@/room/StatusLine";
import { useOverview } from "@/room/useOverview";
import { useConnection } from "@/shell/ConnectionProvider";
import { ThemeToggle } from "@/shell/ThemeToggle";

/**
 * Phase 1a: the Room's frame — header padding 72/24/0, the headline, the theme
 * toggle and the status line. The presence field, the timeline, the composer and
 * the Door arrive in plan 1b.
 */
export function Room() {
  const { online, lastTrueAt } = useConnection();
  const { data: overview } = useOverview();

  return (
    <main className="relative flex flex-1 flex-col overflow-hidden" style={{ background: "var(--bg)" }}>
      <header className="relative z-10 flex flex-col gap-1 px-6 pt-[72px]">
        <div className="flex items-end justify-between gap-3">
          <h1 className="t-headline">Listening, sir.</h1>
          <ThemeToggle />
        </div>
        <StatusLine overview={overview} online={online} lastTrueAt={lastTrueAt} />
      </header>
    </main>
  );
}
```

- [ ] **Step 5: Write `web/src/App.tsx`**

Complete file:

```tsx
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthGate } from "@/gates/AuthGate";
import { Room } from "@/room/Room";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { QueryProvider } from "@/shell/QueryProvider";
import { ThemeProvider } from "@/shell/ThemeProvider";

/**
 * Provider order is load-bearing: ConnectionProvider and AuthGate both read the
 * query client, and AuthGate reads the connection for the Denied gate's stamp.
 *
 * There is one screen. `/actions/:id` is the Room as well — a notification tap
 * must land on the approval it names (spec §6.3), which plan 1b opens over it.
 */
export default function App() {
  return (
    <QueryProvider>
      <ThemeProvider>
        <ConnectionProvider>
          <BrowserRouter>
            <AuthGate>
              <Routes>
                <Route path="/" element={<Room />} />
                <Route path="/actions/:id" element={<Room />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </AuthGate>
          </BrowserRouter>
        </ConnectionProvider>
      </ThemeProvider>
    </QueryProvider>
  );
}
```

- [ ] **Step 6: Write `web/src/main.tsx`**

Complete file:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import { installViewportVars } from "@/lib/viewport";

// Before the first paint: #root is sized from --app-height, and the composer's
// padding from --keyboard-inset. (installAudioUnlock() joins this in plan 1b,
// task 28 — iOS blocks audio until a gesture, so the unlock hook has to exist
// before the first tap, not before the first render.)
installViewportVars();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 7: Run the composition test and the whole suite**

Run: `npm test -- src/App.test.tsx`
Expected: `Test Files  1 passed (1)`, 4 tests.

Run: `npm test`
Expected: `Test Files  22 passed (22)`, `Tests  219 passed (219)`.

Run: `npm run lint`
Expected: no output.

Run: `npm run build`
Expected: `tsc -b` silent, `✓ built in …`, `dist/index.html` plus hashed assets.

- [ ] **Step 8: The Python SPA gate**

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
uv sync --all-extras
uv run pytest tests/core/channels/test_spa_ci.py -q
```

Expected: `2 passed`. If it prints `2 skipped`, `web/dist/index.html` is missing — run `npm run build` in `web/` first. This is the same gate CI runs after the `web` job uploads `web/dist`.

- [ ] **Step 9: Check the shape of what 1a leaves behind**

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
grep -rn "TODO\|FIXME\|placeholder" web/src --include='*.ts' --include='*.tsx' | grep -v "placeholder=" | grep -v "placeholder:"
```

Expected: no output (`placeholder=` on the credential inputs and `placeholder:` in the fixtures are the schema's own field, not a marker).

```bash
git log --oneline origin/master..HEAD
```

Expected: one `feat(web):` commit per task (Task 9 spans four), each followed by any `fix(web):` commits its reviews produced — every line a conventional-commit line, nothing merged from elsewhere.

- [ ] **Step 10: Commit**

```bash
git add web/src/App.tsx web/src/App.test.tsx web/src/main.tsx web/src/shell/QueryProvider.tsx web/src/room/Room.tsx
git commit -m "feat(web): compose the shell — providers, routes and the room's frame"
```

---

## Done

At this point the branch builds, lints, tests and serves. Deployed as it stands it would show:

- a first-run device: the three setup steps, ending in an empty Room;
- a registered, signed-out device: the sign-in gate with its remembered passkey line;
- a signed-in device: the Room's header — greeting, theme toggle, and a status line that tells the truth about being online, offline or brand new;
- a lapsed session or an off-network write: the Expired or Denied gate over whatever was already there.

**Do not open the PR yet.** Plan 1b (`docs/superpowers/plans/2026-09-07-pwa-phase1b-room-and-door.md`, tasks 15–28) continues on this branch and in this worktree, and fills the Room and the Door. The PR is opened at the end of 1b, titled:

```
feat(web): PWA client phase 1 — shell, gates, Room and Door
```

Handover to 1b — what exists now and must not be renamed:

| | |
|---|---|
| Types | everything in `src/lib/types.ts`, including `PendingAction`, `ActionResultEvent`, `NotificationEvent`, `Mood`, `Urgency` |
| Formatters | `hhmm`, `dayMonth`, `mmss`, `usd`, `evs`, `shortId`, `humaniseTool`, `rawCall`, `dayLabel` (plus the carried-over `summarize`, `timeOf`) |
| Shell | `QueryProvider`, `ThemeProvider`/`useTheme`, `ThemeToggle`, `ConnectionProvider`/`useConnection`/`markTrue`, `Layer`, `Sheet`, `presence.ts` (`usePresence`, `useModalFocus`, `riseStyle`, `riseClass`) |
| Gates | `Gate`, `GateField`, `StepList`, `SetupGate`, `SignInGate`, `ExpiredGate`, `DeniedGate`, `AuthGate` |
| Room | `Room` (to be replaced), `StatusLine`, `useOverview` |
| Lib | `api`/`post`/`put`/`del`/`ApiError`, `authEvents`, `auth.ts`, `webauthn.ts`, `ws.ts` (+ `pingIntervalMs`, `lastMessageAt`), `chat-socket.ts`, `telemetry-socket.ts`, `lifecycle.ts`, `viewport.ts` (+ `useKeyboardOpen`), `theme.ts` |
| CSS | the tokens, `.t-*` scale, `.rise-in`/`.rise-out`, `.gate-field`, `.pb-keyboard`, the seven keyframes |
| Fixtures | `integrationsFixture`, `attentionFixture`, `overviewFixture`, `firstRunOverviewFixture` |
| Still to build in 1b | `lib/{presence-signal,headline,history,actions,slide,recorder}.ts`, `lib/audio.ts` (rewrite), `room/*`, `sheets/HeldBackSheet.tsx`, `door/*`, `web/README.md`, `docs/superpowers/qa/2026-09-07-pwa-phase1-ios-checklist.md` |
