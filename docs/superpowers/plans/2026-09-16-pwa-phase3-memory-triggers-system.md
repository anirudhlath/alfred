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

| # | Where | Handoff says | This plan ships | Why |
|---|---|---|---|---|
| 1 | Episodic, no results | `best score 0.31 · threshold 0.55 · 128 hot, 1 204 cold searched` | `nothing scored above the server's threshold` | `recall()` returns matches only. The score of the best *rejected* row, the threshold and the corpus sizes are not in the response and are not on any endpoint. Printing them would be invention. |
| 2 | Episodic, model pill | `model: ok` / `model: 503`, before searching | `model: unknown` until the first search answers, then `ok` or `503` | There is no readiness probe. The only way to learn the embedder is down is to search and be refused. |
| 3 | Episodic rows | one row shape | one row shape, built by an adapter | Browse-hot, browse-cold and search return three different shapes for the same row (`content` vs `summary`, comma-string vs JSON-string vs array, epoch-string vs REAL vs ISO). Task 1 normalises; nothing downstream sees the difference. |
| 4 | Episodic, hot rows | a row identity | key `hot:<index>` | Browse-hot rows are `HGETALL`'d with the Redis key discarded, so they carry no id at all. The list is replaced whole on every read, so an index key is correct here and nowhere else. |
| 5 | Triggers, meta | `one-shot · fires 08:40 tomorrow` | `one-shot · runs 08:40 tomorrow` from `run_at`; for a cron, `recurring · cron 0 19 * * 4` | `next_fire_time()` lives in the triggers process; the stored record has only `cron`/`run_at`. An `run_at` we can format honestly; a cron we can only print. |
| 6 | Triggers, corrupt record | `This record can't be read. / 500 · condition JSON fails to parse at byte 118` | the card renders only when a *mutation* answers 500, on the row that was toggled | `GET /api/admin/triggers` silently drops unparseable values, so a corrupt trigger is never in the list to draw a card for. The 500 is reachable only by toggling one that decayed since the read. |
| 7 | Triggers, kinds | `All · Time · Schedule · Sensor · Composite` | same five, with Time = `trigger_type "time"` carrying `run_at`, Schedule = `trigger_type "time"` carrying `cron` | `trigger_type` only ever holds `time`/`sensor`/`composite`. "Schedule" is a real distinction in the data, just not a stored one. |
| 8 | System › Health | `bus · redis 6 services, 8 streams`; `reflex · reflex-3b · gpu 41%` | `bus · redis · <n> streams`; `reflex · <model>` | There is no service inventory endpoint and no GPU telemetry anywhere in the repo. The stream count is `Object.keys(overview.streams).length`. |
| 9 | System › Reach | a push-notification card in four states | **omitted**, with one line in the README saying it lands in phase 5 | Web Push has no backend (spec §6 is phase 5). A card whose only control cannot work is the same defect the phase-2 placeholder tabs were. |
| 10 | System › Sessions | `GET /api/admin/sessions` | `GET /api/auth/sessions` | There are two session lists. The admin one is *conversation* sessions with `turns`/`ttl_seconds`; the design's row (`passkey · pwa · signed in 07:02 · 192.168.1.24` + End) is the auth one, and `DELETE /api/auth/sessions/{id}` is its End. |
| 11 | System › Quiet | three DND states | same three, plus the footnote `a meeting in your calendar can also quiet Alfred; that is not shown here` | `overview.dnd` is the raw manual key. `DNDChecker` also honours calendar DND and lazily expires a stale manual window, and neither is visible over HTTP — so "off" here can be wrong. §5.2's whole point is that we say so. |
| 12 | System › Connected services | one `Save & test` | one button, two calls (`PUT` then `GET …/status`), and a distinct 403 | The `PUT` is network-gated while every read on the bench is session-gated, so credential editing fails off-LAN while the rest of the screen works. That 403 gets its own sentence, not the generic one. |
| 13 | System › Reflex | not specified by the handoff | a section built from the System card idiom | Spec §10 names `System › Reflex` for the attention set and gives no fidelity-locked design. `GET/PUT /api/admin/attention` exist; the section is caps label + radius-12 container + 56 px rows, like its neighbours. |
| 14 | Routines | confidence sparkline "last 8 consolidations" | shipped | `RoutineSpec.confidence_history` landed in phase 0b. The §10 row proposing we drop it is superseded by the build list under it. |

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
- **A new token or colour pair must be restated in `src/test/contrast.ts`** or `token()` throws. This plan adds no tokens.
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
export function episodicMeta(row: EpisodicRow): string;   // no `now`: the stamp is hhmm only
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
export type Pending = { kind: "enabling" | "disabling" | "firing"; at: number; error?: string };
export interface Triggers { kind; setKind; triggers: Trigger[]; shown: Trigger[]; open: string | null;
  toggleOpen; pending: Record<string, Pending>; toggle; fire; fired: Record<string, number>;
  loading; error: string | null }
