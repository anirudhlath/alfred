# PWA Phase 3 — Memory, Triggers and System

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 3 of spec §8 — the Workshop's other three benches. Memory shows episodic recall (hot and cold), the semantic documents, the routine lifecycle and the scratchpad; Triggers browses every trigger, filters by kind, toggles and fires them; System shows health, spend, quiet hours, sessions, connected services, passkeys, the reflex attention set and the nightly consolidation. Gate: *full capability parity with the old SPA*.

**Architecture:** Three benches, each a pure view over one hook, mounted by `WorkshopPanel` in place of the `not built yet · phase 3` placeholder. `lib/memory.ts`, `lib/triggers.ts` and `lib/system.ts` hold the types, the fetches and — the load-bearing part — the pure adapters that turn what the server actually sends into what a row can render. `useMemory`, `useTriggers` and `useSystem` are react-query hooks over those, each exporting a documented state interface the bench consumes and a test factory can fill. The Workshop's header status span becomes the live region the three new benches do not carry, and the Room gains the Held-back sheet route from System › Quiet.

**Tech Stack:** Unchanged. Vite 8 · React 19 · TypeScript strict · Tailwind v4 · TanStack Query 5 · react-router 7 · Vitest 4 + Testing Library + jsdom · ESLint 10. **No new dependencies. No backend changes.**

**Spec:** `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md` — §2 (hot vs cold, detected vs confirmed), §5.1 (Memory, Triggers, Notifications, Health, Operations, Identity), §5.2 (all six honesty rules), §8 (phase 3 gate), §10 (the gaps table and the closed vocabulary).
**Design handoff:** `docs/design/2026-09-04-pwa-client-handoff/README.md` §6 (Memory), §7 (Triggers), §8 (System), §2 (Held-back sheet). Copy is final and quoted verbatim in each task.
**Predecessors:** `…-pwa-phase1a-shell-and-gates.md`, `…-phase1b-room-and-door.md`, `…-pwa-phase2-workshop-and-activity.md`. Everything they export is used here as spelled there.

---

## Product decisions already taken (do not reopen)

1. **Phase 1 and 2's decisions all still hold** — public repo (`alfred.example.com`, `192.168.1.x`, never a real address), plain-text replies, 401/403 as gates, the closed status vocabulary, no virtualisation, colours from tokens only.
2. **Client only. No backend change, in this phase, for any reason.** The API is what it is on `origin/master`; where the design assumes a field that does not exist, this plan ships the honest sentence instead and logs the gap. A task that finds itself wanting to edit `core/` has misread the plan.
3. **Every bench is a pure view over one hook**, exactly as `ActivityBench` is over `Activity`. The bench fetches nothing. This is what makes a bench testable without a `QueryClient`, and it is the single convention most worth preserving.
4. **Hooks live in `WorkshopPanel`, not in the bench** — `useActivity` already does. A hook called in the panel keeps its state across a trip to another tab; a hook called inside the bench component dies with the tab. All four bench hooks are called side by side in the panel, and every one of them is cheap when its bench is not showing (see decision 5).
5. **A hook that is not showing does not poll.** Each of the three new hooks takes `enabled: boolean` and passes it to react-query. The panel passes `bench === "memory"` and so on. `useActivity` is the exception and stays as it is: its subscription is the feed, and phase 2 decided it runs while the Workshop is open.
6. **Fire-and-forget controls never move the row.** Trigger enable/disable, fire, drain, run consolidation and Save & test all return `{"status":"queued"}`. The control's own label becomes the status word, a note in `--accent` says when it was queued, and the row keeps its old state until a fresh read says otherwise. DND set/clear and ending a session are the two exceptions — direct writes the server confirms — and those do move.
7. **`--accent` is never success.** A queued note is accent because it is a decision waiting on the world, not because it went well. `--green` is only `alive` and `applied`. No red anywhere.
8. **The Workshop header status span becomes the live region.** Phase 2 left a comment saying so (`Workshop.tsx`): the Activity bench's `Feed status` banner unmounts with its bench, so on the other three nothing announces a socket drop. The span becomes `role="status"` and the banner in `ActivityBench` loses its region role to avoid two regions saying the same thing.
9. **Memory is read-only.** No forget, no redact, no semantic edit, no routine promote — none of it has a route. The bench says what it can and does not pretend to a button.
10. **Nothing is virtualised.** Consistent with phase 1 §4 and phase 2 §8: episodic browse is capped at 100 rows per store by the server, triggers are typically dozens, semantic files are a handful.

### Deviations from the handoff (deliberate; flag them in review)

Added while building: **`2.1 ev/s`, not the handoff's `2.1/s`** — `rateText` is shared with the Room status line and the Workshop header, where the handoff itself writes `ev/s`, and one number reading two ways is worse than the mismatch. **`resets 00:00` dropped** — the cost window rolls on UTC midnight, so the string is false outside UTC. **Health's four cards are internal borders in one `Section`**, not the handoff's four radius-12 cards with an 8 px gap; the mandated frame forces it. Backlog all three in task 11.

| # | Where | Handoff says | This plan ships | Why |
|---|---|---|---|---|
| 1 | Episodic, no results | `best score 0.31 · threshold 0.55 · 128 hot, 1 204 cold searched` | `nothing scored above the server's threshold` | `recall()` returns matches only. The score of the best *rejected* row, the threshold and the corpus sizes are not in the response and are not on any endpoint. Printing them would be invention. |
| 2 | Episodic, model pill | `model: ok` / `model: 503`, before searching | `model: unknown` until the first search answers, then `ok` or `503` | There is no readiness probe. The only way to learn the embedder is down is to search and be refused. |
| 3 | Episodic rows | one row shape | one row shape, built by an adapter | Browse-hot, browse-cold and search return three different shapes for the same row (`content` vs `summary`, comma-string vs JSON-string vs array, epoch-string vs REAL vs ISO). Task 1 normalises; nothing downstream sees the difference. |
| 4 | Episodic, hot rows | a row identity | key `hot:<index>` | Browse-hot rows are `HGETALL`'d with the Redis key discarded, so they carry no id at all. The list is replaced whole on every read, so an index key is correct here and nowhere else. |
| 5 | Triggers, meta | `one-shot · fires 08:40 tomorrow` | `one-shot · runs 08:40 tomorrow` from `run_at`; for a cron, `recurring · cron 0 19 * * 4` | `next_fire_time()` lives in the triggers process; the stored record has only `cron`/`run_at`. An `run_at` we can format honestly; a cron we can only print. |
| 6 | Triggers, corrupt record | `This record can't be read. / 500 · condition JSON fails to parse at byte 118` | the card renders only when a *mutation* answers 500, on the row that was toggled | `GET /api/admin/triggers` silently drops unparseable values, so a corrupt trigger is never in the list to draw a card for. The 500 is reachable only by toggling one that decayed since the read. |
| 7 | Triggers, kinds | `All · Time · Schedule · Sensor · Composite` | same five, with Time = `trigger_type "time"` carrying `run_at`, Schedule = `trigger_type "time"` carrying `cron`. **A record carrying both is a Time**, and the meta prints the `run_at`. | `trigger_type` only ever holds `time`/`sensor`/`composite`, so "Schedule" is a real distinction in the data but not a stored one. `run_at` wins the overlap because `TimeTrigger.next_fire_time` (`core/triggers/types/time.py`) returns from its `run_at` branch *unconditionally* and never falls through to the cron — such a record fires once and dies. Chipping it `schedule` would promise a recurrence that cannot happen. Nothing enforces one-field-only on the write side, so this is reachable. |
| 8 | System › Health | `bus · redis 6 services, 8 streams`; `reflex · reflex-3b · gpu 41%` | `bus · redis · <n> streams`; `reflex · <model>` | There is no service inventory endpoint and no GPU telemetry anywhere in the repo. The stream count is `Object.keys(overview.streams).length`. |
| 9 | System › Reach | a push-notification card in four states | **omitted**, with one line in the README saying it lands in phase 5 | Web Push has no backend (spec §6 is phase 5). A card whose only control cannot work is the same defect the phase-2 placeholder tabs were. |
| 10 | System › Sessions | `GET /api/admin/sessions` | `GET /api/auth/sessions` | There are two session lists. The admin one is *conversation* sessions with `turns`/`ttl_seconds`; the design's row (`passkey · pwa · signed in 07:02 · 192.168.1.24` + End) is the auth one, and `DELETE /api/auth/sessions/{id}` is its End. |
| 11 | System › Quiet | three DND states | same three, plus the footnote `a meeting in your calendar can also quiet Alfred; that is not shown here` | `overview.dnd` is the raw manual key. `DNDChecker` also honours calendar DND and lazily expires a stale manual window, and neither is visible over HTTP — so "off" here can be wrong. §5.2's whole point is that we say so. |
| 12 | System › Connected services | one `Save & test` | one button, two calls (`PUT` then `GET …/status`), and a distinct 403 | The `PUT` is network-gated while every read on the bench is session-gated, so credential editing fails off-LAN while the rest of the screen works. That 403 gets its own sentence, not the generic one. |
| 13 | System › Reflex | not specified by the handoff | a section built from the System card idiom | Spec §10 names `System › Reflex` for the attention set and gives no fidelity-locked design. `GET/PUT /api/admin/attention` exist; the section is caps label + radius-12 container + 56 px rows, like its neighbours. |
| 14 | Routines | confidence sparkline "last 8 consolidations" | shipped, captioned with the number of readings it really drew | `RoutineSpec.confidence_history` landed in phase 0b. The §10 row proposing we drop it is superseded by the build list under it. A caption reading "last 8" over three bars would be the invention the bench exists not to make. |
| 15 | Scratchpad | two stat cards: `14`/`episodes queued, unscored` and `03:00`/`next consolidation · last 03:00 today` | both, the second from `overview.librarian` | `pending_queue` covers the first; the second needs the Librarian's schedule, which is on the overview and not on `/memory/scratchpad`. `useMemory` reads the already-cached `useOverview()` rather than adding a poll. |
| 16 | Episodic, 503 card | `Embedding model is still loading.` / `503 · search by meaning unavailable · the list below is by recency` | line 2 verbatim; line 1 becomes `The embedder is not answering.` | A 503 out of `recall()` says one thing: nothing answered. Whether the embedder is still loading, has died or was never configured is not in the response, and "still loading" promises a wait that may never end. |
| 17 | Episodic rows, routine names, rail labels | `muted` for a cold row, a dormant/archived routine and the rail's stage names | `--fg2` | `--muted` is 3.46:1 on `--bg` in the light theme — under AA at 15 px as well as at 10 px. `BenchSwitcher` already made this substitution for the inactive benches (§4) and for the same reason; `--fg2` is 7.66:1 light / 9.36:1 dark and still reads a clear step back from `--fg`. |

---

## Before you start

1. **Worktree and branch.** This plan runs in `~/code/.worktrees/alfred/pwa-phase3-benches` on `feat/pwa-phase3-benches`, branched from `origin/master` at `fd2a5f4`. Never commit from `~/code/alfred-deploy/alfred` — a merge to `master` deploys.

```bash
cd ~/code/.worktrees/alfred/pwa-phase3-benches
git status --short
git log --oneline -1
```

Expected: no output from `status`, and `fd2a5f4 docs: the shell is the visual viewport, not the window (#243)` — or this plan's own `docs(web): plan PWA phase 3 …` commit above it.

2. **Confirm the client is green before adding to it.** `node_modules` is per-worktree; run `npm ci` first if `eslint: command not found`.

```bash
cd ~/code/.worktrees/alfred/pwa-phase3-benches/web
npm run lint && npx vitest run && npm run build
```

Expected: eslint prints nothing, `Test Files  54 passed (54)`, `Tests  837 passed (837)`, vite prints `✓ built in …`. A red baseline is master's problem, not yours. **`npx vitest run` from `web/`, never from the repo root.**

3. **How to run things** (all from `web/`):

| Command | What it does |
|---|---|
| `npx vitest run` | The whole suite, once |
| `npx vitest run src/lib/memory.test.ts` | One file |
| `npx vitest run -t "reads a cold row"` | One test by name |
| `npm run lint` | ESLint over `web/` |
| `npm run build` | `tsc -b` then `vite build` — the type check lives here, not in lint |

CI runs node 22 in UTC. Tests must not depend on the wall clock or the zone: construct dates with `new Date(y, m, d, h, mi, s)` and ids with `Date.UTC(...)`.

4. **Test-file and test counts as you go.** Master is at 54 files / 837 tests. Each task states the counts it should reach. A different number means a test was skipped or duplicated — find it before moving on.

5. **The API this plan calls.** Every path below is on `origin/master` today. Confirm before you start, because the whole plan rests on it:

```bash
cd ~/code/.worktrees/alfred/pwa-phase3-benches
grep -n 'memory/episodic\|memory/semantic\|memory/routines\|memory/scratchpad' core/channels/admin_api.py | head
grep -n 'triggers/{trigger_id}/enabled\|triggers/{trigger_id}/fire\|/dnd\|notifications/deferred\|notifications/drain\|librarian/run\|/attention' core/channels/admin_api.py | head -20
grep -n 'auth/sessions\|auth/credentials\|auth/pairing' core/identity/auth_routes.py | head
grep -n 'confidence_history' core/memory/schemas.py
```

Expected: all present. `confidence_history` is a `list[float]`, newest last, capped at `CONFIDENCE_HISTORY_LEN`.

6. **Secret hygiene before any push** — run the two `git grep` checks from the operator's exposure runbook (`~/code/alfred-deploy/PWA-EXPOSURE-RUNBOOK.md`, §"Secret hygiene", outside this repo). Both must print nothing. They are deliberately not quoted here: writing them into a public tree would commit the very literals they hunt for.

---

## Conventions

Identical to phases 1 and 2, repeated because they are load-bearing:

- **TDD, always.** Failing test → run it and read the failure → implement → run it green → commit.
- **Conventional commits**, one per task minimum.
- **Imports use the `@/` alias** everywhere except inside `src/lib/`, where siblings are relative (`./format`, `./types`).
- **Named exports** everywhere except `App.tsx`.
- **Colours come from CSS custom properties** — `style={{ color: "var(--muted)" }}`. Never a Tailwind palette colour. The decided pairs are not re-argued: accent-as-text is `--accent-text`; text on a filled accent is `--on-accent`; a filled dark button is `--ink` on `--paper`; a selected segment is `--field` on `--surface` with `--fg2` for the unselected labels.
- **Type comes from the `.t-*` classes.** `.t-meta` is decorative meta only — a stamp beside the line it stamps. **Anything a reader must take on its own to trust the screen is `.t-meta-strong`**, because `--muted` is 3.46:1 on `--bg` in light. Every status sentence in these three benches is load-bearing, so the default here is `.t-meta-strong`; `.t-meta` is the exception, not the rule.
- **A new token or colour pair must be restated in `src/test/contrast.ts`** or `token()` throws — and `TOKENS` is missing `line` and `muted`, so the pairs a control's *boundary* needs cannot currently be measured at all. Add them the first time a task needs one. A control that shows state (the trigger switch, the DND switch) needs 3:1 for both its indicator and its boundary, WCAG 1.4.11. This plan adds exactly one: **`--green-text`**, because phase 3 is the first place green is a *word* rather than a dot (the `model: ok` pill; System's `alive` in task 8) and raw `--green` is 2.29:1 on light `--bg`. Same reason `--accent-text` exists. `--green` still fills dots.
- **Safe areas via `env()`, never a literal.**
- **Every tappable element is ≥44 px tall**; smaller visuals grow their hit area with negative insets (`after:-inset-y-1`).
- **Tests live next to the source.** Each test file builds its own providers — a fresh `QueryClient` per file. A bench test needs no providers at all.
- **The status vocabulary is closed** (handoff): `queued`, `applied`, `last true HH:MM`, `unknown since HH:MM`, `hot / cold`, `candidate · active · dormant · archived`, `expired · not done`, `takes effect within 60 s`. Always mono, always lower case. Do not invent new words for system state.
- **No placeholders, no dead code.**

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `web/src/lib/memory.ts` | Episodic/semantic/routine/scratchpad types, the three-way episodic adapter, fetches, row formatters | 1 |
| `web/src/workshop/useMemory.ts` | Sub-tab, browse/search, the four reads, the model pill's state | 2 |
| `web/src/workshop/MemoryBench.tsx` | Sub-tabs, Episodic list + search footer, Semantic cards, Scratchpad | 3 |
| `web/src/workshop/RoutineRow.tsx` | One routine: line, trend, lifecycle rail, expanded detail, sparkline | 3 |
| `web/src/lib/triggers.ts` | Trigger types, `triggerKind`, `triggerMeta`, list/toggle/fire fetches | 4 |
| `web/src/workshop/useTriggers.ts` | List, kind filter, expand, the pending map, the 60 s re-read | 5 |
| `web/src/workshop/TriggerRow.tsx` | One trigger: kind label, name, meta, switch, expanded payload, Fire now | 6 |
| `web/src/workshop/TriggersBench.tsx` | Kind chips, list, empty state, the footer note | 6 |
| `web/src/lib/system.ts` | Auth sessions, credentials, pairing, integrations, attention: types + fetches + formatters | 7 |
| `web/src/workshop/useSystem.ts` | The System bench's one hook over overview + five reads and their writes | 7 |
| `web/src/workshop/SystemBench.tsx` | Section frame, Health, Quiet, Maintenance | 8 |
| `web/src/workshop/SystemSections.tsx` | Sessions, Connected services, Devices & identity, Reflex | 9 |
| `web/src/workshop/Workshop.tsx` | Mount the three benches, delete `UNBUILT`, move the live region into the header | 10 |
| `web/src/workshop/ActivityBench.tsx` | Drop the duplicate `role="status"` on the feed banner | 10 |
| `web/src/room/Room.tsx` | Hold the Held-back sheet opened from System › Quiet | 10 |
| `web/src/lib/types.ts`, `web/src/test/fixtures.ts` | Server-mirroring types and the payload fixtures the tests share | 1, 4, 7 |
| `web/README.md`, `docs/web-frontend.md`, `docs/backlog/low/pwa-phase3-followups.md`, `docs/superpowers/qa/2026-09-16-pwa-phase3-ios-checklist.md` | Docs, backlog, QA | 11 |

### The contract between tasks (names used exactly as spelled)

```ts
// lib/memory.ts
export type MemoryStore = "hot" | "cold";
export interface EpisodicRow { key: string; id: string | null; store: MemoryStore; text: string;
  at: number | null; significance: number | null; recalled: number; lastRecalled: number | null;
  entities: string[]; score: number | null; decaying: boolean }
export interface SemanticFile { name: string; dir: "preferences" | "profile"; content: string; modified: string }
export interface RoutineStep { description: string; action: { tool_name: string; target_service: string; parameters: Record<string, unknown> } | null }
export type RoutineState = "candidate" | "active" | "dormant" | "archived";
export interface Routine { name: string; trigger_pattern: string; steps: RoutineStep[]; confidence: number;
  learned_from: string[]; state: RoutineState; last_hit: string | null; consecutive_misses: number;
  last_suggested: string | null; confidence_history: number[] }
export interface Scratchpad { content: string; pending_queue: number }
export const ROUTINE_STAGES = [...] as const satisfies readonly RoutineState[];   // candidate · active · dormant · archived
export function toEpisodicRow(raw: Record<string, unknown>, index: number): EpisodicRow;
export function episodicMeta(row: EpisodicRow, now: number): string;   // `now` is for dayLabel: "20:52 today · …"
export function routineTrend(routine: Routine): { text: string; rising: boolean };
export function routineDetail(routine: Routine): string;
export function fetchEpisodic(query: string): Promise<EpisodicRow[]>;
export function fetchSemantic(): Promise<SemanticFile[]>;
export function fetchRoutines(): Promise<Routine[]>;
export function fetchScratchpad(): Promise<Scratchpad>;

// workshop/useMemory.ts
export type MemoryTab = "episodic" | "semantic" | "routines" | "scratchpad";
export type ModelState = "unknown" | "ok" | "503";
export interface Memory { tab; setTab; query; setQuery; submit; searching;
  /** A search *answered*, not merely was typed — so a refused or pending search never claims the "nothing matched" empty state. */
  searched: boolean;
  rows: EpisodicRow[]; model: ModelState; files: SemanticFile[]; routines: Routine[];
  scratchpad: Scratchpad | null; openRoutine: string | null; toggleRoutine; loading; error: string | null }
export function useMemory(enabled: boolean): Memory;

// lib/triggers.ts
export type TriggerType = "time" | "sensor" | "composite";
export type TriggerKind = "time" | "schedule" | "sensor" | "composite";
export const TRIGGER_KINDS: readonly ["all", "time", "schedule", "sensor", "composite"];
export interface Trigger { trigger_id; trigger_type: TriggerType; name; enabled; one_shot; created_by;
  created_at; last_fired: string | null; action; urgency; conditions: Record<string, unknown> }
export function triggerKind(trigger: Trigger): TriggerKind;
export function triggerMeta(trigger: Trigger, now: number): string;
export function fetchTriggers(): Promise<Trigger[]>;
export function setTriggerEnabled(id: string, enabled: boolean): Promise<void>;
export function fireTrigger(id: string): Promise<void>;

// workshop/useTriggers.ts
export const REREAD_MS = 60_000;
export interface Pending { kind: "enabling" | "disabling" | "firing"; at: number; error?: string; status?: number }
export interface Triggers { kind; setKind; triggers: Trigger[]; shown: Trigger[]; open: string | null;
  toggleOpen; pending: Record<string, Pending>; toggle(t: Trigger); fire(t: Trigger);
  /** When *this client queued* a fire — never when the trigger ran, which no read reports. */
  fired: Record<string, number>;
  loading; error: string | null }
export function useTriggers(enabled: boolean): Triggers;

// lib/system.ts
export interface AuthSession { session_id; credential_id; device_name; channel; ip; user_agent; created_at; expires_in: number; current: boolean }
export interface Credential { credential_id; device_name; transports: string[]; created_at; last_used_at: string | null; current: boolean }
export interface Integration { name; category; kind: "adapter" | "service"; schema: { fields: Record<string, IntegrationField> }; configured: Record<string, boolean> }
export interface IntegrationStatus { name: string; healthy: boolean; latency_ms: number | null }
export interface AttentionDomain { domain: string; members: string[]; seen: string[] }
export type ServiceState = "ok" | "failed" | "unset" | "testing" | "queued";
export const HOME_SERVICE = "home-service";
export function sessionMeta(session: AuthSession, now: number): string;   // `now` for dayLabel, as triggerMeta
export function credentialMeta(credential: Credential, now: number): string;
export function serviceNote(state: ServiceState): string;
export function spendNote(cost: Overview["cost"]): string;
export function spendFraction(cost: Overview["cost"]): number;   // clamped [0,1]; 0 past a zero or missing cap
export function fetchAuthSessions(): Promise<AuthSession[]>;
export function endAuthSession(id: string): Promise<void>;
export function fetchCredentials(): Promise<Credential[]>;
export function mintPairingCode(): Promise<{ code: string; expires_at: string }>;
export function fetchIntegrations(): Promise<Integration[]>;
export function fetchIntegrationStatus(name: string): Promise<IntegrationStatus>;
export function saveCredentials(name: string, values: Record<string, string>): Promise<void>;
export function fetchAttention(): Promise<AttentionDomain[]>;
export function putAttention(domain: string, allow: string[], ask: string[]): Promise<AttentionDomain>;
export function setDnd(active: boolean, until: string | null): Promise<void>;
export function drainDeferred(): Promise<void>;
export function runLibrarian(): Promise<void>;

// workshop/useSystem.ts
export interface System { overview; health; quiet; sessions; credentials; integrations; attention;
  pairing; maintenance; loading; error: string | null }
/** `credentials` is the **passkey** list. Saving a *service's* credentials lives at
 *  `integrations.saves[name]` = `{ saving, savedAt, error, gated }` — one service refusing
 *  says nothing about the others, and one field cannot mean both things.
 *  Each `health` cell is `{ value, note, alive: boolean }`; the flag exists so the view's
 *  green-vs-muted dot never has to string-match on the word "alive". */
export function useSystem(enabled: boolean, onHeld: () => void): System;   // second parameter added by task 8

// workshop/Workshop.tsx
export function Workshop({ open; onClose; onWhy: (ref: StreamRef) => void; onHeld: () => void }): JSX
```