export function useTriggers(enabled: boolean): Triggers;

// lib/system.ts
export interface AuthSession { session_id; credential_id; device_name; channel; ip; user_agent; created_at; expires_in: number; current: boolean }
export interface Credential { credential_id; device_name; transports: string[]; created_at; last_used_at: string | null; current: boolean }
export interface Integration { name; category; kind: "adapter" | "service"; schema: { fields: Record<string, IntegrationField> }; configured: Record<string, boolean> }
export interface IntegrationStatus { name: string; healthy: boolean; latency_ms: number | null }
export interface AttentionDomain { domain: string; members: string[]; seen: string[] }
export type ServiceState = "ok" | "failed" | "unset" | "testing" | "queued";
export function sessionMeta(session: AuthSession): string;
export function credentialMeta(credential: Credential): string;
export function serviceNote(state: ServiceState): string;
export function spendNote(cost: Overview["cost"]): string;
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
export function useSystem(enabled: boolean): System;

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

- [ ] **Step 4: Add the keys to `REHYDRATE_KEYS`**

`web/src/shell/ConnectionProvider.tsx` line ~61. Add `"memory"` so returning to the foreground re-reads what is on screen. The list is prefix-matched — confirm that in the file before assuming; if it matches exact keys, add all four.

- [ ] **Step 5: Green + lint + build. Step 6: Commit** `feat(web): the Memory bench's hook`

---

## Task 3: `MemoryBench` and `RoutineRow`

**Files:**
- Create: `web/src/workshop/MemoryBench.tsx`, `web/src/workshop/MemoryBench.test.tsx`
- Create: `web/src/workshop/RoutineRow.tsx`, `web/src/workshop/RoutineRow.test.tsx`

`MemoryBench({ memory }: { memory: Memory })` — a pure view, no `QueryClient` in its tests.

**Layout (handoff §6).** A sub-tab row under the bench switcher: four segments, 32 px tall, `--field` on `--surface` for the selected one, `--fg2` labels for the rest, in the same idiom `BenchSwitcher` already uses — read that file and match it rather than inventing a second segmented control. Below it, `flex-1 overflow-y-auto` content. Padding `0 16px`; rows separated by a 1 px `--line` divider, never a border box.

**The note, verbatim, once, under the sub-tabs on Episodic:**

> Browsing here does not count as recall. Nothing you open is kept warmer or colder for it.

`.t-meta-strong`. It is the reason the endpoint passes `update_stats=False`, and it is the single most important sentence on the bench: a memory browser that silently reinforced what you looked at would corrupt the decay it is showing you.

**Episodic.** A search field (`type="search"`, `aria-label="Search memory"`, **≥16 px**, or iOS zooms on focus) in a `<form>` whose submit calls `memory.submit()`. Beside it the model pill: `model: unknown` / `model: ok` / `model: 503`, mono, `.t-meta-strong`, `--green` only for `ok`. Then rows:

- line 1 — `row.text`, `.t-body`, two lines max (`line-clamp-2`).
- line 2 — `episodicMeta(row)`, `.t-meta-strong`, preceded by a 6 px dot: **filled `--accent` for hot, a 1 px `--line` ring for cold**. Give the dot `aria-hidden` and let the meta line carry the word `hot`/`cold`, which it already does — the dot is redundancy for the eye, not information for a reader.
- line 3 — the entities, `.t-meta`, only when there are any. Decorative: the row above already says everything load-bearing.

Empty states, exactly:
- searched, nothing back → `Nothing scored above the server's threshold.` on line 1, `.t-body`; `searched by meaning · the server does not report what it rejected` on line 2, `.t-meta-strong`.
- browsing, nothing there → `No episodic memories yet.`
- `model: 503` → `The embedder is not answering, so meaning search is off. Browsing still works.` **and the previous rows stay on screen below it.**

**Semantic.** One card per file: the file name in mono `.t-body`, `dir · modified HH:MM` in `.t-meta-strong`, then the content in a `<pre>` with `whitespace-pre-wrap break-words`, clamped to 12 lines with a `Show all` / `Show less` toggle (a real `<button>`, ≥44 px). Empty: `No semantic memory files yet.`

**Scratchpad.** The content in the same `<pre>`, and above it `pending_queue N in the queue` (`.t-meta-strong`, `0 in the queue` when empty — never hide a zero here, an empty queue is a fact worth stating). Empty content: `The scratchpad is empty.`

**Routines** delegate to `RoutineRow`.

### `RoutineRow({ routine, open, onToggle })`

Collapsed: the name in `.t-body`; `routineTrend(routine).text` in `.t-meta-strong`, coloured `--green` when rising and `--fg2` when not (**not red when falling** — a routine losing confidence is the system working); and the lifecycle rail.

**The rail** is the design's one real invention and is worth building carefully: four dots joined by 1 px `--line` segments, in `ROUTINE_STAGES` order, the current stage filled `--accent` and the ones before it filled `--line`, the ones after hollow. `aria-hidden` on the whole rail, and a visually-hidden `<span>` carrying `stage: active` so a reader gets the same fact. 44 px tall including the row's own padding.

Expanded (the row is a `<button>` with `aria-expanded`): `routineDetail(routine)`, then the steps as an ordered list of `description` lines with `humaniseTool(action.tool_name)` beside each one that has an action, then the sparkline.

**The sparkline**: 8 bars from `confidence_history.slice(-8)`, 3 px wide, 2 px apart, max height 20 px, `--accent` at 0.35 opacity except the last which is full. `role="img"` with `aria-label={\`confidence over the last ${n} consolidations, now ${confidence}\`}`. Fewer than 8 readings draws what it has, left-aligned; **zero readings draws nothing at all** rather than a flat line, because a flat line is a claim.

- [ ] **Step 1: Write `RoutineRow.test.tsx` first** (~12): renders the name and trend; rising is green and falling is not red (assert the style value, as the other bench tests do); rail marks the current stage and announces it in text; `aria-expanded` flips; steps appear only when open; a step with no action renders its description alone; sparkline has the labelled `img` role with the right count; **no sparkline element at all for an empty history**; eight bars for a twelve-reading history; the last bar is the opaque one; the whole row is ≥44 px; `onToggle` fires with the routine's name.