---

## Task 1: `lib/memory.ts` — the types, the adapter, the reads

The load-bearing task of the Memory bench. `GET /api/admin/memory/episodic` answers in **three different shapes** for what the design draws as one row, and every one of them is confirmed in the server source:

| | browse · hot (`CONTEXT_PREFIX` hash) | browse · cold (SQLite row) | search (`EpisodicResult`) |
|---|---|---|---|
| identity | **absent** — the Redis key is discarded | `id` | `id` |
| the sentence | `content` | `summary` | `summary` |
| time | `timestamp`, epoch seconds **as a string** | `timestamp`, epoch seconds as a REAL | `timestamp`, an **ISO 8601 string** |
| entities | `"lamp,kitchen"` | `"[\"lamp\",\"kitchen\"]"` | `["lamp","kitchen"]` |
| significance | string | **JSON text** `{"overall": …}` (v2 migration column) | **object** `{"overall": …}` (`SignificanceScore`) |
| recall | `retrieval_count` as a string, `last_retrieved` as `0.0` when never | absent | `retrieval_count`, `last_retrieved` |
| score | absent | absent | `score` |
| `store` | `"hot"` | `"cold"` | `"hot"` or `"cold"` |

One adapter absorbs all of it, so nothing above this file ever branches on a store again.

**Files:**
- Create: `web/src/lib/memory.ts`
- Create: `web/src/lib/memory.test.ts`
- Modify: `web/src/test/fixtures.ts` (add `hotRow`, `coldRow`, `searchRow`, `routine`, `semanticFile`)

- [ ] **Step 1: Write the failing tests for the adapter**

`web/src/lib/memory.test.ts`. Build the three raw shapes literally — do not import a helper that hides them, the point of the test is that the shapes differ:

```ts
import { describe, expect, it } from "vitest";
import { episodicMeta, routineDetail, routineTrend, toEpisodicRow } from "./memory";
import type { Routine } from "./memory";

const AT = Date.UTC(2026, 8, 16, 7, 2, 0);          // 2026-09-16T07:02:00Z
const HOT = {
  type: "episodic", store: "hot", content: "Kitchen lamp turned off",
  semantic_key: "kitchen lamp", source: "system1_action", entities: "lamp,kitchen",
  timestamp: String(AT / 1000), significance: "0.7", retrieval_count: "3",
};
const COLD = {
  store: "cold", id: "ep-91", timestamp: AT / 1000, source: "conversation",
  summary: "Asked about the dentist", entities: '["dentist"]', valence: "neutral",
  significance: 0.4, semantic_key: "dentist",
};
const FOUND = {
  store: "cold", score: 0.62, id: "ep-91", timestamp: "2026-09-16T07:02:00Z",
  source: "conversation", summary: "Asked about the dentist", entities: ["dentist"],
  significance: 0.4, semantic_key: "dentist", retrieval_count: 2,
  last_retrieved: "2026-09-16T08:00:00Z", compressed_into: null, valence: "neutral",
};

describe("toEpisodicRow", () => {
  it("reads a hot row's content, comma entities and string numbers", () => {
    const row = toEpisodicRow(HOT, 0);
    expect(row).toMatchObject({
      store: "hot", text: "Kitchen lamp turned off", at: AT,
      significance: 0.7, recalled: 3, entities: ["lamp", "kitchen"], score: null,
    });
  });

  it("gives a hot row an index key, because the server discards its id", () => {
    expect(toEpisodicRow(HOT, 4)).toMatchObject({ key: "hot:4", id: null });
  });

  it("reads a cold row's summary and JSON entities", () => {
    const row = toEpisodicRow(COLD, 0);
    expect(row).toMatchObject({
      store: "cold", id: "ep-91", key: "ep-91", text: "Asked about the dentist",
      at: AT, significance: 0.4, recalled: 0, entities: ["dentist"],
    });
  });

  it("reads a search row's ISO timestamp, array entities and score", () => {
    const row = toEpisodicRow(FOUND, 0);
    expect(row).toMatchObject({ at: AT, entities: ["dentist"], score: 0.62, recalled: 2,
      lastRecalled: Date.UTC(2026, 8, 16, 8, 0, 0) });
  });

  it("survives every field being missing", () => {
    const row = toEpisodicRow({ store: "hot" }, 2);
    expect(row).toEqual({ key: "hot:2", id: null, store: "hot", text: "", at: null,
      significance: null, recalled: 0, lastRecalled: null, entities: [], score: null,
      decaying: false });
  });

  it("treats an unparseable entities string as no entities, not as one entity", () => {
    expect(toEpisodicRow({ ...COLD, entities: "[oops" }, 0).entities).toEqual([]);
  });

  it("drops empty entity fragments", () => {
    expect(toEpisodicRow({ ...HOT, entities: "lamp,,kitchen," }, 0).entities)
      .toEqual(["lamp", "kitchen"]);
  });

  it("calls a cold row with low significance and no recalls decaying", () => {
    expect(toEpisodicRow({ ...COLD, significance: 0.2 }, 0).decaying).toBe(true);
    expect(toEpisodicRow({ ...COLD, significance: 0.8 }, 0).decaying).toBe(false);
    expect(toEpisodicRow({ ...FOUND, significance: 0.2 }, 0).decaying).toBe(false);
  });
});
```

`decaying` is `store === "cold" && significance !== null && significance < DECAY_FLOOR && recalled === 0`. It is the only derived judgement in the file and it exists because the design's cold rows carry a `decaying` note; keep the rule in one place so the bench never re-derives it.

Then the meta line and the routine formatters:

```ts
describe("episodicMeta", () => {
  const now = Date.UTC(2026, 8, 16, 9, 0, 0);
  it("stamps, names the store and counts recalls", () => {
    expect(episodicMeta(toEpisodicRow(HOT, 0), now)).toBe("07:02 · hot · recalled 3×");
  });
  it("says never recalled rather than 0×", () => {
    expect(episodicMeta(toEpisodicRow(COLD, 0), now)).toBe("07:02 · cold · never recalled");
  });
  it("adds the match score when one is in the row", () => {
    expect(episodicMeta(toEpisodicRow(FOUND, 0), now)).toBe(
      "07:02 · cold · recalled 2× · match 0.62");
  });
  it("says decaying instead of a recall count when the row is decaying", () => {
    expect(episodicMeta(toEpisodicRow({ ...COLD, significance: 0.2 }, 0), now))
      .toBe("07:02 · cold · decaying");
  });
  it("says --:-- for a row with no readable time", () => {
    expect(episodicMeta(toEpisodicRow({ store: "hot" }, 0), now)).toMatch(/^--:-- · hot/);
  });
});

describe("routineTrend", () => {
  const base: Routine = { name: "evening-lights", trigger_pattern: "sunset", steps: [],
    confidence: 0.82, learned_from: ["ep-1"], state: "active", last_hit: "2026-09-15T19:02:00Z",
    consecutive_misses: 0, last_suggested: null, confidence_history: [0.6, 0.71, 0.82] };
  it("reports a rise against the previous consolidation", () => {
    expect(routineTrend(base)).toEqual({ text: "0.82 · +0.11 since last week", rising: true });
  });
  it("reports a fall", () => {
    expect(routineTrend({ ...base, confidence_history: [0.9, 0.82] }))
      .toEqual({ text: "0.82 · -0.08 since last week", rising: false });
  });
  it("says nothing about a trend it has only one reading for", () => {
    expect(routineTrend({ ...base, confidence_history: [0.82] }))
      .toEqual({ text: "0.82 · first reading", rising: false });
  });
  it("treats an empty history as a first reading rather than reading confidence twice", () => {
    expect(routineTrend({ ...base, confidence_history: [] }).text).toBe("0.82 · first reading");
  });
});

describe("routineDetail", () => {
  it("counts the steps and the evidence", () => {
    expect(routineDetail({ ...base, steps: [{ description: "a", action: null },
      { description: "b", action: null }] })).toBe("2 steps · learned from 1 memory");
  });
  it("pluralises the evidence and says none when there is none", () => { /* 2 memories; 0 -> "no evidence kept" */ });
  it("says 1 step, not 1 steps", () => { /* … */ });
});
```

The trend text says **"since last week"** because consolidation is the Librarian's nightly-into-weekly pass, and the handoff's routine card says exactly that. Use `confidence` for the headline number, not `history.at(-1)` — history is what the *last* pass recorded and `confidence` is current.

- [ ] **Step 2: Run them and watch every one fail**

```bash
cd web && npx vitest run src/lib/memory.test.ts
```

Expected: `Failed to resolve import "./memory"`.

- [ ] **Step 3: Write `lib/memory.ts`**

Types exactly as the contract block spells them. The adapter, in full — the coercions are the whole point, so write them as helpers and reuse them:

```ts
/** Below this, with nothing ever recalling it, a cold row is on its way out. */
const DECAY_FLOOR = 0.3;

const num = (value: unknown): number | null => {
  const parsed = typeof value === "string" ? Number(value) : value;
  return typeof parsed === "number" && Number.isFinite(parsed) ? parsed : null;
};

/** Epoch seconds (number or string) or an ISO string, all to epoch ms. */
const time = (value: unknown): number | null => {
  const seconds = num(value);
  if (seconds !== null) return Math.round(seconds * 1000);
  if (typeof value !== "string") return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
};

const entityList = (value: unknown): string[] => {
  if (Array.isArray(value)) return value.filter((e): e is string => typeof e === "string");
  if (typeof value !== "string" || value === "") return [];
  if (value.startsWith("[")) {
    try {
      const parsed: unknown = JSON.parse(value);
      return Array.isArray(parsed) ? parsed.filter((e): e is string => typeof e === "string") : [];
    } catch {
      return [];       // Half a JSON array is no entities, never one entity called "[oops".
    }
  }
  return value.split(",").map((e) => e.trim()).filter(Boolean);
};
```

`toEpisodicRow` then reads `store` (defaulting `"hot"`), `text` from `summary ?? content`, `key` from `id ?? \`${store}:${index}\``, and the rest through the helpers. **`num` must run before `Date.parse`**, or `Date.parse("1758006120")` silently becomes the year 1758 in some engines.

`episodicMeta(row, now)`: `hhmm(row.at)` or `--:--`, then the store, then `decaying` / `never recalled` / `recalled N×`, then `match X.XX` when `score !== null` — joined with ` · `. Import `hhmm` from `./format` (relative: this is inside `lib/`). `now` is taken and unused for the stamp today; it is in the signature because the bench will want `dayLabel` here the moment a row can be older than today, and changing a call site is worse than an unused parameter. **Do not add a parameter you do not use** — if the test above does not need `now`, drop it from the signature and from the contract block, and say so in the commit.

The four fetches unwrap the server's envelope and nothing else:

```ts
export const fetchSemantic = async (): Promise<SemanticFile[]> =>
  (await api<{ files: SemanticFile[] }>("/api/admin/memory/semantic")).files;
```

`fetchEpisodic(query)` calls `/api/admin/memory/episodic` with `?q=` (URL-encoded) when the query is non-empty, then maps `entries` through `toEpisodicRow`. It does **not** catch the 503 — `useMemory` needs to see it to set the model pill.

- [ ] **Step 4: Green, then lint and typecheck**

```bash
cd web && npx vitest run src/lib/memory.test.ts && npm run lint && npm run build
```

Expected: ~22 passing, eslint silent, build clean.

- [ ] **Step 5: Add the fixtures other tasks will share**

In `web/src/test/fixtures.ts`, export `hotRow`, `coldRow`, `searchRow` (the three raw objects above, as functions taking an overrides object, matching whatever convention that file already uses), plus `routine(overrides)` and `semanticFile(overrides)`. Tasks 2 and 3 import these; do not let them re-declare their own.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/memory.ts web/src/lib/memory.test.ts web/src/test/fixtures.ts
git commit -m "feat(web): episodic row adapter and memory reads"
```

---

## Task 2: `useMemory` — one hook, four reads, an honest model pill

**Files:**
- Create: `web/src/workshop/useMemory.ts`, `web/src/workshop/useMemory.test.tsx`
- Modify: `web/src/shell/ConnectionProvider.tsx` (`REHYDRATE_KEYS`)

The sub-tab is hook state, so a trip to Triggers and back comes home to the tab you left. The three non-episodic reads are `enabled: enabled && tab === …` — a bench nobody is looking at must not glob a directory on the server.

**Search is submit-only.** Typing does not search: every keystroke would be a `recall()` that embeds a query on the GPU. `submit()` is the form's `onSubmit`. An empty query submits as a browse.

- [ ] **Step 1: Write the failing tests**

`web/src/workshop/useMemory.test.tsx`, using this file's own wrapper (fresh `QueryClient`, `retry: false`) and `renderHook` from `@testing-library/react`, with `fetch` stubbed per test. Cover:

1. `starts on episodic with an unknown model and no search` — `tab === "episodic"`, `model === "unknown"`, `searched === false`.
2. `browses without a q parameter` — asserts the URL has no `?q=`.
3. `searches with an encoded q on submit, not on typing` — `setQuery("dentist & co")`, assert no fetch, then `submit()`, assert `q=dentist%20%26%20co` (or `+`, whichever `URLSearchParams` produces — assert on the parsed param, not the raw string).
4. `turns the model pill green only once a search has answered` — after a successful search, `model === "ok"`.
5. `reads a 503 as the embedder being down, and says so without losing the rows` — a 503 from a search leaves `model === "503"`, `error` set to the server's detail, and `rows` still holding the previous browse. **This is the honesty test of the whole bench: a failed search must not blank the screen.**
6. `a later successful search clears the 503` — back to `ok`.
7. `does not read semantic while the tab is episodic` — one fetch, to episodic.
8. `reads semantic when the tab changes to semantic`.
9. `reads nothing at all when the bench is disabled` — `useMemory(false)`, zero fetches.
10. `keeps the tab across a disable and re-enable` — rerender with `false` then `true`, tab still `routines`.
11. `opens one routine at a time and closes the open one` — `toggleRoutine("a")`, `openRoutine === "a"`; again → `null`; `toggleRoutine("b")` → `"b"`.
12. `reports the first error it has, not a stale one` and `loading is true only while something is in flight`.
13. `does not poll` — advance timers 60 s with `vi.useFakeTimers`, fetch count unchanged. (React-query needs no `refetchInterval` here; memory changes at consolidation speed, not at chat speed.)

- [ ] **Step 2: Run them; expect the import to fail**

```bash
cd web && npx vitest run src/workshop/useMemory.test.tsx
```

- [ ] **Step 3: Implement**

Four `useQuery` calls keyed `["memory","episodic",submitted]`, `["memory","semantic"]`, `["memory","routines"]`, `["memory","scratchpad"]`. `submitted` is the *submitted* query string, separate from the `query` the field holds; `submit()` copies one into the other and sets `searched`. The model pill is `useState<ModelState>`, driven from the episodic query's result and error: success while `submitted !== ""` → `"ok"`; an `ApiError` with `status === 503` → `"503"`. Do not derive it from `error` alone — a 401 is a gate, not a dead embedder.

Keep the last good rows: read `episodic.data ?? []`, and set `placeholderData: (prev) => prev` so a failed search does not empty the list. `error` is `errorText(first error)`.

- [ ] **Step 4: Add the keys to `REHYDRATE_KEYS`** — `"memory"` here; `"triggers"` and `"system"` land with task 7, which must also update the exact-list assertion in `ConnectionProvider.test.tsx`.

`web/src/shell/ConnectionProvider.tsx` line ~61. Add `"memory"` so returning to the foreground re-reads what is on screen. The list is prefix-matched — confirm that in the file before assuming; if it matches exact keys, add all four.

- [ ] **Step 5: Green + lint + build. Step 6: Commit** `feat(web): the Memory bench's hook`

---

## Task 3: `MemoryBench` and `RoutineRow`

**Files:**
- Create: `web/src/workshop/MemoryBench.tsx`, `web/src/workshop/MemoryBench.test.tsx`
- Create: `web/src/workshop/RoutineRow.tsx`, `web/src/workshop/RoutineRow.test.tsx`

`MemoryBench({ memory }: { memory: Memory })` — a pure view, no `QueryClient` in its tests.

**Layout (handoff §6).** A sub-tab row under the bench switcher: four **44 px pills, radius 22**, the active one filled `--paper` on `--ink`, the rest `--fg2`. This is a different control from `BenchSwitcher`'s `--field` segments — the handoff specifies it separately — so share only the keyboard logic (roving tabindex, wrapping arrows, `Home`/`End`) by extracting it to `workshop/tabs.ts` and having `BenchSwitcher` use it too, rather than copying thirty lines of a11y-critical code. Below the pills, `flex-1 overflow-y-auto` content with `padding 12 16 40`, gap 12. The tabpanel always carries `tabIndex={0}`, for the reason `Workshop.tsx` writes out: Scratchpad holds nothing focusable, ever.

**Each tab opens with its note**, `.t-meta-strong`, verbatim from the handoff:

- Episodic: `Browsing here does not count as recall. Nothing you open is kept warmer or colder for it.`
- Semantic: `Human-readable documents the conscious mind reads before every reply. Rewritten by the nightly consolidation.`
- Routines: `Patterns Alfred noticed on its own. Ignored suggestions lose confidence and slide right until archived.`
- Scratchpad: `Working notes Alfred keeps between consolidations. The nightly pass reads them and rewrites semantic memory.` *(invented in the handoff's voice — the handoff says "note" without quoting one.)*

Episodic's is the most important sentence on the bench: it is the reason the endpoint passes `update_stats=False`, and a memory browser that silently reinforced what you looked at would corrupt the decay it is showing you.

**Episodic.** Rows with a 1 px `--line` top and `padding 11 0`: an 8 px circle with a 1.5 px `--accent` border, **filled for hot and transparent for cold**, `aria-hidden`; the text at 14.5 px, **cold rows in `--fg2`**; then `episodicMeta(row, now)` in `.t-meta-strong` — `20:52 today · significance 0.62 · recalled 1× · hot`, the store last, `· match 0.62` appended for a search row. The entities go on a third line in `.t-meta` only when there are any: decorative, because the line above already says everything load-bearing.

A pinned footer holds the search: a 50 px `type="search"` field (`aria-label="Search memory"`, placeholder `Search by meaning`, **≥16 px or iOS zooms on focus**) inside a `<form>` whose submit calls `memory.submit()`, and beside it the model pill — `model: unknown` / `model: ok` / `model: 503`, mono, `--green-text` only for `ok`.

Empty and error states, exactly:
- searched, nothing back → `Nothing close enough to "<the submitted query>".` on line 1, then `searched by meaning · the server does not report what it rejected` on line 2. **Quote `submitted`, not `query`** — the field drifts the moment the reader types again.
- browsing, nothing there → `No episodic memories yet.`
- `model: 503` → a `--surface` card reading `The embedder is not answering.` / `503 · search by meaning unavailable · the list below is by recency`, **and the previous rows stay on screen below it.**

**Semantic.** One `--surface` card per file, radius 12, padding 14: the file name at 15 px/500, `dir · modified <day> HH:MM` in mono `.t-meta-strong` (a day label as well as a clock — these are rewritten nightly-into-weekly, so a bare clock on a three-week-old file is quietly false), then the content at 14/1.55 in `--fg2`, `pre-line`, clamped to 12 lines with a `Show all` / `Show less` toggle (a real `<button>`, ≥44 px). The clamp and the button are gated on the same estimate, so nothing is ever hidden without a way past it. Empty: `No semantic memory files yet.`

**Scratchpad.** The note, then a `--surface` mono 12/1.65 `pre-wrap` block of the working notes, then two stat cards (1 px `--line`, radius 12): `<pending_queue>` / `episodes queued, unscored`, and `<next consolidation>` / `next consolidation · last 03:00 today`. The second reads `overview.librarian` through `Memory.consolidation` — `useMemory` calls the already-cached `useOverview(false)`, which adds no request — and says `never run` rather than inventing a stamp. Never hide a zero queue; an empty queue is a fact worth stating. Empty content: `The scratchpad is empty.`

**Routines** delegate to `RoutineRow`. Empty: `No routines learned yet.`

### `RoutineRow({ routine, open, onToggle })`

Rows `padding 12 0`. Collapsed: the name at 14.5 px, **`--fg2` for a dormant or archived routine**; `routineTrend(routine).text` in mono on the right, **`--accent-text` when rising** and `--fg2` when not (the handoff's "accent when rising" — a routine losing confidence is the system working, so nothing here is red); and the lifecycle rail.

**The rail** is four columns, each a 3 px bar with a **visible mono 10 px label** under it — `candidate · active · dormant · archived`. `--accent` for the current stage, `--muted` for the stages passed, `--line` for those ahead. Because the labels are visible and name the stage, no `sr-only` duplicate is needed.

Expanded (the row is a `<button>` with `aria-expanded`): `routineDetail(routine)` in a mono detail block, then the steps as an **ordered list with rendered ordinals** — a routine's steps are order-bearing — each `description` with `humaniseTool(action.tool_name)` beside the ones that have an action. Then the sparkline.

**The sparkline**: up to 8 bars from `confidence_history.slice(-8)`, 3 px wide, 2 px apart, **28 px tall**, `--accent` at 0.7 opacity except the last, which is full. A reading of 0 still gets a floor bar, or the picture loses a consolidation that happened. `role="img"` with `aria-label={\`confidence over the last ${n} consolidations, now ${confidence}\`}`, and a visible `aria-hidden` caption `confidence, last <n> consolidations` — **counting the bars actually drawn**, not a flat 8. Fewer than 8 readings draws what it has, left-aligned; **zero readings draws nothing at all** rather than a flat line, because a flat line is a claim.

- [ ] **Step 1: Write `RoutineRow.test.tsx` first** (~14): renders the name and trend; rising is `--accent-text` and falling is not red (assert the style value, as the other bench tests do); a dormant routine's name is dimmed; the rail's visible labels mark the current stage; a bar's height is asserted, including a 0 reading keeping its floor; `aria-expanded` flips; steps appear only when open; a step with no action renders its description alone; sparkline has the labelled `img` role with the right count; **no sparkline element at all for an empty history**; eight bars for a twelve-reading history; the last bar is the opaque one; the whole row is ≥44 px; `onToggle` fires with the routine's name.

- [ ] **Step 2: Write `MemoryBench.test.tsx`** (~18): the four sub-tabs render and switch via `memory.setTab`; **each tab's note is present and quoted exactly**; hot and cold rows render their meta; typing does not call `submit`; submitting the form does; the three empty states each render their exact sentence; the 503 message renders *and the rows are still in the document*; a semantic file clamps and expands; the scratchpad shows `0 in the queue`; routines render one `RoutineRow` each; an error banner renders `memory.error`. Build `Memory` objects with a local `state(overrides)` factory rather than mocking the hook.

- [ ] **Step 3: Run both, watch them fail. Step 4: Implement. Step 5: Green, lint, build.**

At the end of this task: **58 test files, ~901 tests.** `MemoryBench` is not mounted yet — task 10 does that — so the app is unchanged on screen.

- [ ] **Step 6: Commit** `feat(web): the Memory bench`

---

## Task 4: `lib/triggers.ts` — kinds, meta, and three calls

The record is `BaseTrigger.model_dump_json()` straight out of a Redis hash (`core/triggers/models.py`), so the fields are exactly: `trigger_id`, `trigger_type` (`"time"` / `"sensor"` / `"composite"`), `name`, `enabled`, `one_shot`, `created_by`, `created_at`, `last_fired`, `action` (`{tool_name, target_service, parameters}` or null), `urgency`, `conditions`. The conditions differ by type:

| type | conditions |
|---|---|
| time | `{cron: string \| null, run_at: string \| null}` |
| sensor | `{entity_id, state_match, attribute_match}` |
| composite | `{children: object[], require: number}` |

**Files:** Create `web/src/lib/triggers.ts`, `web/src/lib/triggers.test.ts`. Modify `web/src/test/fixtures.ts` (add `trigger(overrides)`).

- [ ] **Step 1: Write the failing tests** (~16)

```ts
describe("triggerKind", () => {
  it("calls a time trigger with a cron a schedule", () => {
    expect(triggerKind(trigger({ trigger_type: "time",
      conditions: { cron: "0 19 * * 4", run_at: null } }))).toBe("schedule");
  });
  it("calls a time trigger with a run_at a time", () => { /* -> "time" */ });
  it("calls a time trigger with neither a time, not a schedule", () => { /* -> "time" */ });
  it("passes sensor and composite through", () => { /* … */ });
  it("falls back to time for a type it has never heard of", () => {
    expect(triggerKind(trigger({ trigger_type: "weather" as TriggerType }))).toBe("time");
  });
});

describe("triggerMeta", () => {
  const now = Date.UTC(2026, 8, 16, 21, 30, 0);
  it("prints a cron verbatim, because the next fire time is in another process", () => {
    expect(triggerMeta(trigger({ trigger_type: "time", conditions: { cron: "0 19 * * 4" },
      created_by: "conversation", created_at: "2026-09-16T20:52:00Z" }), now))
      .toBe("recurring · cron 0 19 * * 4 · created from conversation 20:52");
  });
  it("formats a run_at, which it can read honestly", () => {
    expect(triggerMeta(trigger({ trigger_type: "time", one_shot: true,
      conditions: { run_at: "2026-09-17T08:40:00Z" } }), now))
      .toBe("one-shot · runs 08:40 tomorrow · created from conversation 20:52");
  });
  it("names the entity a sensor trigger watches", () => {
    expect(triggerMeta(trigger({ trigger_type: "sensor",
      conditions: { entity_id: "binary_sensor.front_door", state_match: "on" } }), now))
      .toBe("recurring · binary_sensor.front_door is on · created from conversation 20:52");
  });
  it("drops the state clause when a sensor trigger matches any state", () => { /* "… on any change" */ });
  it("counts what a composite needs", () => {
    /* conditions: { children: [{}, {}, {}], require: 2 } -> "recurring · 2 of 3 conditions · …" */
  });
  it("gives created_at a day label, so a week-old trigger is not a bare clock", () => { /* dayLabel */ });
  it("says nothing it cannot read rather than guessing", () => {
    expect(triggerMeta(trigger({ trigger_type: "time", conditions: {} }), now))
      .toBe("recurring · no schedule stored · created from conversation 20:52");
  });
});
```

`runs 08:40 tomorrow` comes from `hhmm` plus `dayLabel(date, now)` — both already in `lib/format.ts`; import them relatively. A `run_at` in the past renders as its date, not as `tomorrow`; add that test.

- [ ] **Step 2: Run, watch fail. Step 3: Implement.**

`triggerMeta` assembles `[one_shot ? "one-shot" : "recurring", <what it does>, "created from <created_by> <stamp>"]` with ` · ` — the handoff's three clauses (§7: `one-shot · fires 08:40 tomorrow · created from conversation 20:52`). The middle clause is a `switch` on `triggerKind`. **`last_fired` is deliberately not in the meta line** — the handoff does not put it there and the row is already three clauses long; it belongs in the expanded detail (task 6). `fetchTriggers` unwraps `{triggers}`; `setTriggerEnabled` is `post(\`/api/admin/triggers/${encodeURIComponent(id)}/enabled\`, { enabled })` returning `void`; `fireTrigger` likewise on `/fire`. **`encodeURIComponent` on the id is not optional** — ids come from the LLM's trigger-creation tool.

- [ ] **Step 4: Green, lint, build. Step 5: Commit** `feat(web): trigger kinds and meta lines`

---

## Task 5: `useTriggers` — the pending map and the 60 s re-read

The interesting part. `POST …/enabled` returns `{"status":"queued","effective_within_seconds":60}`: the triggers process applies it inside its own 60 s cache window. So the client knows the request landed and does **not** know the trigger's state. Decision 6 governs — the row does not move.

**Files:** Create `web/src/workshop/useTriggers.ts`, `web/src/workshop/useTriggers.test.tsx`.

- [ ] **Step 1: Write the failing tests** (~15). Use `vi.useFakeTimers()`.

1. `reads the list once when enabled` / `reads nothing when disabled`.
2. `shows every trigger under the all chip` and `filters to one kind`.
3. `keeps the chip across a disable and re-enable`.
4. `marks a toggled trigger enabling without changing its enabled flag` — after `toggle(t)` on a disabled trigger, `pending["t-1"].kind === "enabling"` and `triggers[0].enabled` is still `false`. **The assertion that encodes decision 6.**
5. `re-reads the list 60 s after a toggle, and clears the pending note` — advance `REREAD_MS`, assert a second fetch, assert `pending` empty.
6. `does not re-read early` — advance 59 s, one fetch.
7. `a second toggle restarts the window rather than stacking timers` — toggle, advance 30 s, toggle again, advance 31 s: no re-read yet; advance to 60 s from the second: one re-read.
8. `keeps the pending note when the re-read comes back unchanged` — **no**: state the opposite. The note clears on the re-read whatever it says, because §5.2 forbids a client claim outliving the evidence for it; the row then tells the truth the server last told us. Assert the clearing.
9. `records a failed toggle on the row that failed, not on the bench` — a 500 leaves `pending["t-1"].error` set and `error` (the bench-wide one) null.
10. `fire marks the row firing and clears it on the same 60 s window` + `fired` keeps the timestamp so the row can say `fired 21:15`.
11. `two rows can be pending at once, independently`.
12. `expands one trigger at a time`.
13. `clears every timer on unmount` — **and the naive version of this test does not bite**: after unmount the query has no observers, and `invalidateQueries` refetches only *active* queries, so leaked timers fire invisibly. Mount, toggle three rows, unmount, then **mount again on the same `QueryClient`** (the Workshop closing and reopening) and advance: a leak shows up as stray reads. Leaking a 60 s timer per toggle is how a long-lived PWA ends up storming the API.
14. `polls on nothing` — 5 minutes of fake time, still one fetch.

- [ ] **Step 2: Run, watch fail. Step 3: Implement.**

Timers in a `useRef<Map<string, ReturnType<typeof setTimeout>>>`, cleared in a `useEffect` cleanup. On each mutation: set `pending[id]`, `clearTimeout` any timer for that id, `setTimeout(() => { delete pending[id]; queryClient.invalidateQueries({ queryKey: ["triggers"] }); }, REREAD_MS)`.

- [ ] **Step 4: Green, lint, build. Step 5: Commit** `feat(web): the Triggers bench's hook`

---

## Task 6: `TriggerRow` and `TriggersBench`

**Files:** Create `web/src/workshop/TriggerRow.tsx`, `TriggerRow.test.tsx`, `TriggersBench.tsx`, `TriggersBench.test.tsx`.

### `TriggerRow({ trigger, now, pending, firedAt, open, onToggleOpen, onToggle, onFire })`

Handoff §7. Rows carry a 1 px `--line` top border and `padding 11 0`; 16 px side padding comes from the bench.

- Left column: the kind in **mono 10 px `--accent-text`** — `time` / `schedule` / `sensor` / `composite`; the name at 14.5 px; then `triggerMeta(trigger, now)` in `.t-meta-strong`. **`now` is a prop, never `Date.now()` in render** — `eslint-plugin-react-hooks` v7's purity rule forbids it, and a per-row clock lets two rows disagree about which day `tomorrow` is. The bench holds one `useState(() => Date.now())`, as `MemoryBench` does.
- **A one-shot that has already fired recedes by swapping tokens, not by opacity** (`one_shot && last_fired`). The handoff says `opacity .55`, but composited that takes the meta line to 2.71:1 and the name to 3.63:1 in light — under AA — and a whole-row opacity is invisible to `contrast.ts`, which is the file that exists to stop exactly this. `RoutineRow` and `StreamChips` both already recede by losing hue rather than alpha. It is done; it should not read as live, and it should still be readable.
- Right: **a 52×32 switch that does not move on tap.** A `<button role="switch" aria-checked={trigger.enabled}>`; track `--accent` on and `--line` off, a 26 px knob in `--bg` travelling left 3 → 23 over 200 ms. While `pending` is set it gains `aria-busy="true"`, the knob keeps its old position, and an **accent mono** note appears under the meta line, verbatim:

  `queued 21:15 · enabling · takes effect within 60 s`

  `disabling` for the other direction. The switch carries **`aria-disabled`, not `disabled`**, while pending — `ActivityBench`'s `↑ older` button documents why: a control that disables itself under the finger that just pressed it throws focus to `<body>` mid-action, and a keyboard or screen-reader user loses their place in the list. The click handler no-ops instead — a second tap inside the window can only confuse the reader, and the server would queue a second action against a state neither of us knows.
- A failed mutation replaces the note with `<status or message> · that did not land · the scheduler still has the old setting`. `Pending.status` is present only when a server answered, so a request that never arrived prints its error text rather than a number nobody sent.
- **The corrupt-record card** — the one form of deviation 6 that is reachable. It **self-dismisses 60 s after the tap**, because the hook's window clears every note whatever it says (task 5, behaviour 8). That is deliberate: a client-side claim must not outlive the evidence for it, and the next read either shows the trigger again or does not. When a mutation answers **500**, the row is replaced by a `--surface` card carrying the handoff's copy, with the server's own detail in place of its example byte offset:

  `This record can't be read.` / `500 · <the server's detail> · the scheduler skips it · fix in the store or delete`

  and **both the switch and Fire now `aria-disabled`**. The card replaces the **meta line** — which is built from the very conditions the server has just said it cannot parse — and keeps the kind, name and switch: a card that does not say *which* record is broken is not worth drawing, and the disabled controls the copy promises have to exist in order to be disabled. `GET /api/admin/triggers` drops unparseable records, so this can only appear after a toggle on a record that decayed since the read.
- Expanded (`aria-expanded` on the row button): `created_by`, `created_at` via `dayLabel`, `urgency`, and **`last fired <stamp>` / `never fired`** — which the meta line deliberately leaves out. Then the payload: the action as `rawCall(action.tool_name, action.parameters)` in a mono `<pre>`, or `no action · notification only`; then the conditions as a mono `<pre>` of `JSON.stringify(conditions, null, 2)`.
- **Fire now** is a 44 px **outlined** button (1 px `--line`, transparent fill), reading `Fire again` once `firedAt` is set. Tapping disables it for the window. The note under it is the handoff's, verbatim:

  `queued only; look for trigger.fired on the events stream to know it ran`

  and once queued, `queued 21:15 · look for trigger.fired on the events stream to know it ran`. Never `Fired`. We do not know that.
- **A queued fire gets no row-level note** — its note lives under its own button, inside the expanded detail. Do not invent a `firing · takes effect within 60 s` third sentence: the 60 s is the *enabled-cache* window and says nothing about a manual fire. A **refused** fire does surface at row level, because a refusal is news about the row whether or not it is open.

### `TriggersBench({ triggers }: { triggers: Triggers })`

Kind chips across the top: `All · Time · Schedule · Sensor · Composite`, each 32 px in a 44 px track. These are **filters, not tabs** — `aria-pressed` buttons in a `role="group"`, walked by Tab — so `workshop/tabs.ts` does **not** fit: `tabKeyDown` moves focus among `[role="tab"]` children and activates on arrow. Plain buttons, with a comment saying why the shared helper was declined. Their borders are `--muted`, not `--line`: five adjacent tap targets edged in `--line` sit at 1.18:1, the finding `StreamChips` already documents. A count beside `All` only.

Rows below in `flex-1 overflow-y-auto`. Empty: `No triggers yet.` for a genuinely empty list; `No <kind> triggers.` when a filter empties it. Error: the bench-wide `error` in a banner at the top, rows still shown beneath. **The banner is a permanently-mounted `role="status"` region** (`aria-label="Read errors"`, the shape `ActivityBench` already uses) — task 10b's header region carries connection status only, and decision 8 removes the *Feed status* banner's role, not the read-error one. A read failure nothing announces is a silent screen.

**The footer note — the handoff's sentence verbatim, then ours:**

> Switches are fire-and-forget: the server queues the change and the scheduler picks it up within 60 s. A row keeps its old state, with a note, until a fresh read confirms.
>
> Nothing here edits a trigger — ask Alfred to change or remove one.

`.t-meta-strong`, 16 px padding, `env(safe-area-inset-bottom)` under it. The second line is doing real work: there is no create/edit/delete route (deviation), and a reader who does not know that will hunt for a button that is not there.

- [ ] **Step 1: `TriggerRow.test.tsx`** (~16): renders kind, name and meta; the switch reports `aria-checked` from `enabled`; **tapping while pending is impossible and the knob has not moved** (assert `aria-checked` is still the old value and the button is `aria-disabled`, not `disabled`); the queued note is exact; the failure note carries the status; **a 500 renders the corrupt-record card with the server's detail and `aria-disabled`s both controls**; a fired one-shot is dimmed and a live one is not; expand reveals `last fired`, the action and the conditions; a trigger with no action says `no action · notification only`; Fire now calls `onFire` once, then reads `Fire again` with the queued note; the row is ≥44 px; `onToggle` is not called by tapping the row body (expanding must not toggle — the classic defect in this layout, and worth its own test).
- [ ] **Step 2: `TriggersBench.test.tsx`** (~10): chips render and filter; the `all` count; both empty states with their exact sentences; **the footer note is quoted exactly**; the error banner leaves the rows in place; each trigger gets a row.
- [ ] **Step 3: Run both, fail. Step 4: Implement. Step 5: Green, lint, build.**

At the end of this task: **62 test files, ~951 tests.**

- [ ] **Step 6: Commit** `feat(web): the Triggers bench`

---

## Task 7: `lib/system.ts` and `useSystem`

**Files:** Create `web/src/lib/system.ts`, `system.test.ts`, `web/src/workshop/useSystem.ts`, `useSystem.test.tsx`. Modify `web/src/shell/ConnectionProvider.tsx` (`REHYDRATE_KEYS` gains **both** `"system"` and `"triggers"` — task 5 deliberately left the latter to this task rather than widening its own commit) and `ConnectionProvider.test.tsx` (its exact-list assertion).

The System bench reads from **six** places, three of them outside `/api/admin`:

| what | call | gate |
|---|---|---|
| health, spend, DND, counts, reflex, librarian | `useOverview()` — **reuse it, do not add a second query** | session |
| sessions | `GET /api/auth/sessions`, `DELETE /api/auth/sessions/{id}` | session |
| passkeys | `GET /api/auth/credentials` | session |
| pairing code | `POST /api/auth/pairing` | none (its own five-minute budget) |
| services | `GET /api/integrations`, `GET /api/integrations/{name}/status` | session |
| service credentials | `PUT /api/integrations/{name}/credentials` | **session + trusted network** |
| reflex attention | `GET /api/admin/attention`, `PUT /api/admin/attention/{domain}` | session |

That last gate is deviation 12: off the home network the whole bench reads fine and one button 403s.

- [ ] **Step 1: Write `system.test.ts`** (~12) for the formatters only — the fetches are one-liners the hook's tests cover.

```ts
describe("sessionMeta", () => {
  it("names the channel, the sign-in time and the address", () => {
    expect(sessionMeta(session({ channel: "web", created_at: "2026-09-16T07:02:00Z",
      ip: "192.168.1.24" }))).toBe("passkey · web · signed in 07:02 · 192.168.1.24");
  });
  it("leaves out an address the server did not send", () => { /* no trailing " · " */ });
  it("says this device for the current session", () => { /* "passkey · web · this device · …" */ });
});

describe("credentialMeta", () => {
  it("names the transports and the last use", () => {
    expect(credentialMeta(credential({ transports: ["internal", "hybrid"],
      last_used_at: "2026-09-16T07:02:00Z" }))).toBe("internal, hybrid · last used 07:02");
  });
  it("says never used rather than an empty stamp", () => { /* … */ });
  it("says no transports recorded for an empty list", () => { /* … */ });
});

describe("spendNote", () => {
  it("states the spend against the cap and the request count", () => {
    expect(spendNote({ date: "2026-09-16", spend_usd: 0.42, cap_usd: 5, request_count: 118,
      avg_usd: 0.0036 })).toBe("$0.42 of $5.00 today · 118 requests · $0.0036 each");
  });
  it("drops the clauses the server did not send", () => { /* "$0.42 of $5.00 today" */ });
  it("says no spend recorded today for a null cost", () => { /* … */ });
  it("does not divide by a zero cap", () => { /* bar width 0, no NaN in the text */ });
});

describe("serviceNote", () => {
  it("maps each state to its sentence", () => {
    expect(serviceNote("ok")).toBe("reachable");
    expect(serviceNote("failed")).toBe("not answering");
    expect(serviceNote("unset")).toBe("no credentials saved");
    expect(serviceNote("testing")).toBe("testing…");
    expect(serviceNote("queued")).toBe("saved · testing");
  });
});
```

`usd` is already in `lib/format.ts` — use it, and check what it does with `0.0036` before asserting (if it rounds to cents, the per-request clause needs its own formatting; write the test against the real behaviour, not against the string above).

- [ ] **Step 2: Implement `lib/system.ts`.** Types from the contract block; fetches unwrap `{sessions}`, `{credentials}`, `{domains}`; `/api/integrations` returns a **bare array**, not an envelope — the one endpoint on this bench that does. `endAuthSession` is `api(path, { method: "DELETE" })`.

- [ ] **Step 3: Write `useSystem.test.tsx`** (~18).

1. `reads nothing while the bench is closed`.
2. `reads sessions, credentials, integrations and attention when it opens`.
3. `probes each integration's status once` — one `/status` call per integration, no more.
4. `an integration whose status 503s is failed, not missing` — it stays in the list.
5. `setting do-not-disturb moves the switch once the server confirms` — this write *is* applied directly (`POST /api/admin/dnd` writes Redis), so unlike a trigger it moves; assert the overview is invalidated afterwards.
6. `clearing do-not-disturb sends active false and no until`.
7. `a DND write that fails leaves the switch where it was and reports the error`.
8. `ending a session drops it from the list after the re-read`.
9. `ending your own session does not crash the bench` — the server clears the cookie and the next read 401s; assert the hook does not throw and the auth gate's event fires (it is `api`'s job, so assert `authEvents` was emitted).
10. `saving credentials tests them afterwards` — `PUT` then `GET …/status`, in that order.
11. `a 403 on save is reported as a network gate, not as a bad password` — `credentials.error` holds the 403 detail and a `gated: true` flag the section can branch on.
12. `drain and consolidation are queued, and stay queued` — `maintenance.drainedAt` / `ranAt` set, no optimistic change to the counts.
13. `attention allow and ask each PUT the one domain they touch`.
14. `attention returns the updated domain and the list reflects it without a full re-read`.
15. `the hook does not poll` — the overview's own 30 s interval is `useOverview`'s and is not doubled here (`staleTime` already handles it; assert no *extra* timer).

- [ ] **Step 4: Implement `useSystem(enabled)`.** Compose: `useOverview()` plus five `useQuery`s (`enabled`) and their mutations, grouped into the sub-objects the contract names (`health`, `quiet`, `sessions`, `credentials`, `integrations`, `attention`, `pairing`, `maintenance`) so `SystemBench` destructures one object per section and the sections stay independently testable.

Derive `health` here, not in the view:

```ts
const health = {
  bus:   { value: redis.connected ? "alive" : "unknown", note: `bus · redis · ${streamCount} streams` },
  reflex:{ value: reflex?.last_ms != null ? `${Math.round(reflex.last_ms)} ms` : "—",
           note: reflex?.model ? `reflex · ${reflex.model}` : "reflex · no model reported" },
  rate:  { value: rateText(overview), note: "event rate · 5-minute mean" },
  home:  { value: homeStatus, note: homeLatency },
};
```

`homeStatus` comes from the `home-service` (or `home_assistant`, whichever `GET /api/integrations` actually names — **check, do not assume**) entry's `/status`. No GPU figure and no service count: deviation 8.

- [ ] **Step 5: `REHYDRATE_KEYS` gains `"system"`. Step 6: Green, lint, build. Step 7: Commit** `feat(web): the System bench's reads and writes`

---

## Task 8: `SystemBench` — the frame, Health, Quiet, Maintenance

**Files:** Create `web/src/workshop/SystemBench.tsx`, `SystemBench.test.tsx`, `web/src/workshop/SystemFrame.tsx`, `web/src/workshop/Switch.tsx`. Modify `web/src/workshop/TriggerRow.tsx` (adopt the shared switch), `web/src/lib/system.ts` (`spendHeadline`, the `5-min mean` label), `web/src/workshop/useSystem.ts` (`online` from `dataUpdatedAt`; key `maintenance.error` per control) and their tests.

~~**First, widen the hook.**~~ Task 7's fix round already shipped `useSystem(enabled, onHeld)` with `onHeld` hung on `quiet` (`c4e8bb3`). Nothing to do here; `useSystem.ts` is not a task 8 file after all.

`SystemBench({ system }: { system: System })`. A scrolling column of sections. **The section frame** (used here and by task 9, so export it from this file):

```tsx
// web/src/workshop/SystemFrame.tsx
export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) …
```

`aside` carries Health's live stamp. Task 9's four sections use the two-prop form.

The frame lives in `web/src/workshop/SystemFrame.tsx`, not in the bench — task 9 imports it, and the bench was already the largest view in `src/workshop/` before task 9's four sections. The switch lives in `web/src/workshop/Switch.tsx`, shared with `TriggerRow`: the two were forked byte-for-byte, and the copied contrast rationale then asserted ratios measured against `--bg` for a control that sits on `--surface`.

— a caps `.t-meta-strong` label with `0.08em` letter-spacing in `--fg2` and 8 px below it; then a `rounded-xl` container with a 1 px `--line` border and `--surface` background; rows inside it 56 px tall, divided by 1 px `--line`, 12 px side padding.

Handoff §8. The plan first said "16 px above the label" *and* "22 px between sections", which is 38 px between a card and the next label; it is 16 px above the **first** label and 22 px between sections, carried on the label's own top margin so task 9's sections inherit it.

**Health** — a stamp on the right of the section label: `live · 21:14:07` in `--muted` while online, `unknown since 21:14` in `--accent-text` while not. Then a 2×2 grid of stat cards (`padding 12 14`), each an 8 px dot driven by the cell's own `alive` flag — **never by string-matching its `value`** — distinguishing its two states by **fill vs outline**, `MemoryBench.tsx:114-122`'s idiom, not by hue: `--green` against `--muted` is 1.22:1 dark and 1.51:1 light, two circles of near-identical luminance, a 20 px/500 value, and a mono label:

| value | label |
|---|---|
| `alive` | `bus · redis · <n> streams` |
| `380 ms` | `reflex · <model>` |
| `2.1 ev/s` | `event rate · 5-min mean` |
| `ok` | `home assistant · 210 ms` |

Deviation 8 removes the handoff's `6 services` and `gpu 41%` — neither has a source. **Offline the grid recedes by swapping to `--fg2` *and blanks itself*** — `?`, `?`, `?`, `—` over `bus · unknown since 21:14`, `reflex · unknown`, `event rate · unknown`, `home assistant · unknown`, which is not invented: `docs/design/2026-09-04-pwa-client-handoff/Alfred.dc.html:817` builds that grid literally. A dimmed `210 ms` is still a latency claim about a service that is down. (`healthGrid`'s `not read yet` notes are for the never-read case; this is the went-stale case.) The dots go out with it — the same line sets `dot: t.muted` offline. **And "offline" is `overviewQuery.dataUpdatedAt` going stale, never `error !== null`:** TanStack Query 5 defaults to `networkMode: "online"`, so with no network the poll is *paused*, not failed — `data` is retained and `error` stays `null` for ever while the wall-clock stamp keeps ticking `live · …` over four frozen cells — the handoff says opacity .55; see the Triggers task for why this phase dims by token instead. A stale number presented at full strength is the §5.2 failure.

Under the grid, the spend card: title `Cloud spend today`, then mono `$1.42 of $5.00`, a 4 px bar filling `spendFraction(cost)` (`--accent` on `--line`; the clamp and the zero-cap guard live in `lib/system.ts`, not here), and the note. Task 7's `spendNote` led with the money the mono headline already shows, so the card was first built as one line; that was overruled. **`lib/system.ts` gains `spendHeadline(cost)`** (`$1.42 of $5.00` / `$1.42 · no cap set` / `no spend recorded today`) and `spendNote` loses its money clause and gains the handoff's `at the cap, the conscious mind declines and says so` — **copy, not data**: it needs no server field, it is verifiably true (`core/conscious/engine.py:672-686` returns exactly that System-1 fallback when `is_budget_exceeded()`), and it is the only place in the client that says what the cap *does*. `resets 00:00` stays dropped: `core/conscious/cost.py:70` rolls the day on `datetime.now(UTC)`, so the window resets at UTC midnight and `00:00` is false for any household outside it. The bar's `aria-labelledby` names both ids. `role="img"` on the bar with the note as its label; a zero or missing cap draws an empty bar and the text says why. Never `NaN`.

**Quiet** —
- Row 1 (56 px min): `Do-not-disturb`, a `role="switch"` reporting `dnd.active`. This one **moves on tap**, once the server has confirmed, because `POST /api/admin/dnd` is a direct write (decision 6's exception) — and says `Applied`. In flight it is `aria-busy` (`quiet.setting`) and `aria-disabled`, not `disabled`, for the reason the Triggers task gives. Task 7 deliberately does **not** patch the cache optimistically: the switch moves when the overview re-read lands. The visible gap is the point — an un-retired optimistic claim resurrects the old position the next time a calendar meeting moves the switch on its own.
- The sub-line, verbatim per state: `off · urgent still speaks regardless` / `on · until 08:30 · queue drains then` / `on · no expiry · queue will not drain on its own`.
- Row 2, only while active: expiry chips `1 h · until noon · until 22:00 · no expiry`, 44 px, radius 10, each posting `active: true` with the computed `until` as an ISO string (`no expiry` sends `null`). The current one is `aria-pressed`.
- Row 3: `Held back` with `2 held ›` on the right — **`2 · growing` in `--accent-text` when there is no expiry**, because a queue with no drain is a different fact from a queue with one. Opens the Held-back sheet through `system.quiet.onHeld`. Count from `overview.counts.deferred`.
- Row 4: `Send them now` — `drainDeferred()`. After it: `queued 21:15 · the notifier sends them when it next reads the queue`. Never `Sent`.
- **The footnote, verbatim:** `A meeting in your calendar can also quiet Alfred; that is not shown here.` — `.t-meta-strong`. Deviation 11; it is the difference between a screen that is wrong and a screen that is honest about its blind spot.

**Maintenance** —
- `Nightly consolidation` / `last 03:00 · 42 reviewed` from `librarian.{last_run_at, reviewed}` via `dayLabel` + `hhmm`; `never run` when null. `next <stamp>` beside it when `next_run_at` is set.
- `Run consolidation now`, a 44 px **outlined** button reading `Run again` once used, with the handoff's note: `queued only; the run reports on the events stream, not here`, becoming `queued 21:15 · progress shows on the events stream as consolidation.*`.
- `Session idle timeout` row: `session.idle_minutes` minutes, from the overview — and `idleMinutes` is `null`, never `0`, until it lands, so print nothing rather than `0 minutes`. No restart, no shutdown, no log download — none of it has a route (log the gap in the backlog).

- [ ] **Step 1: Write `SystemBench.test.tsx`** (~22) against a `state(overrides)` factory: the four health cells render their values and labels; `alive` is green and `unknown` is not red; **offline renders `?`/`—` and dims the grid**; the live/`unknown since` stamp; the spend bar clamps at a full cap and does not produce `NaN` with a zero cap; the spend note drops the clauses the server did not send; the DND switch reports, flips and says `Applied`; expiry chips only appear while active and post the right `until`; **the calendar footnote is quoted exactly**; the three DND state sentences verbatim; `2 · growing` only with no expiry; held-back opens via the callback; drain and consolidation each render their exact queued note and never say "sent"/"done"; `Run again` after a run; `never run` renders; every button is ≥44 px.
- [ ] **Step 2: Fail. Step 3: Implement. Step 4: Green, lint, build. Step 5: Commit** `feat(web): System bench health, quiet hours and maintenance`

---

## Task 9: `SystemSections` — Sessions, Connected services, Devices & identity, Reflex

**Files:** Create `web/src/workshop/SystemSections.tsx`, `SystemSections.test.tsx`. Modify `SystemBench.tsx` to render them, and `web/src/lib/system.ts` + `system.test.ts` for the one note below that task 7 left open.

Four exported components, each taking its own slice of `System`, each with its own tests. They live in one file because they share the `Section` frame and the same row idiom, and four one-component files would be four copies of the same imports.

### `SessionsSection({ sessions })`
Rows 56 px: `device_name`, then `sessionMeta(s, now)` in mono `.t-meta-strong` — the handoff's `passkey · pwa · signed in 07:02 · 192.168.1.24`. On the right, **`current`** for your own (a disabled label, not a button — you do not "end" the session you are using from a list) and **`End`** in `--accent-text` otherwise, ≥44 px. After ending: the row recedes to `--fg2` (not opacity .5 — see the Triggers task) and reads `ended 21:15 · applied`, then disappears on the re-read. Empty: `No other sessions.`

### `ServicesSection({ integrations })`
Rows: the name, `category · kind` in `.t-meta`, then on the right the state word and an 8 px dot — `ok` in `--green-text` with a `--green` dot, `failed` in `--accent-text`, `unset` in `--muted`. Tapping a row expands a credential form built from `schema.fields`: one labelled input per field, 48 px tall, radius 10, **mono 14 px** (≥16 px if that fights iOS focus zoom — the zoom rule wins over the handoff's 14), `type="password"` for anything the schema marks secret (read the field shape in `core/channels/service_credentials.py` — do not guess the flag's name), placeholder `configured[field] ? "saved" : ""` and **never the value itself**, which the server does not send and must not.

**Save & test** is a filled 44 px button reading `Testing…` while it works → `PUT` then re-`GET …/status`. The note under the row is `serviceNote(state, status)` from `lib/system.ts` — the handoff's four, verbatim, plus `queued`. **This section prints it and never a word of its own:**

| state | note |
|---|---|
| `ok` | `stored encrypted at rest · last check ok` |
| `failed` | `<status> from the service on the last check · stored value kept until you replace it` |
| `unset` | `nothing stored · Alfred answers without this source` |
| `testing` | `round-trip in progress · up to 10 s` |
| `queued` | `saved · testing` |

The handoff writes `401` in the `failed` note because that is the common case; pass the status the server actually reported, and fall back to `401` only when there is none. **A 404 is the exception and needs its own branch** — task 7 shipped it reading `404 from the service on the last check`, which is false: a 404 there comes from Alfred's own route, not from the service. Say `404 · Alfred does not know this name · the service may have unregistered since the list was read` instead. This is a new sentence, not a new state word; the closed vocabulary is unaffected. (Task 7 first gave `serviceNote` five short glosses on the state *word* — `reachable`, `not answering` — which nothing consumes, since the word on the right of the row is the bare `ServiceState`. Overruled, so the export is not dead.)

On 403 from the `PUT` — read it off `integrations.saves[name].gated`, which task 7 sets rather than making the view parse a status: `Credentials can only be changed from the home network.` in `--fg2`, with the form left filled so nothing is lost. Note that `api` already emits `denied` for every 403, so the Denied gate will rise over this — confirm what that looks like and, if the gate explains it better than the inline sentence does, say so in your report rather than fighting it.

### `IdentitySection({ credentials, pairing })`
Rows per passkey, the handoff's shape: `iPhone 15 Pro · passkey · registered 12 Aug · Face ID · this device` — i.e. `device_name`, then `credentialMeta(c, now)`, which carries the **whole** line — `passkey · registered 12 Aug · internal, hybrid · last used 07:02 · this device`, dropping any clause the server did not send. Task 7 first shipped only the transports and the last use, leaving this section to compose three clauses around it; that was overruled, because `sessionMeta` carries `passkey` and `this device` itself and two rows on one screen built by two different rules is the split the formatters exist to prevent. **`Add a passkey on another device`** in `--accent-text`, with `<n> registered` beside it; it mints a pairing code and shows it at 32 px, mono, letter-spaced, with `Pairing window closes 21:20 · enter this on the new device`. The code is shown until the bench is left; there is no way to re-show it and the note says so. Then **`Sign out on this device`** (`POST /api/auth/logout`).

No delete button — `DELETE /api/auth/credentials/{id}` exists and refuses the last one with a 409, but removing the passkey you are holding is a foot-gun with no confirmation design in the handoff. Log it in the backlog and leave it out. *(If review disagrees, it is a small addition — but it ships with a typed confirmation or not at all.)*

### `ReflexSection({ attention })`
Deviation 13 — no fidelity-locked design, so it follows the section frame. One sub-heading per domain, `members` as chips with an `×` that calls `ask(domain, entity)`, and `seen` entities not in `members` as hollow chips with a `+` that calls `allow(domain, entity)`. Above them: `Alfred acts on these without asking. Everything else it asks about first.` Empty: `Nothing is on the attention set yet.` A 503 from the store renders its detail — the endpoint deliberately distinguishes "nothing configured" from "the store is down", and this section must not flatten the two.

- [ ] **Step 1: Write `SystemSections.test.tsx`** (~32, roughly 8 per section) covering: each row's content; `current` is a disabled label and `End` a button; `ended 21:15 · applied` and the dimming; the empty states; the credential form building from the schema, masking secrets, never pre-filling a value, and showing `saved`; **each of the four per-state notes verbatim**; Save & test reading `Testing…` and calling PUT then status in that order; the 403 sentence with the form intact; the pairing code, its closing time and the `<n> registered` count; `this device`; `Sign out on this device`; attention chips both ways with their callbacks; the attention 503 detail; the intro sentence quoted exactly.
- [ ] **Step 2: Fail. Step 3: Implement. Step 4: Wire the four into `SystemBench` between Quiet and Maintenance** (Health · Quiet · Sessions · Connected services · Devices & identity · Reflex · Maintenance) and extend `SystemBench.test.tsx` with one test asserting the seven section headings in order. **Step 5: Green, lint, build.**

At the end of this task: **66 test files.** The plan's running test totals were written against an early count and are all stale — task 7 already ends at 64 files / 1115 tests. Take the count from the previous task's commit, not from this document.

- [ ] **Step 6: Commit** `feat(web): System bench sessions, services, identity and reflex`

---

## Task 10: Mount the three benches, move the live region, thread the sheet

The task that makes any of the previous nine visible. Three separate concerns; do them in order and commit once.

**Files:** Modify `web/src/workshop/Workshop.tsx`, `Workshop.test.tsx`, `web/src/workshop/ActivityBench.tsx`, `ActivityBench.test.tsx`, `web/src/room/Room.tsx`.

### 10a — the benches

Delete `UNBUILT` and the comment above it. Call the three hooks in `WorkshopPanel` beside `useActivity`, each gated on its own bench:

```tsx
const memory = useMemory(bench === "memory");
const triggers = useTriggers(bench === "triggers");
const system = useSystem(bench === "system", onHeld);   // the second parameter is added by task 8
```

Replace the ternary with a `switch (bench)` returning one of the four benches — not a chain of ternaries, and not a lookup object built in the render body (that would be a fresh object every render, which is exactly what `memo` on `Workshop` is there to prevent downstream).

### 10b — the live region

The header's status span becomes the live region (decision 8, and the standing instruction in `Workshop.tsx`'s own comment):