- [ ] **Step 2: Write `MemoryBench.test.tsx`** (~18): the four sub-tabs render and switch via `memory.setTab`; **the browsing note is present on Episodic and quoted exactly**; hot and cold rows render their meta; typing does not call `submit`; submitting the form does; the three empty states each render their exact sentence; the 503 message renders *and the rows are still in the document*; a semantic file clamps and expands; the scratchpad shows `0 in the queue`; routines render one `RoutineRow` each; an error banner renders `memory.error`. Build `Memory` objects with a local `state(overrides)` factory rather than mocking the hook.

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
      last_fired: "2026-09-16T21:13:00Z" }), now))
      .toBe("recurring · cron 0 19 * * 4 · last fired 21:13");
  });
  it("formats a run_at, which it can read honestly", () => {
    expect(triggerMeta(trigger({ trigger_type: "time", one_shot: true,
      conditions: { run_at: "2026-09-17T08:40:00Z" }, last_fired: null }), now))
      .toBe("one-shot · runs 08:40 tomorrow · never fired");
  });
  it("names the entity a sensor trigger watches", () => {
    expect(triggerMeta(trigger({ trigger_type: "sensor",
      conditions: { entity_id: "binary_sensor.front_door", state_match: "on" },
      last_fired: null }), now))
      .toBe("recurring · binary_sensor.front_door is on · never fired");
  });
  it("drops the state clause when a sensor trigger matches any state", () => { /* "… on any change" */ });
  it("counts what a composite needs", () => {
    /* conditions: { children: [{}, {}, {}], require: 2 } -> "recurring · 2 of 3 conditions · …" */
  });
  it("says today, yesterday and a date for last_fired", () => { /* dayLabel */ });
  it("says nothing it cannot read rather than guessing", () => {
    expect(triggerMeta(trigger({ trigger_type: "time", conditions: {} }), now))
      .toBe("recurring · no schedule stored · never fired");
  });
});
```

`runs 08:40 tomorrow` comes from `hhmm` plus `dayLabel(date, now)` — both already in `lib/format.ts`; import them relatively. A `run_at` in the past renders as its date, not as `tomorrow`; add that test.

- [ ] **Step 2: Run, watch fail. Step 3: Implement.**

`triggerMeta` assembles `[one_shot ? "one-shot" : "recurring", <what it does>, <last fired>]` with ` · `. The middle clause is a `switch` on `triggerKind`. `fetchTriggers` unwraps `{triggers}`; `setTriggerEnabled` is `post(\`/api/admin/triggers/${encodeURIComponent(id)}/enabled\`, { enabled })` returning `void`; `fireTrigger` likewise on `/fire`. **`encodeURIComponent` on the id is not optional** — ids come from the LLM's trigger-creation tool.

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
13. `clears every timer on unmount` — unmount, advance, no fetch. Leaking a 60 s timer per toggle is how a long-lived PWA ends up storming the API.
14. `polls on nothing` — 5 minutes of fake time, still one fetch.

- [ ] **Step 2: Run, watch fail. Step 3: Implement.**

Timers in a `useRef<Map<string, ReturnType<typeof setTimeout>>>`, cleared in a `useEffect` cleanup. On each mutation: set `pending[id]`, `clearTimeout` any timer for that id, `setTimeout(() => { delete pending[id]; queryClient.invalidateQueries({ queryKey: ["triggers"] }); }, REREAD_MS)`.

- [ ] **Step 4: Green, lint, build. Step 5: Commit** `feat(web): the Triggers bench's hook`

---

## Task 6: `TriggerRow` and `TriggersBench`

**Files:** Create `web/src/workshop/TriggerRow.tsx`, `TriggerRow.test.tsx`, `TriggersBench.tsx`, `TriggersBench.test.tsx`.

### `TriggerRow({ trigger, pending, firedAt, open, onToggleOpen, onToggle, onFire })`

64 px minimum, `--line` divider, 16 px side padding.

- Left column: the kind label in mono `.t-meta`, uppercase — `time` / `schedule` / `sensor` / `composite`; the name in `.t-body`; `triggerMeta(trigger, Date.now())` in `.t-meta-strong`.
- Right: **a 52×32 switch that does not move on tap.** It is a `<button role="switch" aria-checked={trigger.enabled}>`; while `pending` is set it gains `aria-busy="true"`, its knob keeps its old position, and the pending note appears under the meta line:

  `queued 21:15 · enabling · takes effect within 60 s`

  in `--accent-text`, `.t-meta-strong`, mono. `disabling` for the other direction. The switch is `disabled` while pending — a second tap inside the window can only confuse the reader, and the server would queue a second action against a state neither of us knows.
- A failed mutation replaces the note with `<status> · that did not land · the scheduler still has the old setting`, where `<status>` is the ApiError's status. For a 500 specifically, add ` · this record cannot be read` — deviation 6's only reachable form.
- Expanded (`aria-expanded` on the row button): `created_by`, `created_at` via `dayLabel`, `urgency`; the action as `rawCall(action.tool_name, action.parameters)` in a mono `<pre>` when there is one and `no action · notification only` when there is not; the conditions as a mono `<pre>` of `JSON.stringify(conditions, null, 2)`; then **Fire now**.
- **Fire now** is a 44 px `<button>`, `--ink` on `--paper`. Tapping it makes it read `Queued` and disables it for the window. Under it, once `firedAt` is set: `queued 21:15 · the engine fires it when it next reads the queue`. Never `Fired`. We do not know that.

### `TriggersBench({ triggers }: { triggers: Triggers })`

Kind chips across the top in `StreamChips`' idiom (read that file; match it, do not fork it): `all · time · schedule · sensor · composite`, each 32 px, the selected one `--field` on `--surface`. A count beside `all` only — per-kind counts would need a second pass over the list for information nobody asked for.

Rows below in `flex-1 overflow-y-auto`. Empty: `No triggers yet.` for a genuinely empty list; `No <kind> triggers.` when a filter empties it. Error: the bench-wide `error` in a banner at the top, rows still shown beneath.

**The footer note, verbatim, always, under the list:**

> Changes are queued for the trigger engine. It applies them within 60 seconds. Nothing here edits a trigger — ask Alfred to change or remove one.

`.t-meta-strong`, 16 px padding, `env(safe-area-inset-bottom)` under it. The second sentence is doing real work: there is no create/edit/delete route (deviation), and a reader who does not know that will hunt for a button that is not there.

- [ ] **Step 1: `TriggerRow.test.tsx`** (~14): renders kind, name and meta; the switch reports `aria-checked` from `enabled`; **tapping while pending is impossible and the knob has not moved** (assert `aria-checked` is still the old value and the button is disabled); the queued note is exact; the failure note carries the status; a 500 adds its clause; expand reveals the action and conditions; a trigger with no action says `no action · notification only`; Fire now calls `onFire` once and then reads `Queued`; the row is ≥44 px; `onToggle` is not called by tapping the row body (expanding must not toggle — the classic defect in this layout, and worth its own test).
- [ ] **Step 2: `TriggersBench.test.tsx`** (~10): chips render and filter; the `all` count; both empty states with their exact sentences; **the footer note is quoted exactly**; the error banner leaves the rows in place; each trigger gets a row.
- [ ] **Step 3: Run both, fail. Step 4: Implement. Step 5: Green, lint, build.**

At the end of this task: **62 test files, ~951 tests.**

- [ ] **Step 6: Commit** `feat(web): the Triggers bench`

---

## Task 7: `lib/system.ts` and `useSystem`

**Files:** Create `web/src/lib/system.ts`, `system.test.ts`, `web/src/workshop/useSystem.ts`, `useSystem.test.tsx`. Modify `web/src/shell/ConnectionProvider.tsx` (`REHYDRATE_KEYS` gains `"system"`).

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

**Files:** Create `web/src/workshop/SystemBench.tsx`, `SystemBench.test.tsx`.

`SystemBench({ system }: { system: System })`. A scrolling column of sections. **The section frame** (used here and by task 9, so export it from this file):

```tsx
export function Section({ title, children }: { title: string; children: ReactNode }) …
```

— a caps `.t-meta-strong` label with `0.08em` letter-spacing in `--fg2`, 16 px above and 8 px below; then a `rounded-xl` container with a 1 px `--line` border and `--surface` background; rows inside it 56 px tall, divided by 1 px `--line`, 12 px side padding.

**Health** — a 2×2 grid of stat cells, each the big value in `.t-title` and the note under it in `.t-meta-strong`: bus, reflex, event rate, home assistant, exactly as `system.health` gives them. `alive` is the only thing that gets `--green`; `unknown` gets `--fg2`, never red.

Under the grid, the spend row: `spendNote(cost)` and a 4 px bar, `--accent` filling `spend_usd / cap_usd` clamped to `[0, 1]`, `--line` behind it. `role="img"` with the note as its label; a zero or missing cap draws an empty bar and the text says why.

**Quiet** —
- Row 1: `Do not disturb`, a `role="switch"` reporting `dnd.active`. This one **moves on tap**, once the server has confirmed, because `POST /api/admin/dnd` is a direct write (decision 6's exception). While in flight it is `aria-busy` and disabled.
- Row 2, only while active: expiry chips — `30 min`, `2 hours`, `until 07:00`, `no expiry` — each a ≥44 px button posting `active: true` with the computed `until` (an ISO string; `no expiry` sends `null`). The current one is `aria-pressed`.
- Row 3: `N held back ›`, opening the Held-back sheet through `system.quiet.onHeld`. Count from `overview.counts.deferred`.
- Row 4: `Send them now` — `drainDeferred()`. After it: `queued 21:15 · the notifier sends them when it next reads the queue`. Never `Sent`.
- The state sentence under the switch: `off · urgent still speaks regardless` / `on until 07:00 · urgent still speaks regardless` / `on with no expiry · this queue never drains on its own`.
- **The footnote, verbatim:** `A meeting in your calendar can also quiet Alfred; that is not shown here.` — `.t-meta-strong`. Deviation 11; it is the difference between a screen that is wrong and a screen that is honest about its blind spot.

**Maintenance** —
- `Last consolidation` — `librarian.last_run_at` via `dayLabel` + `hhmm`, `reviewed N memories`, `next <stamp>`; `never run` when null.
- `Run consolidation now` — a ≥44 px button; after it, `queued 21:15 · the Librarian starts on its own schedule`.
- `Version` and `Session idle timeout` rows: `session.idle_minutes` minutes, from the overview. No restart, no shutdown, no log download — none of it has a route (log the gap in the backlog).

- [ ] **Step 1: Write `SystemBench.test.tsx`** (~18) against a `state(overrides)` factory: the four health cells render their values and notes; `alive` is green and `unknown` is not red; the spend bar clamps at a full cap and does not produce `NaN` with a zero cap; the DND switch reports and flips; expiry chips only appear while active and post the right shape; **the calendar footnote is quoted exactly**; the three DND state sentences; held-back opens via the callback; drain and consolidation each render their exact queued note and never say "sent"/"done"; `never run` renders; every button is ≥44 px.
- [ ] **Step 2: Fail. Step 3: Implement. Step 4: Green, lint, build. Step 5: Commit** `feat(web): System bench health, quiet hours and maintenance`

---

## Task 9: `SystemSections` — Sessions, Connected services, Devices & identity, Reflex

**Files:** Create `web/src/workshop/SystemSections.tsx`, `SystemSections.test.tsx`. Modify `SystemBench.tsx` to render them.

Four exported components, each taking its own slice of `System`, each with its own tests. They live in one file because they share the `Section` frame and the same row idiom, and four one-component files would be four copies of the same imports.

### `SessionsSection({ sessions })`
One row per session: `device_name` in `.t-body`, `sessionMeta(s)` in `.t-meta-strong`, and **End** on the right (≥44 px, `--fg2`; `--accent-text` for your own, where it also reads **End · this device**). Ending your own signs you out — the server clears the cookie and the next read 401s into the Expired gate, which is correct and needs no special case here beyond the label warning you. Empty: `No other sessions.` (there is always at least one — yours).

### `ServicesSection({ integrations })`
One row per integration: the name, `category · kind` in `.t-meta`, a dot (filled `--green` for `ok`, `--line` ring otherwise) and `serviceNote(state)` in `.t-meta-strong`. Tapping a row expands a credential form built from `schema.fields`: one labelled input per field, `type="password"` for anything the schema marks secret (check the field shape in `core/channels/service_credentials.py` — do not guess the flag's name), each ≥16 px text, placeholder `configured[field] ? "saved" : ""` and **never the value itself**, which the server does not send and must not.

**Save & test** → `PUT` then re-`GET …/status`; the row reads `saved · testing` then settles. On 403: `Credentials can only be changed from the home network.` in `--fg2`, with the form left filled so nothing is lost. Do not route a 403 here into the Denied gate — `api` already emits `denied` for every 403; confirm what that does to this surface and, if the gate rises over a form the user can legitimately not use from here, say so in the deviations and handle it (the simplest honest handling is to let the gate rise, since it explains exactly this).

### `IdentitySection({ credentials, pairing })`
One row per passkey: `device_name`, `credentialMeta(c)`, and `this passkey` marking the one you signed in with. **Add a device** mints a pairing code and shows it 32 px, mono, letter-spaced, with `Pairing window closes 21:20 · enter this on the new device`. The code is shown until the sheet is left; there is no way to re-show it and the note says so. No delete button — `DELETE /api/auth/credentials/{id}` exists and refuses the last one with a 409, but removing the passkey you are holding is a foot-gun with no confirmation design in the handoff. Log it in the backlog and leave it out. *(If review disagrees, it is a small addition — but it ships with a typed confirmation or not at all.)*

### `ReflexSection({ attention })`
Deviation 13 — no fidelity-locked design, so it follows the frame. One sub-section per domain: the domain name as the section title, `members` as chips with an `×` that calls `ask(domain, entity)`, and `seen` entities not in `members` as hollow chips with a `+` that calls `allow(domain, entity)`. Above them: `Alfred acts on these without asking. Everything else it asks about first.` Empty: `Nothing is on the attention set yet.` A 503 from the store renders its detail — the endpoint deliberately distinguishes "nothing configured" from "the store is down", and this section must not flatten the two.

- [ ] **Step 1: Write `SystemSections.test.tsx`** (~28, roughly 7 per section) covering: each row's content; End's own-session label; the empty states; the credential form building from the schema, masking secrets, never pre-filling a value, and showing `saved`; Save & test calling PUT then status in order; the 403 sentence with the form intact; the pairing code and its closing time; `this passkey`; attention chips both ways with their callbacks; the attention 503 detail; the intro sentence quoted exactly.
- [ ] **Step 2: Fail. Step 3: Implement. Step 4: Wire the four into `SystemBench` between Quiet and Maintenance** (Health · Quiet · Sessions · Connected services · Devices & identity · Reflex · Maintenance) and extend `SystemBench.test.tsx` with one test asserting the seven section headings in order. **Step 5: Green, lint, build.**

At the end of this task: **66 test files, ~1011 tests.**

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
const system = useSystem(bench === "system", onHeld);
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
  13. recall stats on cold *browse* rows — the SELECT in `admin_api.py` omits `retrieval_count` and `last_retrieved`, so every cold browse row reads `never recalled`, and any cold row under the decay floor reads `decaying` whatever its real history. Search rows carry the stats; browse rows cannot.
- [ ] **Step 4: `docs/backlog/low/pwa-phase2-followups.md`** — close item §3 (the live region), noting task 10 moved it into the header.
- [ ] **Step 5: the QA checklist** — device steps in the phase-2 checklist's format, covering: all four benches reachable and each one's first paint; the episodic search keyboard (does the field zoom? it must not); a trigger toggle showing the queued note and the row not moving; a trigger toggle over a dropped connection; DND on and off from System and the Room's row agreeing; Held back opening from both places; the pairing code readable at arm's length; Save & test from off-LAN showing the network sentence; VoiceOver hearing one announcement per socket drop, not two; every bench under a 60 s socket outage.
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