```tsx
<span className="t-meta-strong" role="status" aria-live="polite" data-testid="workshop-status">
```

Rewrite the comment above it: it currently argues the opposite, and a comment that contradicts the code is worse than no comment. Say what changed and why — on three of four benches nothing else announces a drop.

Then **remove `role="status"` from the feed banner in `ActivityBench.tsx`**, leaving its text. Two polite regions saying the same sentence is a double announcement, and the header's is the one that survives a tab change. Update `ActivityBench.test.tsx` accordingly — if a test asserts `getByRole("status")` there, it should now assert the text is present and that the element is *not* a status region.

The rate changes every 30 s and is now inside a live region. **That is the one risk of this change:** a reader would hear `live · 2.1 ev/s` every poll. Fix it at the source — only announce the *state*, not the number: give the span `aria-live="polite"` and put the rate in a nested `<span aria-hidden="true">`, or keep the region's text to `live` / `paused · N new` / `last true HH:MM · not live` and render the rate as a sibling outside it. **Pick one and test it**: a test that the region's accessible text does not contain `ev/s` while the visible header does.

### 10c — the Held-back sheet

`WorkshopProps` gains `onHeld: () => void`. In `Room.tsx`:

```tsx
const openHeld = useCallback(() => setSheetOpen(true), []);
…
<Workshop open={workshopOpen} onClose={closeWorkshop} onWhy={setWhy} onHeld={openHeld} />
```

`useCallback` is mandatory — an inline arrow re-renders the memoised Workshop on every chat frame, undoing the optimisation `Workshop.tsx`'s comment spends a paragraph explaining. The sheet is already rendered in the Room and already opens from `DndRow`; this adds a second opener, not a second sheet.

- [ ] **Step 1: Write the failing tests in `Workshop.test.tsx`** (~10): each of the four benches renders when its tab is chosen; **bench state survives a trip to another tab and back** (the test that pins the hooks-in-the-panel convention — switch to Memory, change the sub-tab, go to Activity, come back, assert the sub-tab); the header status has `role="status"`; its accessible text excludes the rate; `onHeld` reaches the Room from System (assert through the Workshop's props, not through the Room).
- [ ] **Step 2: Run; expect the placeholder text to still be found.**
- [ ] **Step 3: Implement 10a, 10b, 10c.**
- [ ] **Step 4: Run the whole suite** — `npx vitest run`. Anything red here is a phase-2 test asserting `not built yet · phase 3`; fix the test, not the code.
- [ ] **Step 5: `npm run lint && npm run build`. Step 6: Commit** `feat(web): mount the Memory, Triggers and System benches`

---

## Task 11: Docs, backlog, QA checklist

**Files:** Modify `web/README.md`, `docs/web-frontend.md`; create `docs/backlog/low/pwa-phase3-followups.md`, `docs/superpowers/qa/2026-09-16-pwa-phase3-ios-checklist.md`; modify `docs/backlog/low/pwa-phase2-followups.md`.

- [ ] **Step 1: `docs/web-frontend.md`** — a "The Workshop's four benches" section: the bench-is-a-pure-view convention, hooks-in-the-panel and why (state survives a tab change), the `enabled` gate, the three-shapes episodic adapter (a short version of task 1's table), and decision 6's queued-vs-applied rule as the house style for every fire-and-forget control.
- [ ] **Step 2: `web/README.md`** — the new files in the structure list, and one line under System: *Web Push (the Reach card) lands with phase 5; the card is deliberately absent rather than inert.*
- [ ] **Step 3: `docs/backlog/low/pwa-phase3-followups.md`** — one entry per deviation from the table above that a backend change would close, each stating the endpoint that would close it:
  1. episodic search metadata (best rejected score, threshold, corpus sizes) — `recall()` would have to return them
  2. an embedder readiness probe, so the model pill can be honest before the first search
  3. an `id` on browse-hot rows — the `CONTEXT_PREFIX` key, currently discarded
  4. one row shape for episodic browse and search
  5. `next_fire_time` over HTTP for cron triggers
  6. corrupt triggers surfaced in the list instead of dropped
  7. trigger create / edit / delete
  8. `GET /api/admin/dnd` reporting the effective state, calendar DND included
  9. a service / GPU / satellite inventory for Health
  10. ops actions (restart a service, download logs)
  11. passkey removal with a confirmation design
  12. session-gating the credential `PUT`, or a clearer story for editing from outside
  13. a shared `RawDump` for the `key: value` pill markup, which now exists once in `EventRow.tsx` and once in `TriggerRow.tsx`. Task 6 deliberately kept each as a per-file module constant rather than inventing a cross-file styling module inside a bench commit; if a third copy appears, extract it.
  14. recall stats on cold *browse* rows — the SELECT in `admin_api.py` omits `retrieval_count` and `last_retrieved`, so every cold browse row reads `never recalled`, and any cold row under the decay floor reads `decaying` whatever its real history. Search rows carry the stats; browse rows cannot.
- [ ] **Step 4: `docs/backlog/low/pwa-phase2-followups.md`** — close item §3 (the live region), noting task 10 moved it into the header.
- [ ] **Step 5: the QA checklist** — device steps in the phase-2 checklist's format, covering: all four benches reachable and each one's first paint; the episodic search keyboard (does the field zoom? it must not); a trigger toggle showing the queued note and the row not moving; a trigger toggle over a dropped connection; DND on and off from System and the Room's row agreeing; Held back opening from both places; the pairing code readable at arm's length; **the Triggers kind chips at 360 px — five `flex-1` chips give ~60.8 px each and "Composite" at 13 px medium is right at that width, so check it does not clip or wrap** (task 6 shipped `whitespace-nowrap px-1` rather than guess); Save & test from off-LAN showing the network sentence; VoiceOver hearing one announcement per socket drop, not two; every bench under a 60 s socket outage.
- [ ] **Step 6: Commit** `docs(web): phase 3 conventions, backlog and device checklist`

---

## Task 12: Branch green, self-review, PR

- [ ] **Step 1: The whole gate, from `web/`**

```bash
cd ~/code/.worktrees/alfred/pwa-phase3-benches/web
npm run lint && npx vitest run && npm run build
```

Expected: eslint silent, **~66 test files / ~1011 tests passing**, build clean. The counts are a guide — a task that needed more tests is fine, a task that has fewer than its section said is a skipped test.

- [ ] **Step 2: Run the suite three times.** A bench with timers and fake clocks is where flake lives, and a test that passes four times in five is a broken test.

```bash
for i in 1 2 3; do npx vitest run 2>&1 | tail -3; done
```

- [ ] **Step 3: Mutation-check the two claims that matter most.** Not the whole suite — two edits, each reverted immediately:
  1. In `TriggerRow`, make the switch report the *requested* state while pending instead of the stored one. A test must fail. If none does, decision 6 is undefended and the bench will eventually lie about a trigger.
  2. In `toEpisodicRow`, run `Date.parse` before the numeric branch. A test must fail.

- [ ] **Step 4: Self-review the diff.** `git diff origin/master --stat`, then read the diff. Look for: a colour that is not a token; `.t-meta` on a sentence a reader must trust; an invented status word; a `console.log`; a `TODO`; a control that claims an outcome the server did not confirm; any new token missing from `src/test/contrast.ts`.

- [ ] **Step 5: Secret hygiene over every commit on the branch** — the runbook's two greps (`~/code/alfred-deploy/PWA-EXPOSURE-RUNBOOK.md`, §"Secret hygiene"), run across `git rev-list origin/master..HEAD`, not just `HEAD`. A leak amended out of the tip still ships in history.

- [ ] **Step 6: Push and open the PR.** Body: what the three benches do, the deviations table (it is the most useful thing a reviewer can read first), the backlog file, and a note that the device checklist is unrun.

```bash
git push -u origin feat/pwa-phase3-benches
gh pr create --title "PWA phase 3: Memory, Triggers and System benches" --body "…"
```

**Do not merge.** Merging `master` deploys to the live box; the merge is the user's call.

---

## Self-review of this plan

**Spec coverage.** §5.1 Memory → tasks 1–3. §5.1 Triggers → 4–6. §5.1 Health, Operations, Identity → 7–9. §5.1 Notifications → task 8's Quiet section plus the existing Held-back sheet, now reachable from two places (task 10c). §5.2's six honesty rules → decisions 6 and 7, the queued-note copy in tasks 6 and 8, the "does not count as recall" note in task 3, the calendar footnote in task 8, and the deviations table wherever a fact is unavailable. §8's phase-3 gate (capability parity with the old SPA) → everything the old SPA could do that has an endpoint; the three things it could do that no endpoint supports (trigger editing, log download, service restart) are in the backlog with the route each would need. §10's `System › Reflex` → task 9.

**Known gap, stated rather than hidden:** Web Push (§6, the Reach card) is *not* in this phase. It is phase 5's, it has no backend, and shipping an inert card would repeat the defect phase 2's placeholder tabs were. Phase 3's gate is parity with the old SPA, which had no push either.

**Type consistency.** Every name a later task uses is fixed in the contract block above: `EpisodicRow.key`/`id`/`store`/`text`/`at`, `Memory.tab`/`submit`/`model`, `TriggerKind`, `Pending.kind`, `REREAD_MS`, `ServiceState`, `System`'s eight sub-objects, `WorkshopProps.onHeld`. Two signatures are deliberately marked for the implementer to settle against reality rather than guess: `episodicMeta`'s `now` (drop it if task 1's tests do not need it) and the integration name for Home Assistant (read it from the registry).
