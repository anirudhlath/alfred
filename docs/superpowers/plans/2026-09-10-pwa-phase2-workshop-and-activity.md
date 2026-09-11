# PWA Phase 2 — The Workshop and the Activity bench

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 2 of spec §8 — the Workshop rises over the Room with its four-bench switcher, and the Activity bench shows the eight Redis streams live: filtered per stream, paged older by cursor, one event inspected in place, and a reflex observation traced to its causes in the `Why Alfred did that` sheet. Gate: *debugging is usable on the phone*.

**Architecture:** One pure reducer (`lib/feed.ts`) holds eight per-stream lists (newest first) plus a paused/held queue; a hook (`workshop/useActivity.ts`) feeds it from eight head-page reads and the telemetry socket, and merges the lists into one column above a *horizon* (rows older than the oldest loaded entry of a stream that still has more are withheld until paging brings that stream down, so the interleave never lies about gaps). `lib/streams.ts` names the streams and summarises any event into one line and one meta line; `lib/trace.ts` joins a reflex observation to the entries that share an id with it — `event_id`, `request_id`, `session_id`, `trigger_id`, `actions_taken`, `entity_id` — and marks everything else near it in time as dashed. The Workshop is a `Layer` at a new level (`workshop`, z-10) under sheets (z-20), the Door (z-30) and the gates (z-40). Memory, Triggers and System are switcher tabs that say `not built yet · phase 3`.

**Tech Stack:** Unchanged. Vite 8 · React 19 · TypeScript strict · Tailwind v4 · TanStack Query 5 · react-router 7 · Vitest 4 + Testing Library + jsdom · ESLint 10. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md` — §2 (the five things never to blur), §4.10 (rehydrate the feed gap), §4.12 (explicit dismiss), §5.1 (Activity, Causality), §5.2 (honesty), §7 (`lib/trace.ts`), §8 (phase 2 gate), §10 (passive observations).
**Design handoff:** `docs/design/2026-09-04-pwa-client-handoff/README.md` §"Workshop" (header, switcher, Activity bench, Why sheet — copy is final) and `Alfred.dc.html` lines 128–136 (the handle), 186–246 (the Workshop and the Activity bench), the `events`/`streams`/`pageBackLabel` bindings around line 840.
**Predecessors:** `docs/superpowers/plans/2026-09-07-pwa-phase1a-shell-and-gates.md`, `…-phase1b-room-and-door.md`, and PR #240's phone fixes. Everything they export is used here as spelled there.

---

## Product decisions already taken (do not reopen)

1. **Phase 1's decisions all still hold** — public repo (`alfred.example.com`, `192.168.1.x`, never a real address), plain-text replies, 401/403 as gates, `applied` only ever from the telemetry socket.
2. **The stack gains one level.** `Layer` grows `level: "workshop"` → `z-10`. Sheets stay at z-20 so the Why sheet paints over the Workshop; the Door (z-30) and the gates (z-40) paint over both. The inert stack in `presence.ts` is mount-ordered and needs no change.
3. **The Workshop is mounted in `Room.tsx`**, like `DoorLayer`, with a `workshopOpen` state. There is one `WhySheet`, also in `Room.tsx`, opened from a Room act row's `why?` and from the Workshop's `Why · causal thread` alike.
4. **Only reflex observations get a `why?`** — in the Room (act rows made by `reflexItem`) and in the Activity bench (`Why · causal thread` on RX rows). Notifications and triggers have no server-held cause to join on; a pill that always produced a dashed-only column would be noise.
5. **The feed is subscribed while the Workshop is up, whichever bench is showing**, and unsubscribed when it leaves. Subscriptions are reference-counted on the socket so the Door's `home_action_results` listener is never unsubscribed from under it.
6. **Time in the Workshop is the Redis id.** Every row, cursor and join window uses the millisecond half of the entry id — the server's clock, unambiguous where an ISO `timestamp` without a zone is not. The Room keeps reading `event.timestamp`, as it does today.
7. **Causality is a heuristic and says so.** Solid links are id joins; dashed links are adjacency in time. The client never claims `not caused by the above` (it cannot know); the footnote states exactly what was searched. A server-side correlation id is a follow-up (spec §7), logged in the phase-2 backlog.
8. **Nothing is virtualised.** `MAX_PER_STREAM = 400` entries per stream (3 200 rows worst case) with plain DOM; the backlog item from phase 1 (§4) carries forward.
9. **The older button sits at the top of the list, as the handoff draws it** (`Alfred.dc.html` line 206), and pages append at the bottom. Its label is the cursor readout as much as a control.

### Deviations from the handoff (deliberate; flag them in review)

| # | Where | Handoff says | This plan ships | Why |
|---|---|---|---|---|
| 1 | Bench switcher | four working benches | `Memory`, `Triggers`, `System` render a centred mono `not built yet · phase 3` | Spec §8 puts them in phase 3. A tab that does nothing must say so. |
| 2 | Empty stream note | `UR · 0 in the last 24 h · stream exists, no producers have written` | `UR · 0 entries · nothing has been written` — and `UR · nothing loaded yet` before the head read settles | The client reads a page, not a 24-hour window, so it cannot vouch for the number; and the server answers an empty page whether or not the Redis key exists, so it cannot vouch for "exists" either. |
| 3 | Stale banner | `Feed stopped at 21:14. Nothing below is live.` | adds `Feed has not been live yet. Nothing below is live.` when the socket has never been open this visit | A Workshop opened offline has no stop time to print. |
| 4 | Row meta | `380 ms · decision "…" · watch-listed`, `£0.004`, `risk`, `spoken/dnd/delivered`, `30-min window` | fields the events actually carry (`summarise`, Task 1) | None of those are on the bus events (`bus/schemas/events.py`). Printing them would be invention. |
| 5 | Why sheet, last node | `… · not caused by the above` | `… · adjacent in time only` | The client cannot prove a negative; it can only say what it did and did not join. Decision 7. |
| 6 | Why sheet, first node | `entity_id joins ↓` | HS→RX is joined by `event_id` (`ReflexObservation.trigger_event` is the originating event's full dump, id included) | Stricter and true; an `entity_id` join would attach every state change of that entity. |
| 7 | Older button | always present | present only while some shown stream has a cursor | A button that fetches nothing is a lie; a solo'd stream of three entries has no older page. |
| 8 | Footer padding | `padding-bottom 30px` | `12px + env(safe-area-inset-bottom)` | The prototype's 30 px is its mock home indicator; the real one is the inset (§4.3), as the Sheet already does. |
| 9 | Workshop status | `paused · 3 new` | same, but `last true HH:MM · not live` outranks it when the socket is down | §5.2: live is not last-known. Pause is still honoured underneath. |

---

## Before you start

1. **Worktree and branch.** This plan runs in `~/code/.worktrees/alfred/pwa-phase2-activity` on `feat/pwa-phase2-activity`, branched from `origin/master` at `d5d7277` (PR #240 merged). Never commit from `~/code/alfred-deploy/alfred` — a merge to `master` deploys.

```bash
cd ~/code/.worktrees/alfred/pwa-phase2-activity
git status --short
git log --oneline -1
```

Expected: no output from `status`, and `d5d7277 fix(web): pinned shell, notification bodies, and a Room that ends with the session (#240)` — or this plan's own `docs(web): plan PWA phase 2 …` commit above it.

2. **Confirm the client is green before adding to it.**

```bash
cd ~/code/.worktrees/alfred/pwa-phase2-activity/web
npm run lint && npx vitest run && npm run build
```

Expected: eslint prints nothing, `Test Files  42 passed (42)`, `Tests  636 passed (636)`, vite prints `✓ built in …`. A red baseline is master's problem, not yours. **`npx vitest run` from `web/`, never from the repo root.**

3. **How to run things** (all from `web/`):

| Command | What it does |
|---|---|
| `npx vitest run` | The whole suite, once |
| `npx vitest run src/lib/feed.test.ts` | One file |
| `npx vitest run -t "holds live entries while paused"` | One test by name |
| `npm run lint` | ESLint over `web/` |
| `npm run build` | `tsc -b` then `vite build` — the type check lives here, not in lint |

CI runs node 22 in UTC. Tests must not depend on the wall clock or the zone: construct dates with `new Date(y, m, d, h, mi, s)` and ids with `Date.UTC(...)` where the plan does.

4. **Test-file and test counts as you go.** Master is at 42 files / 636 tests. Each task states the counts it should reach: 1 → 43 / 653 · 2 → 44 / 671 · 3 → 44 / 675 · 4 → 45 / 683 · 5 → 47 / 689 · 6 → 48 / 698 · 7 → 50 / 707 · 8 → 51 / 723 · 9 → 52 / 728 · 10 → 53 / 736. A different number means a test was skipped or duplicated — find it before moving on.

5. **The backend this plan calls** — all on `origin/master`:

```bash
cd ~/code/.worktrees/alfred/pwa-phase2-activity
grep -n 'before\|max_id\|next_before' core/channels/admin_api.py | grep -n 'streams\|max_id\|next_before' | head
grep -n '"subscribe"\|"unsubscribe"' core/channels/telemetry_ws.py
grep -n 'trigger_event=' core/reflex/runner.py
```

Expected: the stream route reads `before` as an exclusive `max_id = f"({before}"` and returns `next_before` only on a full page; the telemetry loop handles both frame types; `trigger_event=event.model_dump()` — the full originating event, `event_id` included.

---

## Conventions

Identical to phase 1, repeated because they are load-bearing:

- **TDD, always.** Failing test → run it and read the failure → implement → run it green → commit.
- **Conventional commits**, one per task minimum.
- **Imports use the `@/` alias** everywhere except inside `src/lib/`, where siblings are relative (`./format`, `./types`).
- **Named exports** everywhere except `App.tsx`.
- **Colours come from CSS custom properties** — `style={{ color: "var(--muted)" }}`. The monogram tile's white is the one literal (`#fff`, the prototype's own); never a Tailwind palette colour.
- **Type comes from the `.t-*` classes** (`t-row`, `t-meta`, `t-monogram`, `t-title`). Sizes outside the scale (13 px banner, 14 px event line, 10 px chip, 11 px older button) are explicit arbitrary values.
- **Tests live next to the source.** Each test file builds its own providers — a fresh `QueryClient` per file.
- **The status vocabulary is closed** (handoff): `queued`, `applied`, `last true HH:MM`, `unknown since HH:MM`, `hot / cold`, `candidate · active · dormant · archived`, `expired · not done`, `takes effect within 60 s`. Always mono, always lower case. `live · 2.1 ev/s`, `paused · 3 new` and `last true 21:14 · not live` are the Workshop's three.
- **Secret hygiene before any push** — run the two `git grep` checks from the operator's exposure runbook (`~/code/alfred-deploy/PWA-EXPOSURE-RUNBOOK.md`, §"Secret hygiene", outside this repo). Both must print nothing. They are deliberately not quoted here: writing them into a public tree would commit the very literals they hunt for.

- **No placeholders, no dead code.**

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `web/src/lib/streams.ts` | The eight stream names in chip order, monogram + hue, id maths, `fetchStreamPage`, `summarise` | 1 |
| `web/src/lib/format.ts` | + `hhmmss` | 1 |
| `web/src/lib/feed.ts` | Pure feed reducer: per-stream lists, paging, live inserts, pause/hold, horizon merge | 2 |
| `web/src/lib/telemetry-socket.ts` | + reference-counted `unsubscribe` | 3 |
| `web/src/shell/Layer.tsx` | + `level: "workshop"` (z-10) | 3 |
| `web/src/workshop/useActivity.ts` | The feed hook: head reads, subscription, rehydrate, solo/expand/pause/older | 4 |
| `web/src/workshop/EventRow.tsx` | One event: monogram, line, meta, expanded payload + pills | 5 |
| `web/src/workshop/StreamChips.tsx` | Eight chips with counts; solo | 5 |
| `web/src/workshop/ActivityBench.tsx` | Stale banner, list with older button, empty states, footer | 6 |
| `web/src/workshop/BenchSwitcher.tsx` | The four-tab segmented control | 7 |
| `web/src/workshop/Workshop.tsx` | The layer: header (`‹ Room`, status), switcher, benches | 7 |
| `web/src/lib/trace.ts` | Candidate fetch + `buildThread` (joins, adjacency, order) | 8 |
| `web/src/sheets/WhySheet.tsx` | `Why Alfred did that`: intro, column of nodes, footnote | 9 |
| `web/src/room/WorkshopHandle.tsx` | The handle under the composer | 10 |
| `web/src/room/Composer.tsx`, `room/rows/ActRow.tsx`, `room/Timeline.tsx`, `lib/history.ts`, `room/Room.tsx` | the handle slot; `why?` on reflex act rows; mount Workshop + WhySheet | 10 |
| `web/README.md`, `docs/web-frontend.md`, `docs/backlog/low/pwa-phase2-followups.md`, `docs/superpowers/qa/2026-09-10-pwa-phase2-ios-checklist.md` | Docs, backlog, QA | 11 |

### The contract between tasks (names used exactly as spelled)

```ts
// lib/streams.ts
export const STREAMS: readonly ["user_requests","user_responses","events","actions","reflex_observations","notifications","home_state","home_action_results"];
export type StreamName; export const STREAM_INFO: Record<StreamName, { mono: string; hue: number }>;
export function isStreamName(value: string): value is StreamName;
export function ring(hue: number): string;                     // `oklch(0.62 0.11 ${hue})`
export function idMs(id: string): number; export function compareIds(a: string, b: string): number;
export const PAGE_COUNT = 50;
export function fetchStreamPage(name: StreamName, before?: string | null, count?: number): Promise<StreamPage>;
export interface StreamRef { stream: StreamName; entry: StreamEntry }
export interface Summary { text: string; meta: string }
export function summarise(stream: StreamName, event: Record<string, unknown>): Summary;
export function scalar(value: unknown): string | null; export function record(value: unknown): Record<string, unknown> | null; export function strings(value: unknown): string[];

// lib/format.ts
export function hhmmss(value: string | number | Date): string;

// lib/feed.ts
export const MAX_PER_STREAM = 400;
export interface FeedRow extends StreamRef { key: string }     // `${stream}:${id}`
export interface StreamFeed { entries: StreamEntry[]; nextBefore: string | null; loaded: boolean }
export interface FeedState { streams: Record<StreamName, StreamFeed>; paused: boolean; held: FeedRow[]; liveAt: number | null }
export type FeedEvent = page | live | seen | pause | resume;
export function initialFeed(): FeedState; export function feedReducer(state, event): FeedState;
export function mergeRows(state: FeedState, streams: readonly StreamName[]): { rows: FeedRow[]; cursor: string | null };
export function olderTargets(state: FeedState, streams: readonly StreamName[]): StreamName[];

// lib/telemetry-socket.ts
unsubscribe(streams: string[]): void;

// shell/Layer.tsx
level?: "workshop" | "layer" | "gate";

// workshop/useActivity.ts
export interface Activity { rows; counts; live; paused; liveAt; heldCount; solo; setSolo; expanded; toggle; pause; resume; cursor; fetchingOlder; loadOlder; loaded; error }
export function useActivity(): Activity;

// lib/trace.ts
export const JOIN_WINDOW_MS = 600_000; export const ADJACENT_MS = 5_000; export const MAX_ADJACENT = 6; export const ENTITY_MS = 60_000; export const CANDIDATE_COUNT = 100;
export type Link = "anchor" | "adjacent" | { key: string; value: string };
export interface ThreadNode extends StreamRef { link: Link }
export interface Thread { nodes: ThreadNode[]; searched: number }
export interface Candidates { candidates: StreamRef[]; searched: number }
export function fetchThreadCandidates(anchor: StreamRef): Promise<Candidates>;
export function buildThread(anchor: StreamRef, candidates: StreamRef[]): ThreadNode[];
export function fetchThread(anchor: StreamRef): Promise<Thread>;
export function nodeMeta(node: ThreadNode): string;

// sheets/WhySheet.tsx      export function WhySheet({ anchor: StreamRef | null; onClose }): JSX
// workshop/Workshop.tsx    export function Workshop({ open; onClose; onWhy: (ref: StreamRef) => void }): JSX
// room/WorkshopHandle.tsx  export function WorkshopHandle({ onOpen }): JSX
// lib/history.ts           act items gain `why?: StreamRef` (reflexItem only)
// room/Timeline.tsx        + onWhy?: (ref: StreamRef) => void
// room/rows/ActRow.tsx     + onWhy?: () => void
```

---

## Task 1: Stream names, id maths, page reads and one-line summaries

**Files:**
- Create: `web/src/lib/streams.ts`
- Create: `web/src/lib/streams.test.ts`
- Modify: `web/src/lib/format.ts` (add `hhmmss` after `hhmm`)
- Modify: `web/src/lib/format.test.ts` (add a `describe("hhmmss")` after `describe("hhmm")`)

Everything the Workshop knows about a stream lives here: its name, monogram and hue (the handoff's rings, `oklch(0.62 0.11 h)`), how to read a page of it, and how to say one event in one line and one meta line. `history.ts` keeps its own inline fetch — the Room's four-page read is not touched.

- [ ] **Step 1: Write the failing tests**

`web/src/lib/format.test.ts` — add after the `describe("hhmm", …)` block:

```ts
describe("hhmmss", () => {
  it("prints the device's clock to the second", () => {
    expect(hhmmss(new Date(2026, 8, 7, 21, 2, 11))).toBe("21:02:11");
    expect(hhmmss(new Date(2026, 8, 7, 0, 0, 0).getTime())).toBe("00:00:00");
  });

  it("says nothing it cannot read", () => {
    expect(hhmmss("not a date")).toBe("--:--:--");
  });
});
```

and add `hhmmss` to the import list at the top of the file.

`web/src/lib/streams.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  compareIds,
  fetchStreamPage,
  idMs,
  isStreamName,
  STREAM_INFO,
  STREAMS,
  summarise,
} from "./streams";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("STREAMS", () => {
  it("lists the eight catalogued streams in the handoff's chip order", () => {
    expect(STREAMS).toEqual([
      "user_requests",
      "user_responses",
      "events",
      "actions",
      "reflex_observations",
      "notifications",
      "home_state",
      "home_action_results",
    ]);
    expect(STREAMS.map((name) => STREAM_INFO[name].mono)).toEqual([
      "UR", "AL", "EV", "AC", "RX", "NT", "HS", "HR",
    ]);
    expect(STREAMS.map((name) => STREAM_INFO[name].hue)).toEqual([
      30, 75, 120, 165, 210, 255, 300, 345,
    ]);
    expect(isStreamName("home_state")).toBe(true);
    expect(isStreamName("home-state")).toBe(false);
  });
});

describe("stream ids", () => {
  it("reads the millisecond half of an id, and 0 for garbage", () => {
    expect(idMs("1788815640000-0")).toBe(1788815640000);
    expect(idMs("garbage")).toBe(0);
  });

  it("orders by milliseconds, then sequence", () => {
    expect(compareIds("1788815640000-0", "1788815640000-1")).toBeLessThan(0);
    expect(compareIds("1788815640001-0", "1788815640000-9")).toBeGreaterThan(0);
    expect(compareIds("1788815640000-2", "1788815640000-2")).toBe(0);
  });
});

describe("fetchStreamPage", () => {
  it("asks for a page of fifty, before a cursor when given one", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL) =>
        new Response(JSON.stringify({ entries: [], next_before: null }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchStreamPage("events");
    await fetchStreamPage("events", "1788815640000-0");
    await fetchStreamPage("events", null, 100);

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/admin/streams/events?count=50",
      "/api/admin/streams/events?count=50&before=1788815640000-0",
      "/api/admin/streams/events?count=100",
    ]);
  });

  it("normalises a page with nothing in it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    expect(await fetchStreamPage("events")).toEqual({ entries: [], next_before: null });
  });
});

describe("summarise", () => {
  it("user_requests: the words, then session · channel · type", () => {
    expect(
      summarise("user_requests", {
        content: "Is the back door locked?",
        session_id: "s_9f3",
        channel: "web_pwa",
        content_type: "text",
      }),
    ).toEqual({ text: "Is the back door locked?", meta: "session s_9f3 · web_pwa · text" });
    expect(
      summarise("user_requests", { content: "", session_id: "s_9f3", channel: "voice", content_type: "audio" }),
    ).toEqual({ text: "voice message", meta: "session s_9f3 · voice · audio" });
  });

  it("user_responses: the reply, then session · mood · tools", () => {
    expect(
      summarise("user_responses", {
        text: "It is locked, sir.",
        session_id: "s_9f3",
        mood: "pleased",
        actions_taken: ["home.get_state", "weather.forecast"],
      }),
    ).toEqual({
      text: "It is locked, sir.",
      meta: "session s_9f3 · mood pleased · home.get_state, weather.forecast",
    });
    expect(summarise("user_responses", { text: "Certainly.", session_id: "s_9f3" })).toEqual({
      text: "Certainly.",
      meta: "session s_9f3 · mood neutral · no tools",
    });
  });

  it("events: trigger fired, trigger created, service registered, anything else", () => {
    expect(
      summarise("events", {
        event_type: "trigger_fired",
        trigger_name: "front door open",
        trigger_type: "sensor",
        urgency: "important",
        fired_by: "engine",
      }),
    ).toEqual({ text: "trigger fired: front door open", meta: "fired by engine · sensor · important" });
    expect(
      summarise("events", {
        event_type: "trigger_created",
        name: "bins out",
        trigger_type: "time",
        created_by: "conscious",
        one_shot: true,
      }),
    ).toEqual({ text: "trigger created: bins out", meta: "one-shot · time · by conscious" });
    expect(
      summarise("events", { event_type: "service_registered", service_name: "home-service", source: "home-service" }),
    ).toEqual({ text: "service registered: home-service", meta: "source home-service" });
    expect(summarise("events", { event_type: "state_changed", source: "bus" })).toEqual({
      text: "state changed",
      meta: "source bus",
    });
  });

  it("actions: tool and entity, then request · service · source · confirmed", () => {
    expect(
      summarise("actions", {
        tool_name: "home.light_set",
        parameters: { entity_id: "light.living_room", brightness: 30 },
        request_id: "4b1d9e00",
        target_service: "home-service",
        source: "reflex",
        confirmed: false,
      }),
    ).toEqual({
      text: "home.light_set light.living_room",
      meta: "request 4b1d · home-service · reflex",
    });
    expect(
      summarise("actions", {
        tool_name: "home.lock_unlock",
        parameters: {},
        request_id: "a91f3c2e",
        target_service: "home-service",
        source: "conscious",
        confirmed: true,
      }),
    ).toEqual({ text: "home.lock_unlock", meta: "request a91f · home-service · conscious · confirmed" });
  });

  it("reflex_observations: what was seen and whether it acted, then tool · request · decision", () => {
    expect(
      summarise("reflex_observations", {
        origin: "state_change",
        trigger_event: { entity_id: "media_player.tv", new_state: "playing" },
        action: { request_id: "4b1d9e00", tool_name: "home.light_set" },
        result: { status: "success" },
        decision_context: "movie started, evening, user home",
      }),
    ).toEqual({
      text: "observed media_player.tv · acted",
      meta: 'home.light_set · request 4b1d · decision "movie started, evening, user home"',
    });
    expect(
      summarise("reflex_observations", {
        origin: "state_change",
        trigger_event: { entity_id: "fan.bathroom", new_state: "on" },
        action: { request_id: "8d2a0000", tool_name: "home.fan_set" },
        result: { status: "error", error: "service unavailable" },
        decision_context: null,
      }),
    ).toEqual({ text: "observed fan.bathroom · acted", meta: "home.fan_set · request 8d2a · failed" });
    expect(
      summarise("reflex_observations", {
        origin: "trigger_fired",
        trigger_event: { trigger_name: "front door open" },
        action: null,
        result: null,
        decision_context: "user moving about, lights already on",
      }),
    ).toEqual({
      text: "observed front door open · watched, took no action",
      meta: 'decision "user moving about, lights already on"',
    });
  });

  it("notifications: the body, then urgency · source · confirmation", () => {
    expect(
      summarise("notifications", {
        title: "Your parcel arrived",
        body: "The door sensor saw it at 18:20.",
        urgency: "important",
        source: "trigger-engine",
        metadata: {},
      }),
    ).toEqual({ text: "The door sensor saw it at 18:20.", meta: "important · trigger-engine" });
    expect(
      summarise("notifications", {
        title: "Confirmation required",
        body: "",
        urgency: "urgent",
        source: "domain-router",
        metadata: { pending_action_id: "a91f3c2e" },
      }),
    ).toEqual({ text: "Confirmation required", meta: "urgent · domain-router · confirmation a91f" });
  });

  it("home_state: entity → state, then domain · was · via", () => {
    expect(
      summarise("home_state", {
        entity_id: "binary_sensor.front_door",
        new_state: "on",
        old_state: "off",
        domain: "home",
        source: "home-service",
      }),
    ).toEqual({ text: "binary_sensor.front_door → on", meta: "home · was off · via home-service" });
    expect(
      summarise("home_state", { entity_id: "light.hall", new_state: "off", domain: "home", source: "home-service" }),
    ).toEqual({ text: "light.hall → off", meta: "home · was unknown · via home-service" });
  });

  it("home_action_results: tool and status, then request · error", () => {
    expect(
      summarise("home_action_results", { tool_name: "home.light_set", status: "success", request_id: "4b1d9e00" }),
    ).toEqual({ text: "home.light_set success", meta: "request 4b1d" });
    expect(
      summarise("home_action_results", {
        tool_name: "home.fan_set",
        status: "error",
        request_id: "8d2a0000",
        error: "service unavailable",
      }),
    ).toEqual({ text: "home.fan_set error", meta: "request 8d2a · service unavailable" });
  });

  it("never throws on an event with none of the fields", () => {
    for (const stream of STREAMS) {
      const { text, meta } = summarise(stream, {});
      expect(text.length).toBeGreaterThan(0);
      expect(typeof meta).toBe("string");
    }
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/lib/streams.test.ts src/lib/format.test.ts`
Expected: `streams.test.ts` fails to import (`./streams` does not exist); `format.test.ts` fails with `hhmmss is not a function` / no such export.

- [ ] **Step 3: Add `hhmmss` to `web/src/lib/format.ts`**

Directly after `hhmm`:

```ts
/** `21:02:11` — the Workshop's row stamp, to the second. Same clock as `hhmm`. */
export function hhmmss(value: string | number | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}
```

- [ ] **Step 4: Write `web/src/lib/streams.ts`**

```ts
import { api } from "./api";
import { notificationText, shortId } from "./format";
import type { StreamEntry, StreamPage } from "./types";

/** The eight catalogued streams, in the handoff's chip order (README §Workshop). */
export const STREAMS = [
  "user_requests",
  "user_responses",
  "events",
  "actions",
  "reflex_observations",
  "notifications",
  "home_state",
  "home_action_results",
] as const;

export type StreamName = (typeof STREAMS)[number];

/** Monogram and ring hue per stream. The Room's act rows use 120/210/255 of these. */
export const STREAM_INFO: Record<StreamName, { mono: string; hue: number }> = {
  user_requests: { mono: "UR", hue: 30 },
  user_responses: { mono: "AL", hue: 75 },
  events: { mono: "EV", hue: 120 },
  actions: { mono: "AC", hue: 165 },
  reflex_observations: { mono: "RX", hue: 210 },
  notifications: { mono: "NT", hue: 255 },
  home_state: { mono: "HS", hue: 300 },
  home_action_results: { mono: "HR", hue: 345 },
};

export function isStreamName(value: string): value is StreamName {
  return (STREAMS as readonly string[]).includes(value);
}

/** The handoff's stream ring: one chroma, one lightness, eight hues. */
export function ring(hue: number): string {
  return `oklch(0.62 0.11 ${hue})`;
}

/** `1788815640000-0` → `[1788815640000, 0]`. Anything unreadable is 0, never NaN, so sorts stay total. */
function idParts(id: string): [number, number] {
  const [ms, seq] = id.split("-");
  return [Number(ms) || 0, Number(seq) || 0];
}

/**
 * When an entry happened: the millisecond half of its Redis id, which is the
 * server's clock at the moment of the write. Preferred over `event.timestamp`
 * in the Workshop because an ISO stamp without a zone is ambiguous and the id
 * never is.
 */
export function idMs(id: string): number {
  return idParts(id)[0];
}

/** Redis stream order: milliseconds, then sequence. Negative when `a` is older. */
export function compareIds(a: string, b: string): number {
  const [aMs, aSeq] = idParts(a);
  const [bMs, bSeq] = idParts(b);
  return aMs - bMs || aSeq - bSeq;
}

export const PAGE_COUNT = 50;

/**
 * One page of a stream, newest first. `before` is exclusive on the server
 * (`max_id = "(" + before`), so passing the oldest id you hold gives the page
 * that ends just before it. The server clamps `count` to 1..200.
 */
export async function fetchStreamPage(
  name: StreamName,
  before?: string | null,
  count: number = PAGE_COUNT,
): Promise<StreamPage> {
  const params = new URLSearchParams({ count: String(count) });
  if (before) params.set("before", before);
  const page = await api<Partial<StreamPage>>(`/api/admin/streams/${name}?${params}`);
  return { entries: page.entries ?? [], next_before: page.next_before ?? null };
}

/** One entry of one stream — what a row, a `why?` and a thread node all point at. */
export interface StreamRef {
  stream: StreamName;
  entry: StreamEntry;
}

export interface Summary {
  /** The row's line: 14 px, ellipsised. */
  text: string;
  /** The row's meta, without the stamp: mono 11, ellipsised. Parts joined by ` · `. */
  meta: string;
}

/** A string, number or boolean as text; anything else (including "") is `null`. Shared with `trace.ts`. */
export function scalar(value: unknown): string | null {
  if (typeof value === "string") return value.length > 0 ? value : null;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

export function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** ` · `-join, dropping anything empty, so a missing field leaves no ` ·  · `. */
function join(parts: (string | null | false | undefined)[]): string {
  return parts.filter((part): part is string => typeof part === "string" && part.length > 0).join(" · ");
}

/**
 * One line and one meta line for any event on any of the eight streams, from
 * the fields `bus/schemas/events.py` and `core/notifications/schema.py`
 * actually carry. Every field is optional here: a producer that omitted one
 * gets a shorter line, never a crash and never a made-up value (spec §5.2).
 */
export function summarise(stream: StreamName, event: Record<string, unknown>): Summary {
  const source = scalar(event.source);
  switch (stream) {
    case "user_requests": {
      const session = scalar(event.session_id);
      return {
        text: scalar(event.content) ?? "voice message",
        meta: join([session && `session ${session}`, scalar(event.channel), scalar(event.content_type)]),
      };
    }
    case "user_responses": {
      const session = scalar(event.session_id);
      const tools = strings(event.actions_taken);
      return {
        text: scalar(event.text) ?? "reply",
        meta: join([
          session && `session ${session}`,
          `mood ${scalar(event.mood) ?? "neutral"}`,
          tools.length > 0 ? tools.join(", ") : "no tools",
        ]),
      };
    }
    case "events": {
      const type = scalar(event.event_type);
      if (type === "trigger_fired") {
        return {
          text: `trigger fired: ${scalar(event.trigger_name) ?? "trigger"}`,
          meta: join([
            `fired by ${scalar(event.fired_by) ?? "engine"}`,
            scalar(event.trigger_type),
            scalar(event.urgency),
          ]),
        };
      }
      if (type === "trigger_created") {
        const by = scalar(event.created_by);
        return {
          text: `trigger created: ${scalar(event.name) ?? "trigger"}`,
          meta: join([event.one_shot === true ? "one-shot" : "recurring", scalar(event.trigger_type), by && `by ${by}`]),
        };
      }
      if (type === "service_registered") {
        return {
          text: `service registered: ${scalar(event.service_name) ?? "service"}`,
          meta: join([source && `source ${source}`]),
        };
      }
      return { text: type ? type.replace(/_/g, " ") : "event", meta: join([source && `source ${source}`]) };
    }
    case "actions": {
      const tool = scalar(event.tool_name) ?? "action";
      const entity = scalar(record(event.parameters)?.entity_id);
      const request = scalar(event.request_id);
      return {
        text: entity ? `${tool} ${entity}` : tool,
        meta: join([
          request && `request ${shortId(request)}`,
          scalar(event.target_service),
          source,
          event.confirmed === true && "confirmed",
        ]),
      };
    }
    case "reflex_observations": {
      const seen = record(event.trigger_event);
      const subject =
        scalar(seen?.entity_id) ?? scalar(seen?.trigger_name) ?? scalar(event.origin) ?? "event";
      const action = record(event.action);
      const request = scalar(action?.request_id);
      const decision = scalar(event.decision_context);
      const failed = scalar(record(event.result)?.status) === "error";
      // Spec §10: a passive observation reads "watched, took no action".
      return {
        text: `observed ${subject} · ${action ? "acted" : "watched, took no action"}`,
        meta: join([
          action && (scalar(action.tool_name) ?? "action"),
          request && `request ${shortId(request)}`,
          decision && `decision "${decision}"`,
          failed && "failed",
        ]),
      };
    }
    case "notifications": {
      const pending = scalar(record(event.metadata)?.pending_action_id);
      return {
        text: notificationText(event.body, event.title) ?? "notification",
        meta: join([scalar(event.urgency), source, pending && `confirmation ${shortId(pending)}`]),
      };
    }
    case "home_state": {
      const entity = scalar(event.entity_id) ?? "entity";
      return {
        text: `${entity} → ${scalar(event.new_state) ?? "unknown"}`,
        meta: join([
          scalar(event.domain),
          `was ${scalar(event.old_state) ?? "unknown"}`,
          source && `via ${source}`,
        ]),
      };
    }
    case "home_action_results": {
      const request = scalar(event.request_id);
      return {
        text: `${scalar(event.tool_name) ?? "action"} ${scalar(event.status) ?? "unknown"}`,
        meta: join([request && `request ${shortId(request)}`, scalar(event.error)]),
      };
    }
  }
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/lib/streams.test.ts src/lib/format.test.ts`
Expected: both files green — `streams.test.ts` 13 tests, `format.test.ts` its previous count + 2.

Then the whole suite: `npx vitest run` → `Test Files  43 passed (43)`, `Tests  653 passed (653)`.

- [ ] **Step 6: Lint and commit**

```bash
cd web && npm run lint
git add src/lib/streams.ts src/lib/streams.test.ts src/lib/format.ts src/lib/format.test.ts
git commit -m "feat(web): name the eight streams, read a page of one, summarise any event"
```

## Task 2: The feed reducer — pages, live entries, pause, the horizon

**Files:**
- Create: `web/src/lib/feed.ts`
- Create: `web/src/lib/feed.test.ts`

Pure state, no React, no fetch. The Activity bench is a merged, newest-first view over up to eight independently paged streams; this module owns the two hard parts: keeping each stream's entries unique and in Redis order across head reads, older pages and live frames, and deciding **how far back the merged view is allowed to show** (the *horizon*). A stream that can still page is only known back to its oldest loaded entry; showing another stream's rows from before that moment would put one stream's past next to another's silence, so rows older than the shallowest pageable stream stay hidden until `↑ older` fetches that stream deeper. The horizon is also the cursor the older button prints.

- [ ] **Step 1: Write the failing tests**

`web/src/lib/feed.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  feedReducer,
  initialFeed,
  MAX_PER_STREAM,
  mergeRows,
  olderTargets,
  type FeedEvent,
  type FeedState,
} from "./feed";
import { STREAMS, type StreamName } from "./streams";
import type { StreamEntry, StreamPage } from "./types";

const BASE = 1788815640000;

/** An entry `offsetMs` after BASE. `seq` is the Redis sequence half of the id. */
function entry(offsetMs: number, seq = 0): StreamEntry {
  return { id: `${BASE + offsetMs}-${seq}`, event: { n: offsetMs } };
}

function page(entries: StreamEntry[], next_before: string | null = null): StreamPage {
  return { entries, next_before };
}

function head(stream: StreamName, entries: StreamEntry[], next_before: string | null = null): FeedEvent {
  return { type: "page", stream, mode: "head", page: page(entries, next_before) };
}

function older(stream: StreamName, entries: StreamEntry[], next_before: string | null = null): FeedEvent {
  return { type: "page", stream, mode: "older", page: page(entries, next_before) };
}

function live(stream: StreamName, e: StreamEntry, at: number): FeedEvent {
  return { type: "live", stream, entry: e, at };
}

function reduce(events: FeedEvent[], from: FeedState = initialFeed()): FeedState {
  return events.reduce(feedReducer, from);
}

function ids(state: FeedState, stream: StreamName): string[] {
  return state.streams[stream].entries.map((e) => e.id);
}

describe("initialFeed", () => {
  it("starts with eight empty streams, not paused, nothing held, never live", () => {
    const state = initialFeed();
    expect(Object.keys(state.streams)).toEqual([...STREAMS]);
    for (const name of STREAMS) {
      expect(state.streams[name]).toEqual({ entries: [], nextBefore: null, loaded: false });
    }
    expect(state).toMatchObject({ paused: false, held: [], liveAt: null });
  });
});

describe("head pages", () => {
  it("fills an empty stream, newest first, and keeps the server's cursor", () => {
    const state = reduce([head("events", [entry(1000), entry(3000), entry(2000)], entry(1000).id)]);
    expect(ids(state, "events")).toEqual([entry(3000).id, entry(2000).id, entry(1000).id]);
    expect(state.streams.events).toMatchObject({ nextBefore: entry(1000).id, loaded: true });
  });

  it("merges an overlapping refresh and keeps the deeper cursor", () => {
    const state = reduce([
      head("events", [entry(3000), entry(2000), entry(1000)], entry(1000).id),
      head("events", [entry(4000), entry(3000), entry(2000)], entry(2000).id),
    ]);
    expect(ids(state, "events")).toEqual([4000, 3000, 2000, 1000].map((ms) => entry(ms).id));
    expect(state.streams.events.nextBefore).toBe(entry(1000).id);
  });

  it("starts over when a refresh leaves a gap it cannot see across", () => {
    const state = reduce([
      head("events", [entry(2000), entry(1000)], entry(1000).id),
      head("events", [entry(9000), entry(8000)], entry(8000).id),
    ]);
    expect(ids(state, "events")).toEqual([entry(9000).id, entry(8000).id]);
    expect(state.streams.events.nextBefore).toBe(entry(8000).id);
  });

  it("marks a stream complete when the page has no cursor", () => {
    const state = reduce([head("events", [entry(1000)])]);
    expect(state.streams.events).toEqual({ entries: [entry(1000)], nextBefore: null, loaded: true });
  });

  it("drops duplicate ids", () => {
    const state = reduce([
      head("events", [entry(2000), entry(1000)], entry(1000).id),
      head("events", [entry(2000), entry(1000)], entry(1000).id),
    ]);
    expect(ids(state, "events")).toEqual([entry(2000).id, entry(1000).id]);
  });
});

describe("older pages", () => {
  it("appends below and moves the cursor to the server's", () => {
    const state = reduce([
      head("events", [entry(3000), entry(2000)], entry(2000).id),
      older("events", [entry(1000), entry(500)], entry(500).id),
    ]);
    expect(ids(state, "events")).toEqual([3000, 2000, 1000, 500].map((ms) => entry(ms).id));
    expect(state.streams.events.nextBefore).toBe(entry(500).id);
  });

  it("an empty page with no cursor ends paging", () => {
    const state = reduce([
      head("events", [entry(3000), entry(2000)], entry(2000).id),
      older("events", []),
    ]);
    expect(ids(state, "events")).toEqual([entry(3000).id, entry(2000).id]);
    expect(state.streams.events).toMatchObject({ nextBefore: null, loaded: true });
  });
});

describe("live entries", () => {
  it("goes to the top and stamps when the feed was last live", () => {
    const state = reduce([
      head("events", [entry(2000), entry(1000)], entry(1000).id),
      live("events", entry(3000), 42),
    ]);
    expect(ids(state, "events")).toEqual([entry(3000).id, entry(2000).id, entry(1000).id]);
    expect(state.liveAt).toBe(42);
    expect(feedReducer(state, { type: "seen", at: 43 }).liveAt).toBe(43);
  });

  it("ignores an id already held", () => {
    const state = reduce([
      head("events", [entry(2000), entry(1000)], entry(1000).id),
      live("events", entry(2000), 42),
    ]);
    expect(ids(state, "events")).toEqual([entry(2000).id, entry(1000).id]);
  });

  it("keeps the newest MAX_PER_STREAM and points the cursor at the last one kept", () => {
    const full = Array.from({ length: MAX_PER_STREAM }, (_, i) => entry((MAX_PER_STREAM - i) * 1000));
    const state = reduce([
      head("events", full),
      live("events", entry((MAX_PER_STREAM + 1) * 1000), 1),
    ]);
    const kept = ids(state, "events");
    expect(kept).toHaveLength(MAX_PER_STREAM);
    expect(kept[0]).toBe(entry((MAX_PER_STREAM + 1) * 1000).id);
    expect(kept[MAX_PER_STREAM - 1]).toBe(entry(2000).id);
    expect(state.streams.events.nextBefore).toBe(entry(2000).id);
  });

  it("is held, not shown, while paused — and still stamps liveAt", () => {
    const state = reduce([head("events", [entry(1000)]), { type: "pause" }, live("events", entry(2000), 42)]);
    expect(ids(state, "events")).toEqual([entry(1000).id]);
    expect(state.held.map((row) => row.key)).toEqual([`events:${entry(2000).id}`]);
    expect(state).toMatchObject({ paused: true, liveAt: 42 });
  });

  it("holds each key once", () => {
    const state = reduce([{ type: "pause" }, live("events", entry(2000), 1), live("events", entry(2000), 2)]);
    expect(state.held).toHaveLength(1);
  });
});

describe("pause and resume", () => {
  it("resume releases held entries into their streams and clears them", () => {
    const state = reduce([
      head("events", [entry(1000)]),
      { type: "pause" },
      live("events", entry(3000), 1),
      live("home_state", entry(2000), 2),
      { type: "resume" },
    ]);
    expect(state).toMatchObject({ paused: false, held: [] });
    expect(ids(state, "events")).toEqual([entry(3000).id, entry(1000).id]);
    expect(ids(state, "home_state")).toEqual([entry(2000).id]);
  });
});

describe("mergeRows", () => {
  it("merges the targets newest first, ties in catalogue order", () => {
    const state = reduce([
      head("events", [entry(2000), entry(1000)]),
      head("home_state", [entry(2000, 1), entry(1000)]),
    ]);
    const { rows, cursor } = mergeRows(state, STREAMS);
    expect(rows.map((row) => row.key)).toEqual([
      `home_state:${entry(2000, 1).id}`,
      `events:${entry(2000).id}`,
      `events:${entry(1000).id}`,
      `home_state:${entry(1000).id}`,
    ]);
    expect(cursor).toBeNull();
  });

  it("hides rows older than the shallowest stream that can still page, and offers that depth as the cursor", () => {
    const state = reduce([
      head("events", [entry(5000), entry(4000)], entry(4000).id),
      head("home_state", [entry(6000), entry(3000), entry(1000)], entry(1000).id),
    ]);
    const all = mergeRows(state, STREAMS);
    expect(all.rows.map((row) => row.entry.id)).toEqual([6000, 5000, 4000].map((ms) => entry(ms).id));
    expect(all.cursor).toBe(entry(4000).id);

    // Solo on the deeper stream: its own depth is the only horizon.
    const solo = mergeRows(state, ["home_state"]);
    expect(solo.rows.map((row) => row.entry.id)).toEqual([6000, 3000, 1000].map((ms) => entry(ms).id));
    expect(solo.cursor).toBe(entry(1000).id);
  });

  it("a stream with nothing older never sets the horizon", () => {
    const state = reduce([
      head("events", [entry(5000), entry(4000)]),
      head("home_state", [entry(6000), entry(3000), entry(1000)], entry(1000).id),
    ]);
    const { rows, cursor } = mergeRows(state, STREAMS);
    expect(rows.map((row) => row.entry.id)).toEqual([6000, 5000, 4000, 3000, 1000].map((ms) => entry(ms).id));
    expect(cursor).toBe(entry(1000).id);
  });
});

describe("olderTargets", () => {
  it("names the streams sitting at the horizon", () => {
    const state = reduce([
      head("events", [entry(5000), entry(4000)], entry(4000).id),
      head("home_state", [entry(6000), entry(3000), entry(1000)], entry(1000).id),
      head("actions", [entry(4000)], entry(4000).id),
    ]);
    expect(olderTargets(state, STREAMS)).toEqual(["events", "actions"]);
    expect(olderTargets(state, ["home_state"])).toEqual(["home_state"]);
    expect(olderTargets(initialFeed(), STREAMS)).toEqual([]);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/lib/feed.test.ts`
Expected: fails to import `./feed`.

- [ ] **Step 3: Write `web/src/lib/feed.ts`**

```ts
import { compareIds, STREAMS, type StreamName, type StreamRef } from "./streams";
import type { StreamEntry, StreamPage } from "./types";

/**
 * Entries kept per stream. Beyond this the oldest fall off and the cursor
 * points at the last one kept, so `↑ older` brings them straight back —
 * memory stays bounded without anything becoming unreachable.
 */
export const MAX_PER_STREAM = 400;

export interface FeedRow extends StreamRef {
  /** `${stream}:${id}` — unique across streams, stable across renders. */
  key: string;
}

export interface StreamFeed {
  /** Newest first, one entry per id. */
  entries: StreamEntry[];
  /** Where `↑ older` reads from next; null once the stream is known in full. */
  nextBefore: string | null;
  /** A head page has been read, so an empty list means the stream is empty. */
  loaded: boolean;
}

export interface FeedState {
  streams: Record<StreamName, StreamFeed>;
  paused: boolean;
  /** Live rows that arrived while paused, in arrival order, unique by key. */
  held: FeedRow[];
  /** When the feed was last known live: socket up, or a frame arrived. */
  liveAt: number | null;
}

export type FeedEvent =
  | { type: "page"; stream: StreamName; page: StreamPage; mode: "head" | "older" }
  | { type: "live"; stream: StreamName; entry: StreamEntry; at: number }
  | { type: "seen"; at: number }
  | { type: "pause" }
  | { type: "resume" };

export function rowKey(stream: StreamName, id: string): string {
  return `${stream}:${id}`;
}

export function initialFeed(): FeedState {
  const streams = {} as Record<StreamName, StreamFeed>;
  for (const name of STREAMS) streams[name] = { entries: [], nextBefore: null, loaded: false };
  return { streams, paused: false, held: [], liveAt: null };
}

/** Newest first, one entry per id; a later copy of an id wins. */
function union(a: StreamEntry[], b: StreamEntry[]): StreamEntry[] {
  const byId = new Map<string, StreamEntry>();
  for (const e of a) byId.set(e.id, e);
  for (const e of b) byId.set(e.id, e);
  return [...byId.values()].sort((x, y) => compareIds(y.id, x.id));
}

function trim(entries: StreamEntry[], nextBefore: string | null): Pick<StreamFeed, "entries" | "nextBefore"> {
  if (entries.length <= MAX_PER_STREAM) return { entries, nextBefore };
  const kept = entries.slice(0, MAX_PER_STREAM);
  return { entries: kept, nextBefore: kept[MAX_PER_STREAM - 1].id };
}

function withPage(feed: StreamFeed, page: StreamPage, mode: "head" | "older"): StreamFeed {
  const incoming = [...page.entries].sort((x, y) => compareIds(y.id, x.id));
  if (mode === "older") {
    // Growing downward is what the user asked for: never trim it away.
    return { entries: union(feed.entries, incoming), nextBefore: page.next_before, loaded: true };
  }
  if (page.next_before === null) {
    // The whole stream fits in one page: there is nothing older to page to.
    return { entries: union(feed.entries, incoming), nextBefore: null, loaded: true };
  }
  const newest = feed.entries[0];
  const oldestIncoming = incoming[incoming.length - 1];
  const gap =
    newest === undefined ||
    (oldestIncoming !== undefined && compareIds(oldestIncoming.id, newest.id) > 0);
  if (gap) {
    // Everything held is older than this whole page, with an unknown stretch
    // between: keep the page and let its cursor lead back to the rest.
    return { entries: incoming, nextBefore: page.next_before, loaded: true };
  }
  const merged = trim(union(feed.entries, incoming), feed.loaded ? feed.nextBefore : page.next_before);
  return { ...merged, loaded: true };
}

function withLive(feed: StreamFeed, entry: StreamEntry): StreamFeed {
  if (feed.entries.some((e) => e.id === entry.id)) return feed;
  return { ...feed, ...trim(union(feed.entries, [entry]), feed.nextBefore) };
}

export function feedReducer(state: FeedState, event: FeedEvent): FeedState {
  switch (event.type) {
    case "page":
      return {
        ...state,
        streams: { ...state.streams, [event.stream]: withPage(state.streams[event.stream], event.page, event.mode) },
      };
    case "live": {
      const row: FeedRow = { stream: event.stream, entry: event.entry, key: rowKey(event.stream, event.entry.id) };
      if (state.paused) {
        const held = state.held.some((h) => h.key === row.key) ? state.held : [...state.held, row];
        return { ...state, held, liveAt: event.at };
      }
      return {
        ...state,
        liveAt: event.at,
        streams: { ...state.streams, [event.stream]: withLive(state.streams[event.stream], event.entry) },
      };
    }
    case "seen":
      return { ...state, liveAt: event.at };
    case "pause":
      return state.paused ? state : { ...state, paused: true };
    case "resume": {
      const streams = { ...state.streams };
      for (const row of state.held) streams[row.stream] = withLive(streams[row.stream], row.entry);
      return { ...state, streams, held: [], paused: false };
    }
  }
}

function oldestId(feed: StreamFeed): string | null {
  const last = feed.entries[feed.entries.length - 1];
  return last === undefined ? null : last.id;
}

/**
 * How far back every target is known. A stream with a cursor is only known
 * back to its oldest loaded entry; a stream without one is known in full and
 * never limits the view. Null when nothing limits it.
 */
function horizonOf(state: FeedState, streams: readonly StreamName[]): string | null {
  let horizon: string | null = null;
  for (const name of streams) {
    const feed = state.streams[name];
    const oldest = oldestId(feed);
    if (feed.nextBefore === null || oldest === null) continue;
    if (horizon === null || compareIds(oldest, horizon) > 0) horizon = oldest;
  }
  return horizon;
}

/**
 * The merged list: every target's rows from the horizon forward, newest
 * first, same-id ties in catalogue order. `cursor` is the horizon — what the
 * `↑ older` button names, null when there is nothing older to fetch.
 */
export function mergeRows(
  state: FeedState,
  streams: readonly StreamName[],
): { rows: FeedRow[]; cursor: string | null } {
  const horizon = horizonOf(state, streams);
  const rows: FeedRow[] = [];
  for (const stream of streams) {
    for (const entry of state.streams[stream].entries) {
      if (horizon === null || compareIds(entry.id, horizon) >= 0) {
        rows.push({ stream, entry, key: rowKey(stream, entry.id) });
      }
    }
  }
  rows.sort(
    (a, b) => compareIds(b.entry.id, a.entry.id) || STREAMS.indexOf(a.stream) - STREAMS.indexOf(b.stream),
  );
  return { rows, cursor: horizon };
}

/** The targets `↑ older` must read to move the horizon: those sitting on it. */
export function olderTargets(state: FeedState, streams: readonly StreamName[]): StreamName[] {
  const horizon = horizonOf(state, streams);
  if (horizon === null) return [];
  return streams.filter((name) => {
    const feed = state.streams[name];
    const oldest = oldestId(feed);
    return feed.nextBefore !== null && oldest !== null && compareIds(oldest, horizon) >= 0;
  });
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/lib/feed.test.ts`
Expected: 18 passed.

Whole suite: `npx vitest run` → `Test Files  44 passed (44)`, `Tests  671 passed (671)`.

- [ ] **Step 5: Lint and commit**

```bash
cd web && npm run lint
git add src/lib/feed.ts src/lib/feed.test.ts
git commit -m "feat(web): feed reducer — merged pages, live frames, pause, one horizon"
```

## Task 3: Refcounted telemetry subscriptions and a workshop layer level

**Files:**
- Modify: `web/src/lib/telemetry-socket.ts`
- Modify: `web/src/lib/ws.test.ts` (extend `describe("TelemetrySocket")`)
- Modify: `web/src/shell/Layer.tsx`
- Modify: `web/src/shell/Layer.test.tsx` (one test after "stacks gates above layers")

The Door subscribes to `home_action_results` for as long as the app lives; the Workshop subscribes to all eight only while it is up. With a plain set, the Workshop closing would unsubscribe the Door's stream too. So subscriptions become a count per stream: the server hears `subscribe` when a stream goes 0 → 1 and `unsubscribe` when it returns to 0, and a reconnect replays every stream anybody still wants.

- [ ] **Step 1: Write the failing tests**

In `web/src/lib/ws.test.ts`, replace the whole `describe("TelemetrySocket", …)` block with:

```ts
describe("TelemetrySocket", () => {
  /** Every frame the newest fake socket has been asked to send. */
  function sent(): unknown[] {
    return FakeWebSocket.instances.at(-1)!.sent.map((s) => JSON.parse(s));
  }

  it("replays subscriptions on reconnect", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events", "actions"]);
    FakeWebSocket.instances[0].emitClose(1006);
    vi.advanceTimersByTime(600);
    FakeWebSocket.instances[1].open();
    expect(sent()).toContainEqual({ type: "subscribe", streams: ["events", "actions"] });
  });

  it("only tells the server about a stream the first time it is wanted", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events"]);
    sock.subscribe(["events", "actions"]);
    expect(sent()).toEqual([
      { type: "subscribe", streams: ["events"] },
      { type: "subscribe", streams: ["actions"] },
    ]);
  });

  it("only unsubscribes a stream once nobody wants it", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events", "actions"]);
    sock.subscribe(["events"]);
    sock.unsubscribe(["events", "actions"]);
    expect(sent().at(-1)).toEqual({ type: "unsubscribe", streams: ["actions"] });
    sock.unsubscribe(["events"]);
    expect(sent().at(-1)).toEqual({ type: "unsubscribe", streams: ["events"] });
    // Unsubscribing what was never subscribed sends nothing.
    const before = sent().length;
    sock.unsubscribe(["events"]);
    expect(sent()).toHaveLength(before);
  });

  it("replays only what is still wanted", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events", "actions"]);
    sock.unsubscribe(["events"]);
    FakeWebSocket.instances[0].emitClose(1006);
    vi.advanceTimersByTime(600);
    FakeWebSocket.instances[1].open();
    expect(sent()).toEqual([{ type: "subscribe", streams: ["actions"] }]);
  });
});
```

In `web/src/shell/Layer.test.tsx`, after the "stacks gates above layers" test:

```tsx
  it("stacks the workshop under layers", () => {
    render(
      <Layer open label="Workshop" level="workshop">
        <p>workshop</p>
      </Layer>,
    );
    expect(screen.getByRole("dialog").className).toContain("z-10");
  });
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/lib/ws.test.ts src/shell/Layer.test.tsx`
Expected: three TelemetrySocket tests fail (`sock.unsubscribe is not a function`; the "first time" test sees two `events` subscribes); the Layer test fails on `z-10` (TypeScript also rejects `level="workshop"` — vitest still runs it).

- [ ] **Step 3: Rewrite `web/src/lib/telemetry-socket.ts`**

```ts
import type { TelemetryMessage } from "./types";
import { ReconnectingSocket, type SocketStatus } from "./ws";

/**
 * The one telemetry socket, shared by everything that wants stream frames.
 * Subscriptions are counted per stream: the Door wants `home_action_results`
 * for the life of the app, the Workshop wants all eight only while it is up,
 * and neither may cancel the other's. The server hears `subscribe` when a
 * stream is first wanted, `unsubscribe` when the last wanter leaves, and a
 * replay of everything still wanted on each reconnect.
 */
export class TelemetrySocket {
  private socket = new ReconnectingSocket("/ws/telemetry");
  private wanted = new Map<string, number>();
  private listeners = new Set<(msg: TelemetryMessage) => void>();

  onstatus: (s: SocketStatus) => void = () => {};

  constructor() {
    this.socket.onstatus = (s) => this.onstatus(s);
    this.socket.onmessage = (data) => {
      for (const fn of this.listeners) fn(data as TelemetryMessage);
    };
    this.socket.onopen = () => {
      if (this.wanted.size > 0) {
        this.socket.send({ type: "subscribe", streams: [...this.wanted.keys()] });
      }
    };
  }

  connect(): void { this.socket.connect(); }
  close(): void { this.socket.close(); }

  subscribe(streams: string[]): void {
    const fresh: string[] = [];
    for (const s of streams) {
      const count = this.wanted.get(s) ?? 0;
      this.wanted.set(s, count + 1);
      if (count === 0) fresh.push(s);
    }
    if (fresh.length > 0) this.socket.send({ type: "subscribe", streams: fresh });
  }

  unsubscribe(streams: string[]): void {
    const done: string[] = [];
    for (const s of streams) {
      const count = this.wanted.get(s) ?? 0;
      if (count <= 1) {
        if (count === 1) done.push(s);
        this.wanted.delete(s);
      } else {
        this.wanted.set(s, count - 1);
      }
    }
    if (done.length > 0) this.socket.send({ type: "unsubscribe", streams: done });
  }

  listen(fn: (msg: TelemetryMessage) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}
```

- [ ] **Step 4: Add the level to `web/src/shell/Layer.tsx`**

Replace the `level` prop doc + type and the className expression:

```ts
  /**
   * The handoff's stack, workshop < sheet < door < gate: a sheet opened from
   * the Workshop paints over it, and a lapsed session paints over everything,
   * whatever order they were mounted in.
   */
  level?: "workshop" | "layer" | "gate";
```

and above the component:

```ts
const Z_INDEX: Record<NonNullable<LayerProps["level"]>, string> = {
  workshop: "z-10",
  layer: "z-30",
  gate: "z-40",
};
```

and in the JSX:

```tsx
      className={`fixed inset-0 ${Z_INDEX[level]} flex flex-col overflow-hidden outline-none ${riseClass(leaving)} ${className}`}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/lib/ws.test.ts src/shell/Layer.test.tsx`
Expected: green. Whole suite: `npx vitest run` → `Test Files  44 passed (44)`, `Tests  675 passed (675)`.

- [ ] **Step 6: Lint, typecheck and commit**

```bash
cd web && npm run lint && npx tsc -b
git add src/lib/telemetry-socket.ts src/lib/ws.test.ts src/shell/Layer.tsx src/shell/Layer.test.tsx
git commit -m "feat(web): count telemetry subscriptions per stream; workshop layer level"
```

## Task 4: `useActivity` — the bench's one hook

**Files:**
- Create: `web/src/workshop/useActivity.ts`
- Create: `web/src/workshop/useActivity.test.tsx`

Everything the Activity bench renders comes out of this hook: it reads the eight head pages on mount and on every return to the foreground (the socket replays nothing — spec §10), subscribes to all eight streams for as long as it is mounted, folds live frames through the reducer, and exposes solo / expand / pause / older as plain callbacks. No component below it touches fetch or the socket.

- [ ] **Step 1: Write the failing tests**

`web/src/workshop/useActivity.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { STREAMS } from "@/lib/streams";
import type { StreamEntry, StreamPage, TelemetryMessage } from "@/lib/types";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { useActivity } from "./useActivity";

const { telemetries } = vi.hoisted(() => ({ telemetries: [] as unknown[] }));

vi.mock("@/lib/chat-socket", () => {
  class ChatSocket {
    onstatus: (status: string) => void = () => {};
    connect() { this.onstatus("online"); }
    close = vi.fn();
    sendText = vi.fn(() => true);
    sendAudio = vi.fn(() => true);
    listen() { return () => {}; }
  }
  return { ChatSocket };
});

vi.mock("@/lib/telemetry-socket", () => {
  class TelemetrySocket {
    onstatus: (status: string) => void = () => {};
    listeners = new Set<(msg: TelemetryMessage) => void>();
    close = vi.fn();
    subscribe = vi.fn();
    unsubscribe = vi.fn();
    constructor() { telemetries.push(this); }
    connect() { this.onstatus("online"); }
    listen(fn: (msg: TelemetryMessage) => void): () => void {
      this.listeners.add(fn);
      return () => void this.listeners.delete(fn);
    }
    deliver(msg: TelemetryMessage): void {
      for (const fn of [...this.listeners]) fn(msg);
    }
  }
  return { TelemetrySocket };
});

interface FakeTelemetry {
  subscribe: ReturnType<typeof vi.fn>;
  unsubscribe: ReturnType<typeof vi.fn>;
  deliver: (msg: TelemetryMessage) => void;
}
const telemetry = (): FakeTelemetry => telemetries.at(-1) as FakeTelemetry;

const BASE = 1788815640000;
const entry = (offsetMs: number): StreamEntry => ({ id: `${BASE + offsetMs}-0`, event: { n: offsetMs } });
const empty: StreamPage = { entries: [], next_before: null };

/** URL → page (or status) for every stream read. Unknown URLs get an empty page. */
let routes: Record<string, StreamPage | number> = {};
const calls: string[] = [];

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      const route = routes[url] ?? empty;
      if (typeof route === "number") {
        return new Response(JSON.stringify({ detail: "redis gone" }), { status: route });
      }
      return new Response(JSON.stringify(route), { status: 200 });
    }),
  );
}

function Probe() {
  const a = useActivity();
  return (
    <div>
      <ul aria-label="rows">
        {a.rows.map((row) => (
          <li key={row.key}>
            <button type="button" onClick={() => a.toggle(row.key)}>{row.key}</button>
          </li>
        ))}
      </ul>
      <output data-testid="state">
        {JSON.stringify({
          loaded: a.loaded,
          live: a.live,
          paused: a.paused,
          liveAt: a.liveAt,
          heldCount: a.heldCount,
          solo: a.solo,
          expanded: a.expanded,
          cursor: a.cursor,
          fetchingOlder: a.fetchingOlder,
          error: a.error,
          counts: a.counts,
        })}
      </output>
      <button type="button" onClick={a.pause}>pause</button>
      <button type="button" onClick={a.resume}>resume</button>
      <button type="button" onClick={a.loadOlder}>older</button>
      <button type="button" onClick={() => a.setSolo("home_state")}>solo home_state</button>
      <button type="button" onClick={() => a.setSolo(null)}>all</button>
    </div>
  );
}

function state(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId("state").textContent ?? "{}") as Record<string, unknown>;
}

function rowKeys(): string[] {
  return Array.from(screen.getByRole("list", { name: "rows" }).querySelectorAll("li")).map(
    (li) => li.textContent ?? "",
  );
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <Probe />
      </ConnectionProvider>
    </QueryClientProvider>,
  );
}

const headUrl = (name: string) => `/api/admin/streams/${name}?count=50`;

beforeEach(() => {
  routes = {};
  calls.length = 0;
  telemetries.length = 0;
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useActivity", () => {
  it("reads the head of all eight streams and merges them newest first", async () => {
    routes[headUrl("events")] = { entries: [entry(3000), entry(1000)], next_before: null };
    routes[headUrl("home_state")] = { entries: [entry(2000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(STREAMS.map(headUrl).every((url) => calls.includes(url))).toBe(true);
    expect(rowKeys()).toEqual([
      `events:${entry(3000).id}`,
      `home_state:${entry(2000).id}`,
      `events:${entry(1000).id}`,
    ]);
    expect(state().counts).toMatchObject({ events: 2, home_state: 1, actions: 0 });
    expect(state().cursor).toBeNull();
    expect(state().error).toBeNull();
  });

  it("reports the streams it could not read, and still shows the rest", async () => {
    routes[headUrl("events")] = { entries: [entry(1000)], next_before: null };
    routes[headUrl("actions")] = 500;
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(state().error).toBe("1 of 8 streams could not be read · redis gone");
    expect(rowKeys()).toEqual([`events:${entry(1000).id}`]);
  });

  it("re-reads the heads when the app comes back to the foreground", async () => {
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    const before = calls.length;
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(calls.length).toBe(before + STREAMS.length));
  });

  it("subscribes to all eight streams while mounted and lets go on unmount", async () => {
    const view = mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(telemetry().subscribe).toHaveBeenCalledWith([...STREAMS]);
    view.unmount();
    expect(telemetry().unsubscribe).toHaveBeenCalledWith([...STREAMS]);
  });

  it("puts a live entry at the top, marks the feed live, and ignores streams it does not know", async () => {
    routes[headUrl("events")] = { entries: [entry(1000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(state().live).toBe(true);
    act(() => {
      telemetry().deliver({ type: "entry", stream: "events", id: entry(2000).id, event: { n: 2000 } });
      telemetry().deliver({ type: "entry", stream: "mystery", id: entry(3000).id, event: {} });
    });
    expect(rowKeys()).toEqual([`events:${entry(2000).id}`, `events:${entry(1000).id}`]);
    expect(typeof state().liveAt).toBe("number");
  });

  it("holds live entries while paused and releases them on resume", async () => {
    routes[headUrl("events")] = { entries: [entry(1000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "pause" }));
    act(() => {
      telemetry().deliver({ type: "entry", stream: "events", id: entry(2000).id, event: { n: 2000 } });
    });
    expect(state()).toMatchObject({ paused: true, heldCount: 1 });
    expect(rowKeys()).toEqual([`events:${entry(1000).id}`]);
    fireEvent.click(screen.getByRole("button", { name: "resume" }));
    expect(state()).toMatchObject({ paused: false, heldCount: 0 });
    expect(rowKeys()).toEqual([`events:${entry(2000).id}`, `events:${entry(1000).id}`]);
  });

  it("solo narrows the rows and closes whatever was expanded", async () => {
    routes[headUrl("events")] = { entries: [entry(3000)], next_before: null };
    routes[headUrl("home_state")] = { entries: [entry(2000)], next_before: null };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: `events:${entry(3000).id}` }));
    expect(state().expanded).toBe(`events:${entry(3000).id}`);
    fireEvent.click(screen.getByRole("button", { name: `events:${entry(3000).id}` }));
    expect(state().expanded).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: `events:${entry(3000).id}` }));
    fireEvent.click(screen.getByRole("button", { name: "solo home_state" }));
    expect(state()).toMatchObject({ solo: "home_state", expanded: null });
    expect(rowKeys()).toEqual([`home_state:${entry(2000).id}`]);
    fireEvent.click(screen.getByRole("button", { name: "all" }));
    expect(rowKeys()).toHaveLength(2);
  });

  it("loads older pages for the streams at the horizon", async () => {
    routes[headUrl("events")] = { entries: [entry(5000), entry(4000)], next_before: entry(4000).id };
    routes[headUrl("home_state")] = { entries: [entry(6000), entry(1000)], next_before: entry(1000).id };
    routes[`/api/admin/streams/events?count=50&before=${entry(4000).id}`] = {
      entries: [entry(2000)],
      next_before: null,
    };
    mount();
    await waitFor(() => expect(state().loaded).toBe(true));
    expect(state().cursor).toBe(entry(4000).id);
    expect(rowKeys()).toHaveLength(3);

    fireEvent.click(screen.getByRole("button", { name: "older" }));
    expect(state().fetchingOlder).toBe(true);
    await waitFor(() => expect(state().fetchingOlder).toBe(false));
    // Only events sat at the horizon; home_state already reached further back.
    expect(calls.filter((url) => url.includes("before="))).toEqual([
      `/api/admin/streams/events?count=50&before=${entry(4000).id}`,
    ]);
    expect(rowKeys()).toEqual([
      `home_state:${entry(6000).id}`,
      `events:${entry(5000).id}`,
      `events:${entry(4000).id}`,
      `events:${entry(2000).id}`,
      `home_state:${entry(1000).id}`,
    ]);
    expect(state().cursor).toBe(entry(1000).id);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/workshop/useActivity.test.tsx`
Expected: fails to import `./useActivity`.

- [ ] **Step 3: Write `web/src/workshop/useActivity.ts`**

```ts
import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { failureText } from "@/lib/auth";
import { feedReducer, initialFeed, mergeRows, olderTargets, type FeedRow } from "@/lib/feed";
import { onVisible } from "@/lib/lifecycle";
import { fetchStreamPage, isStreamName, STREAMS, type StreamName } from "@/lib/streams";
import type { StreamPage } from "@/lib/types";
import { useConnection } from "@/shell/ConnectionProvider";

export interface Activity {
  /** What the list shows: the solo stream or all eight, newest first, from the horizon forward. */
  rows: FeedRow[];
  /** Entries held per stream — the chips' numbers. */
  counts: Record<StreamName, number>;
  /** The telemetry socket is up. When false the stale banner shows. */
  live: boolean;
  paused: boolean;
  /** When the feed was last known live, for the stale banner; null if never. */
  liveAt: number | null;
  heldCount: number;
  solo: StreamName | null;
  setSolo: (name: StreamName | null) => void;
  /** The one open row's key. */
  expanded: string | null;
  toggle: (key: string) => void;
  pause: () => void;
  resume: () => void;
  /** What `↑ older` reads before; null when there is nothing older to fetch. */
  cursor: string | null;
  fetchingOlder: boolean;
  loadOlder: () => void;
  /** The first head read has finished (well or badly). */
  loaded: boolean;
  error: string | null;
}

type Settled = PromiseSettledResult<StreamPage>[];

function readFailure(results: Settled, of: number): string | null {
  const failed = results.filter((r) => r.status === "rejected");
  if (failed.length === 0) return null;
  const reason = (failed[0] as PromiseRejectedResult).reason as unknown;
  return `${failed.length} of ${of} streams could not be read · ${failureText(reason)}`;
}

export function useActivity(): Activity {
  const { telemetry, telemetryStatus } = useConnection();
  const [feed, dispatch] = useReducer(feedReducer, undefined, initialFeed);
  const [solo, setSoloState] = useState<StreamName | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [fetchingOlder, setFetchingOlder] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Head pages on mount and on every return to the foreground: the socket
  // replays nothing, so whatever happened while the app was suspended is only
  // on the server (spec §10).
  useEffect(() => {
    const readHeads = async () => {
      const results = await Promise.allSettled(STREAMS.map((name) => fetchStreamPage(name)));
      results.forEach((result, i) => {
        if (result.status === "fulfilled") {
          dispatch({ type: "page", stream: STREAMS[i], page: result.value, mode: "head" });
        }
      });
      setError(readFailure(results, STREAMS.length));
      setLoaded(true);
    };
    void readHeads();
    return onVisible(() => void readHeads());
  }, []);

  // All eight streams for as long as the bench is mounted. The socket counts
  // wanters per stream, so this never cancels the Door's own subscription.
  useEffect(() => {
    telemetry.subscribe([...STREAMS]);
    const stop = telemetry.listen((msg) => {
      if (msg.type !== "entry" || !isStreamName(msg.stream)) return;
      dispatch({ type: "live", stream: msg.stream, entry: { id: msg.id, event: msg.event }, at: Date.now() });
    });
    return () => {
      stop();
      telemetry.unsubscribe([...STREAMS]);
    };
  }, [telemetry]);

  // The feed is live while the socket is up; stamp both ends of that, so the
  // stale banner can say when it stopped.
  useEffect(() => {
    if (telemetryStatus !== "online") return;
    dispatch({ type: "seen", at: Date.now() });
    return () => dispatch({ type: "seen", at: Date.now() });
  }, [telemetryStatus]);

  const targets = useMemo<readonly StreamName[]>(() => (solo ? [solo] : STREAMS), [solo]);
  const { rows, cursor } = useMemo(() => mergeRows(feed, targets), [feed, targets]);

  const counts = useMemo(() => {
    const result = {} as Record<StreamName, number>;
    for (const name of STREAMS) result[name] = feed.streams[name].entries.length;
    return result;
  }, [feed]);

  const setSolo = useCallback((name: StreamName | null) => {
    setSoloState(name);
    setExpanded(null);
  }, []);

  const toggle = useCallback((key: string) => {
    setExpanded((current) => (current === key ? null : key));
  }, []);

  const pause = useCallback(() => dispatch({ type: "pause" }), []);
  const resume = useCallback(() => dispatch({ type: "resume" }), []);

  const loadOlder = useCallback(() => {
    if (fetchingOlder) return;
    const wanted = olderTargets(feed, targets);
    if (wanted.length === 0) return;
    setFetchingOlder(true);
    void (async () => {
      const results = await Promise.allSettled(
        wanted.map((name) => fetchStreamPage(name, feed.streams[name].nextBefore)),
      );
      results.forEach((result, i) => {
        if (result.status === "fulfilled") {
          dispatch({ type: "page", stream: wanted[i], page: result.value, mode: "older" });
        }
      });
      setError(readFailure(results, wanted.length));
      setFetchingOlder(false);
    })();
  }, [feed, targets, fetchingOlder]);

  return {
    rows,
    counts,
    live: telemetryStatus === "online",
    paused: feed.paused,
    liveAt: feed.liveAt,
    heldCount: feed.held.length,
    solo,
    setSolo,
    expanded,
    toggle,
    pause,
    resume,
    cursor,
    fetchingOlder,
    loadOlder,
    loaded,
    error,
  };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/workshop/useActivity.test.tsx`
Expected: 8 passed.

Whole suite: `npx vitest run` → `Test Files  45 passed (45)`, `Tests  683 passed (683)`.

A reducer `dispatch` called synchronously inside an effect is what `DoorProvider.tsx` already does (`if (data) dispatch({ type: "loaded", … })`) and it passes `react-hooks` 7's `set-state-in-effect` rule, which only tracks `useState` setters. Do not add an eslint-disable.

- [ ] **Step 5: Lint, typecheck and commit**

```bash
cd web && npm run lint && npx tsc -b
git add src/workshop/useActivity.ts src/workshop/useActivity.test.tsx
git commit -m "feat(web): useActivity — eight head pages, live frames, solo, pause, older"
```

## Task 5: `EventRow` and `StreamChips`

**Files:**
- Create: `web/src/workshop/EventRow.tsx`
- Create: `web/src/workshop/EventRow.test.tsx`
- Create: `web/src/workshop/StreamChips.tsx`
- Create: `web/src/workshop/StreamChips.test.tsx`

The two leaf components of the Activity bench, straight from the handoff's Activity markup (`Alfred.dc.html` lines 206–233). A row is one `<li>` with a full-width button — monogram tile in the stream's ring, one line of text, one mono meta line stamped to the second — and, when open, the raw event as pretty JSON plus two pills. The chips are eight equal buttons with the monogram over the count; soloing one fills it with its ring and dims the other seven.

- [ ] **Step 1: Write the failing tests**

`web/src/workshop/EventRow.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FeedRow } from "@/lib/feed";
import { hhmmss } from "@/lib/format";
import { reflexObservationsPage } from "@/test/fixtures";
import { EventRow } from "./EventRow";

const observation = reflexObservationsPage.entries[2]; // obs-1: acted on media_player.tv
const row: FeedRow = {
  stream: "reflex_observations",
  entry: observation,
  key: `reflex_observations:${observation.id}`,
};

describe("EventRow", () => {
  it("shows the monogram in the stream's ring, the summary, and the meta stamped to the second", () => {
    render(
      <ul>
        <EventRow row={row} expanded={false} onToggle={() => {}} onSolo={() => {}} />
      </ul>,
    );
    const monogram = screen.getByTestId("monogram");
    expect(monogram).toHaveTextContent("RX");
    expect(monogram.style.background).toBe("oklch(0.62 0.11 210)");
    expect(screen.getByText("observed media_player.tv · acted")).toBeInTheDocument();
    expect(
      screen.getByText(
        `${hhmmss(1788800280000)} · home.light_set · request 4b1d · decision "movie started, evening, user home"`,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { expanded: false })).toBeInTheDocument();
    expect(screen.queryByText("Only RX")).toBeNull();
  });

  it("opens to the raw event and the pills, offering why only when it is given", () => {
    const onWhy = vi.fn();
    const { rerender } = render(
      <ul>
        <EventRow row={row} expanded onToggle={() => {}} onSolo={() => {}} />
      </ul>,
    );
    expect(screen.getByRole("button", { expanded: true })).toBeInTheDocument();
    expect(screen.getByText(/"tool_name": "home.light_set"/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Only RX" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Why · causal thread" })).toBeNull();

    rerender(
      <ul>
        <EventRow row={row} expanded onToggle={() => {}} onSolo={() => {}} onWhy={onWhy} />
      </ul>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Why · causal thread" }));
    expect(onWhy).toHaveBeenCalledTimes(1);
  });

  it("toggles on the row, solos on the pill", () => {
    const onToggle = vi.fn();
    const onSolo = vi.fn();
    render(
      <ul>
        <EventRow row={row} expanded onToggle={onToggle} onSolo={onSolo} />
      </ul>,
    );
    fireEvent.click(screen.getByText("observed media_player.tv · acted"));
    expect(onToggle).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Only RX" }));
    expect(onSolo).toHaveBeenCalledTimes(1);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
```

`web/src/workshop/StreamChips.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { STREAMS, type StreamName } from "@/lib/streams";
import { StreamChips } from "./StreamChips";

const counts = Object.fromEntries(STREAMS.map((name, i) => [name, i * 3])) as Record<StreamName, number>;

describe("StreamChips", () => {
  it("shows eight chips, monogram over count, none pressed", () => {
    render(<StreamChips counts={counts} solo={null} onSolo={() => {}} />);
    const chips = screen.getAllByRole("button");
    expect(chips.map((chip) => chip.textContent)).toEqual([
      "UR0", "AL3", "EV6", "AC9", "RX12", "NT15", "HS18", "HR21",
    ]);
    for (const chip of chips) {
      expect(chip).toHaveAttribute("aria-pressed", "false");
      expect(chip.style.opacity).toBe("1");
      expect(chip.style.background).toBe("transparent");
    }
    expect(chips[2].style.borderColor).toBe("oklch(0.62 0.11 120)");
    expect(chips[2].style.color).toBe("oklch(0.62 0.11 120)");
  });

  it("fills the solo chip with its ring and dims the rest", () => {
    render(<StreamChips counts={counts} solo="events" onSolo={() => {}} />);
    const chips = screen.getAllByRole("button");
    expect(chips[2]).toHaveAttribute("aria-pressed", "true");
    expect(chips[2].style.background).toBe("oklch(0.62 0.11 120)");
    expect(chips[2].style.color).toBe("rgb(255, 255, 255)");
    expect(chips[2].style.opacity).toBe("1");
    expect(chips[0]).toHaveAttribute("aria-pressed", "false");
    expect(chips[0].style.opacity).toBe("0.35");
    expect(chips[0].style.background).toBe("transparent");
  });

  it("tapping a chip solos it; tapping the solo chip clears", () => {
    const onSolo = vi.fn();
    const { rerender } = render(<StreamChips counts={counts} solo={null} onSolo={onSolo} />);
    fireEvent.click(screen.getByRole("button", { name: "HS 18" }));
    expect(onSolo).toHaveBeenLastCalledWith("home_state");
    rerender(<StreamChips counts={counts} solo="home_state" onSolo={onSolo} />);
    fireEvent.click(screen.getByRole("button", { name: "HS 18" }));
    expect(onSolo).toHaveBeenLastCalledWith(null);
  });
});
```

(`getByRole("button", { name: "HS 18" })` — the accessible name joins the two spans with a space; `textContent` does not.)

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/workshop/EventRow.test.tsx src/workshop/StreamChips.test.tsx`
Expected: both fail to import.

- [ ] **Step 3: Write `web/src/workshop/EventRow.tsx`**

```tsx
import type { FeedRow } from "@/lib/feed";
import { hhmmss } from "@/lib/format";
import { idMs, ring, STREAM_INFO, summarise } from "@/lib/streams";

export interface EventRowProps {
  row: FeedRow;
  expanded: boolean;
  onToggle: () => void;
  /** `Only {mono}` — narrow the list to this row's stream. */
  onSolo: () => void;
  /** `Why · causal thread` — offered on reflex observations only. */
  onWhy?: () => void;
}

const PILL =
  "h-9 rounded-[18px] border border-line bg-transparent px-3.5 text-[13px] font-medium";

/**
 * One event on one stream (handoff, Activity list). The monogram tile carries
 * the stream's ring; the stamp is the Redis id's clock, to the second, because
 * the bench is for telling two events a hundred milliseconds apart in order.
 * Open, the row shows the whole event as the server holds it — no field is
 * hidden or renamed (spec §5.2).
 */
export function EventRow({ row, expanded, onToggle, onSolo, onWhy }: EventRowProps) {
  const { mono, hue } = STREAM_INFO[row.stream];
  const { text, meta } = summarise(row.stream, row.entry.event);

  return (
    <li style={{ borderTop: "1px solid var(--line)" }}>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={onToggle}
        className="grid min-h-11 w-full grid-cols-[22px_1fr] gap-2.5 py-[9px] text-left"
        style={{ color: "var(--fg)" }}
      >
        <span
          data-testid="monogram"
          className="t-monogram mt-px h-[22px] w-[22px] rounded-md text-center"
          style={{ background: ring(hue), color: "#fff" }}
        >
          {mono}
        </span>
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate text-[14px] leading-[1.35]">{text}</span>
          <span className="t-meta truncate">{`${hhmmss(idMs(row.entry.id))} · ${meta}`}</span>
        </span>
      </button>
      {expanded && (
        <div className="mb-3 ml-8 flex flex-col gap-2">
          <pre
            className="m-0 rounded-lg px-3 py-2.5 font-mono text-[11px] leading-[1.55] whitespace-pre-wrap break-all"
            style={{ background: "var(--surface)", color: "var(--fg2)" }}
          >
            {JSON.stringify(row.entry.event, null, 2)}
          </pre>
          <div className="flex gap-2">
            {onWhy && (
              <button type="button" onClick={onWhy} className={PILL} style={{ color: "var(--accent)" }}>
                Why · causal thread
              </button>
            )}
            <button type="button" onClick={onSolo} className={PILL} style={{ color: "var(--fg)" }}>
              Only {mono}
            </button>
          </div>
        </div>
      )}
    </li>
  );
}
```

- [ ] **Step 4: Write `web/src/workshop/StreamChips.tsx`**

```tsx
import { ring, STREAM_INFO, STREAMS, type StreamName } from "@/lib/streams";

export interface StreamChipsProps {
  counts: Record<StreamName, number>;
  solo: StreamName | null;
  onSolo: (name: StreamName | null) => void;
}

/**
 * Eight equal chips, monogram over count (handoff, Activity footer). Tapping
 * one solos its stream — filled with its ring, the other seven at 35 % —
 * and tapping it again clears. The count is what the bench holds, not what
 * the server has.
 */
export function StreamChips({ counts, solo, onSolo }: StreamChipsProps) {
  return (
    <div className="flex justify-between gap-1.5">
      {STREAMS.map((name) => {
        const { mono, hue } = STREAM_INFO[name];
        const color = ring(hue);
        const on = solo === name;
        return (
          <button
            key={name}
            type="button"
            aria-pressed={on}
            onClick={() => onSolo(on ? null : name)}
            className="flex h-11 flex-1 flex-col items-center justify-center gap-0.5 rounded-lg border-[1.5px] p-0 font-mono text-[10px] font-medium"
            style={{
              borderColor: color,
              background: on ? color : "transparent",
              color: on ? "#fff" : color,
              opacity: solo === null || on ? 1 : 0.35,
            }}
          >
            <span>{mono}</span>
            <span className="font-normal opacity-80">{counts[name]}</span>
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/workshop/EventRow.test.tsx src/workshop/StreamChips.test.tsx`
Expected: 3 + 3 passed. Whole suite: `Test Files  47 passed (47)`, `Tests  689 passed (689)`.

- [ ] **Step 6: Lint and commit**

```bash
cd web && npm run lint
git add src/workshop/EventRow.tsx src/workshop/EventRow.test.tsx src/workshop/StreamChips.tsx src/workshop/StreamChips.test.tsx
git commit -m "feat(web): Activity event row and stream chips"
```

## Task 6: `ActivityBench`

**Files:**
- Create: `web/src/workshop/ActivityBench.tsx`
- Create: `web/src/workshop/ActivityBench.test.tsx`

The bench itself, given an `Activity` (Task 4) and an `onWhy`. It owns nothing: stale banner, error line, the list (older button on top, rows, empty state), and the footer (chips, Pause/Resume, All streams). Tests hand it a plain `Activity` object, so the hook and the socket never appear here.

- [ ] **Step 1: Write the failing tests**

`web/src/workshop/ActivityBench.test.tsx`:

```tsx
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FeedRow } from "@/lib/feed";
import { hhmm } from "@/lib/format";
import { STREAMS, type StreamName } from "@/lib/streams";
import { reflexObservationsPage, userRequestsPage } from "@/test/fixtures";
import { ActivityBench } from "./ActivityBench";
import type { Activity } from "./useActivity";

const reflex: FeedRow = {
  stream: "reflex_observations",
  entry: reflexObservationsPage.entries[2],
  key: `reflex_observations:${reflexObservationsPage.entries[2].id}`,
};
const request: FeedRow = {
  stream: "user_requests",
  entry: userRequestsPage.entries[0],
  key: `user_requests:${userRequestsPage.entries[0].id}`,
};

function activity(overrides: Partial<Activity> = {}): Activity {
  return {
    rows: [reflex, request],
    counts: Object.fromEntries(STREAMS.map((name) => [name, 0])) as Record<StreamName, number>,
    live: true,
    paused: false,
    liveAt: 1,
    heldCount: 0,
    solo: null,
    setSolo: vi.fn(),
    expanded: null,
    toggle: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    cursor: null,
    fetchingOlder: false,
    loadOlder: vi.fn(),
    loaded: true,
    error: null,
    ...overrides,
  };
}

describe("ActivityBench", () => {
  it("lists the rows with the older button on top when there is something older", () => {
    const a = activity({ cursor: "1788800280000-0" });
    render(<ActivityBench activity={a} onWhy={() => {}} />);
    const list = screen.getByRole("list");
    const items = within(list).getAllByRole("listitem");
    const older = within(items[0]).getByRole("button", { name: "↑ older · before cursor 1788800280000-0" });
    expect(older).not.toBeDisabled();
    fireEvent.click(older);
    expect(a.loadOlder).toHaveBeenCalledTimes(1);
    expect(within(items[1]).getByText("observed media_player.tv · acted")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("has no older button when nothing older exists", () => {
    render(<ActivityBench activity={activity()} onWhy={() => {}} />);
    expect(screen.queryByRole("button", { name: /older/ })).toBeNull();
  });

  it("says fetching, and waits, while an older page is on its way", () => {
    render(
      <ActivityBench activity={activity({ cursor: "1788800280000-0", fetchingOlder: true })} onWhy={() => {}} />,
    );
    expect(
      screen.getByRole("button", { name: "↑ older · fetching before cursor 1788800280000-0" }),
    ).toBeDisabled();
  });

  it("says the feed stopped, and when, while the socket is down", () => {
    const at = new Date(2026, 8, 7, 21, 14, 0).getTime();
    const { rerender } = render(<ActivityBench activity={activity({ live: false, liveAt: at })} onWhy={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent(`Feed stopped at ${hhmm(at)}. Nothing below is live.`);
    rerender(<ActivityBench activity={activity({ live: false, liveAt: null })} onWhy={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Feed has not been live yet. Nothing below is live.");
  });

  it("shows a read failure as a status line under the banner", () => {
    render(
      <ActivityBench activity={activity({ error: "2 of 8 streams could not be read · redis gone" })} onWhy={() => {}} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("2 of 8 streams could not be read · redis gone");
  });

  it("says what an empty list means: all streams, one stream, and before anything loaded", () => {
    const { rerender } = render(<ActivityBench activity={activity({ rows: [] })} onWhy={() => {}} />);
    expect(screen.getByText("Nothing on any stream yet.")).toBeInTheDocument();
    expect(screen.getByText("8 streams · 0 entries")).toBeInTheDocument();

    rerender(<ActivityBench activity={activity({ rows: [], loaded: false })} onWhy={() => {}} />);
    expect(screen.getByText("8 streams · nothing loaded yet")).toBeInTheDocument();

    rerender(<ActivityBench activity={activity({ rows: [], solo: "user_requests" })} onWhy={() => {}} />);
    expect(screen.getByText("Nothing on this stream yet.")).toBeInTheDocument();
    expect(screen.getByText("UR · 0 entries · nothing has been written")).toBeInTheDocument();

    rerender(<ActivityBench activity={activity({ rows: [], solo: "user_requests", loaded: false })} onWhy={() => {}} />);
    expect(screen.getByText("UR · nothing loaded yet")).toBeInTheDocument();
  });

  it("opens a row on tap, solos from its pill, and asks why only on a reflex row", () => {
    const onWhy = vi.fn();
    const a = activity({ expanded: reflex.key });
    const { rerender } = render(<ActivityBench activity={a} onWhy={onWhy} />);
    fireEvent.click(screen.getByText("Is the back door locked?"));
    expect(a.toggle).toHaveBeenCalledWith(request.key);

    fireEvent.click(screen.getByRole("button", { name: "Why · causal thread" }));
    expect(onWhy).toHaveBeenCalledWith({ stream: "reflex_observations", entry: reflex.entry });
    fireEvent.click(screen.getByRole("button", { name: "Only RX" }));
    expect(a.setSolo).toHaveBeenCalledWith("reflex_observations");

    rerender(<ActivityBench activity={activity({ expanded: request.key })} onWhy={onWhy} />);
    expect(screen.getByRole("button", { name: "Only UR" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Why · causal thread" })).toBeNull();
  });

  it("pauses and resumes the feed from one button that says how many wait", () => {
    const a = activity();
    const { rerender } = render(<ActivityBench activity={a} onWhy={() => {}} />);
    const pause = screen.getByRole("button", { name: "Pause feed" });
    expect(pause.style.background).toBe("var(--ink)");
    fireEvent.click(pause);
    expect(a.pause).toHaveBeenCalledTimes(1);

    const b = activity({ paused: true, heldCount: 0 });
    rerender(<ActivityBench activity={b} onWhy={() => {}} />);
    const resume = screen.getByRole("button", { name: "Resume" });
    expect(resume.style.background).toBe("var(--accent)");
    fireEvent.click(resume);
    expect(b.resume).toHaveBeenCalledTimes(1);

    rerender(<ActivityBench activity={activity({ paused: true, heldCount: 3 })} onWhy={() => {}} />);
    expect(screen.getByRole("button", { name: "Resume · 3 new" })).toBeInTheDocument();
  });

  it("All streams clears the solo and is dimmed when there is none", () => {
    const a = activity({ solo: "events" });
    const { rerender } = render(<ActivityBench activity={a} onWhy={() => {}} />);
    const all = screen.getByRole("button", { name: "All streams" });
    expect(all.style.opacity).toBe("1");
    fireEvent.click(all);
    expect(a.setSolo).toHaveBeenCalledWith(null);

    rerender(<ActivityBench activity={activity()} onWhy={() => {}} />);
    const dimmed = screen.getByRole("button", { name: "All streams" });
    expect(dimmed.style.opacity).toBe("0.4");
    expect(dimmed).toBeDisabled();
  });
});
```

(`userRequestsPage.entries[0].event.content` is `"Is the back door locked?"` in `web/src/test/fixtures.ts`.)

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/workshop/ActivityBench.test.tsx`
Expected: fails to import `./ActivityBench`.

- [ ] **Step 3: Write `web/src/workshop/ActivityBench.tsx`**

```tsx
import { hhmm } from "@/lib/format";
import { STREAM_INFO, STREAMS, type StreamRef } from "@/lib/streams";
import { EventRow } from "./EventRow";
import { StreamChips } from "./StreamChips";
import type { Activity } from "./useActivity";

export interface ActivityBenchProps {
  activity: Activity;
  onWhy: (ref: StreamRef) => void;
}

function staleText(liveAt: number | null): string {
  return liveAt === null
    ? "Feed has not been live yet. Nothing below is live."
    : `Feed stopped at ${hhmm(liveAt)}. Nothing below is live.`;
}

function emptyNote({ solo, loaded }: Activity): string {
  if (solo) {
    const { mono } = STREAM_INFO[solo];
    return loaded ? `${mono} · 0 entries · nothing has been written` : `${mono} · nothing loaded yet`;
  }
  return loaded ? `${STREAMS.length} streams · 0 entries` : `${STREAMS.length} streams · nothing loaded yet`;
}

/**
 * The Activity bench (handoff, Workshop → Activity; spec §8 phase 2). A
 * merged column of the eight streams, newest first; the older button above
 * it names the cursor it will read before; the footer solos, pauses and
 * clears. The banner is the one place the bench speaks about itself: while
 * the socket is down every row is last-known, and it says so (spec §5.2).
 */
export function ActivityBench({ activity, onWhy }: ActivityBenchProps) {
  const a = activity;
  const pauseLabel = a.paused ? (a.heldCount > 0 ? `Resume · ${a.heldCount} new` : "Resume") : "Pause feed";

  return (
    <>
      {!a.live && (
        <div
          role="status"
          className="mx-4 mt-2.5 flex items-center gap-2 rounded-[10px] px-3 py-2 text-[13px] leading-[1.4]"
          style={{ background: "var(--surface)" }}
        >
          <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full" style={{ background: "var(--muted)" }} />
          {staleText(a.liveAt)}
        </div>
      )}
      {a.error && (
        <p role="status" className="t-meta mx-4 mt-2.5">
          {a.error}
        </p>
      )}

      <ul role="list" className="m-0 flex flex-1 list-none flex-col overflow-y-auto px-4 pt-2">
        {a.cursor && (
          <li>
            <button
              type="button"
              disabled={a.fetchingOlder}
              onClick={a.loadOlder}
              className="h-11 w-full font-mono text-[11px]"
              style={{ color: "var(--muted)" }}
            >
              {a.fetchingOlder ? `↑ older · fetching before cursor ${a.cursor}` : `↑ older · before cursor ${a.cursor}`}
            </button>
          </li>
        )}
        {a.rows.map((row) => (
          <EventRow
            key={row.key}
            row={row}
            expanded={a.expanded === row.key}
            onToggle={() => a.toggle(row.key)}
            onSolo={() => a.setSolo(row.stream)}
            onWhy={
              row.stream === "reflex_observations"
                ? () => onWhy({ stream: row.stream, entry: row.entry })
                : undefined
            }
          />
        ))}
        {a.rows.length === 0 && (
          <li className="flex flex-col items-center gap-1.5 py-10 text-center">
            <span className="text-[15px]">{a.solo ? "Nothing on this stream yet." : "Nothing on any stream yet."}</span>
            <span className="t-meta">{emptyNote(a)}</span>
          </li>
        )}
        <li aria-hidden="true" className="h-3 shrink-0" />
      </ul>

      <div className="flex flex-col gap-2.5 px-4 pt-2.5" style={{ borderTop: "1px solid var(--line)" }}>
        <StreamChips counts={a.counts} solo={a.solo} onSolo={a.setSolo} />
        <div
          className="flex items-center gap-2.5"
          style={{ paddingBottom: "calc(12px + env(safe-area-inset-bottom, 0px))" }}
        >
          <button
            type="button"
            onClick={a.paused ? a.resume : a.pause}
            className="h-[50px] flex-1 rounded-[25px] border-0 text-[15px] font-medium"
            style={{
              background: a.paused ? "var(--accent)" : "var(--ink)",
              color: a.paused ? "var(--ink)" : "var(--paper)",
            }}
          >
            {pauseLabel}
          </button>
          <button
            type="button"
            disabled={a.solo === null}
            onClick={() => a.setSolo(null)}
            className="h-[50px] rounded-[25px] border border-line bg-transparent px-[18px] text-[14px] font-medium"
            style={{ color: "var(--fg)", opacity: a.solo === null ? 0.4 : 1 }}
          >
            All streams
          </button>
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/workshop/ActivityBench.test.tsx`
Expected: 9 passed. Whole suite: `Test Files  48 passed (48)`, `Tests  698 passed (698)`.

- [ ] **Step 5: Lint and commit**

```bash
cd web && npm run lint
git add src/workshop/ActivityBench.tsx src/workshop/ActivityBench.test.tsx
git commit -m "feat(web): Activity bench — banner, list with older button, chips, pause"
```

## Task 7: `BenchSwitcher` and the `Workshop` layer

**Files:**
- Create: `web/src/workshop/BenchSwitcher.tsx`
- Create: `web/src/workshop/BenchSwitcher.test.tsx`
- Create: `web/src/workshop/Workshop.tsx`
- Create: `web/src/workshop/Workshop.test.tsx`

The Workshop is a `Layer` at the `workshop` level (under sheets, so the Why sheet paints over it) with the handoff's header — `Room` back button, status line, four-bench switcher — and the Activity bench below. The three other benches are phase 3 and say so. Everything stateful lives in an inner `WorkshopPanel`, so `useActivity` mounts (subscribes, reads eight pages) only while the layer is actually up and unmounts (unsubscribes) when it has finished leaving.

- [ ] **Step 1: Write the failing tests**

`web/src/workshop/BenchSwitcher.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BenchSwitcher } from "./BenchSwitcher";

describe("BenchSwitcher", () => {
  it("is a tablist of the four benches with the current one selected", () => {
    render(<BenchSwitcher bench="activity" onChange={() => {}} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Activity", "Memory", "Triggers", "System"]);
    expect(screen.getByRole("tablist", { name: "Bench" })).toBeInTheDocument();
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[1]).toHaveAttribute("aria-selected", "false");
    expect(tabs[0].style.background).toBe("var(--field)");
    expect(tabs[1].style.background).toBe("transparent");
  });

  it("reports the bench that was tapped", () => {
    const onChange = vi.fn();
    render(<BenchSwitcher bench="activity" onChange={onChange} />);
    fireEvent.click(screen.getByRole("tab", { name: "Triggers" }));
    expect(onChange).toHaveBeenCalledWith("triggers");
  });
});
```

`web/src/workshop/Workshop.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StreamPage, TelemetryMessage } from "@/lib/types";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { overviewFixture, reflexObservationsPage } from "@/test/fixtures";
import { Workshop } from "./Workshop";

const sockets = vi.hoisted(() => ({ telemetryUp: true }));

vi.mock("@/lib/chat-socket", () => {
  class ChatSocket {
    onstatus: (status: string) => void = () => {};
    connect() { this.onstatus("online"); }
    close = vi.fn();
    sendText = vi.fn(() => true);
    sendAudio = vi.fn(() => true);
    listen() { return () => {}; }
  }
  return { ChatSocket };
});

vi.mock("@/lib/telemetry-socket", () => {
  class TelemetrySocket {
    onstatus: (status: string) => void = () => {};
    close = vi.fn();
    subscribe = vi.fn();
    unsubscribe = vi.fn();
    connect() { this.onstatus(sockets.telemetryUp ? "online" : "offline"); }
    listen(_fn: (msg: TelemetryMessage) => void): () => void { return () => {}; }
  }
  return { TelemetrySocket };
});

const empty: StreamPage = { entries: [], next_before: null };

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/admin/overview") return new Response(JSON.stringify(overviewFixture), { status: 200 });
      if (url === "/api/admin/streams/reflex_observations?count=50") {
        return new Response(JSON.stringify(reflexObservationsPage), { status: 200 });
      }
      return new Response(JSON.stringify(empty), { status: 200 });
    }),
  );
}

function mount(open: boolean, onClose = vi.fn(), onWhy = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (isOpen: boolean) => (
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <Workshop open={isOpen} onClose={onClose} onWhy={onWhy} />
      </ConnectionProvider>
    </QueryClientProvider>
  );
  const view = render(tree(open));
  return { ...view, onClose, onWhy, reopen: (isOpen: boolean) => view.rerender(tree(isOpen)) };
}

beforeEach(() => {
  sockets.telemetryUp = true;
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(2026, 8, 7, 21, 14, 0));
  stubFetch();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Workshop", () => {
  it("renders nothing while closed", () => {
    mount(false);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("rises under the sheets as the Workshop, and Room closes it", () => {
    const { onClose } = mount(true);
    const dialog = screen.getByRole("dialog", { name: "Workshop" });
    expect(dialog.className).toContain("z-10");
    fireEvent.click(screen.getByRole("button", { name: "Room" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("opens on Activity; the other benches say they are not built", () => {
    mount(true);
    expect(screen.getByRole("tab", { name: "Activity" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Pause feed" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    expect(screen.getByText("not built yet · phase 3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause feed" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByRole("button", { name: "Pause feed" })).toBeInTheDocument();
  });

  it("says live with the overview's rate", async () => {
    mount(true);
    await waitFor(() => expect(screen.getByTestId("workshop-status")).toHaveTextContent("live · 2.1 ev/s"));
  });

  it("says paused with the held count, and last true when the socket is down", async () => {
    const { unmount } = mount(true);
    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    expect(screen.getByTestId("workshop-status")).toHaveTextContent("paused · 0 new");
    unmount();

    sockets.telemetryUp = false;
    mount(true);
    expect(screen.getByTestId("workshop-status")).toHaveTextContent("last true 21:14 · not live");
    expect(screen.getByRole("status")).toHaveTextContent("Feed has not been live yet. Nothing below is live.");
    // Pause is honoured underneath, but the status line still says not live.
    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    expect(screen.getByTestId("workshop-status")).toHaveTextContent("last true 21:14 · not live");
  });

  it("reads the streams once open and hands a reflex row's why up", async () => {
    const { onWhy } = mount(true);
    await screen.findByText("observed media_player.tv · acted");
    fireEvent.click(screen.getByText("observed media_player.tv · acted"));
    fireEvent.click(screen.getByRole("button", { name: "Why · causal thread" }));
    expect(onWhy).toHaveBeenCalledWith({
      stream: "reflex_observations",
      entry: reflexObservationsPage.entries[2],
    });
  });

  it("stays for its leave, then unmounts", () => {
    const { reopen } = mount(true);
    reopen(false);
    expect(screen.getByRole("dialog").className).toContain("rise-out");
    act(() => vi.advanceTimersByTime(400));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
```

(`BenchSwitcher.test.tsx` has two tests, `Workshop.test.tsx` seven — nine new, so the total after this task is 707. The "stays for its leave" case overlaps `Layer.test.tsx`, but it is the one place that proves the panel — and with it `useActivity`'s subscription — lives through the leave rather than being torn down at `open=false`.)

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/workshop/BenchSwitcher.test.tsx src/workshop/Workshop.test.tsx`
Expected: both fail to import.

- [ ] **Step 3: Write `web/src/workshop/BenchSwitcher.tsx`**

```tsx
export type Bench = "activity" | "memory" | "triggers" | "system";

const BENCHES: { id: Bench; label: string }[] = [
  { id: "activity", label: "Activity" },
  { id: "memory", label: "Memory" },
  { id: "triggers", label: "Triggers" },
  { id: "system", label: "System" },
];

export interface BenchSwitcherProps {
  bench: Bench;
  onChange: (bench: Bench) => void;
}

/** The handoff's four-up segmented control: `field` under the chosen bench, `muted` on the rest. */
export function BenchSwitcher({ bench, onChange }: BenchSwitcherProps) {
  return (
    <div
      role="tablist"
      aria-label="Bench"
      className="grid h-11 grid-cols-4 gap-[3px] rounded-xl p-[3px]"
      style={{ background: "var(--surface)" }}
    >
      {BENCHES.map(({ id, label }) => {
        const active = id === bench;
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(id)}
            className="rounded-[9px] border-0 text-[13px] font-medium"
            style={{
              background: active ? "var(--field)" : "transparent",
              color: active ? "var(--fg)" : "var(--muted)",
            }}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Write `web/src/workshop/Workshop.tsx`**

```tsx
import { useState } from "react";
import { evs, hhmm } from "@/lib/format";
import type { StreamRef } from "@/lib/streams";
import { useOverview } from "@/room/useOverview";
import { useConnection } from "@/shell/ConnectionProvider";
import { Layer } from "@/shell/Layer";
import { ActivityBench } from "./ActivityBench";
import { BenchSwitcher, type Bench } from "./BenchSwitcher";
import { useActivity } from "./useActivity";

export interface WorkshopProps {
  open: boolean;
  onClose: () => void;
  /** A row asked `Why · causal thread`. The Room opens the sheet. */
  onWhy: (ref: StreamRef) => void;
}

/**
 * The Workshop (spec §4.10, §8 phase 2): the second of the two surfaces, under
 * the Room's handle. A `Layer` at the workshop level, so the Why sheet and the
 * Door both paint over it. The panel inside carries every hook, so the feed
 * is subscribed and read only while the layer is mounted — including the
 * 400 ms it takes to leave.
 */
export function Workshop({ open, onClose, onWhy }: WorkshopProps) {
  return (
    <Layer open={open} label="Workshop" durationMs={400} level="workshop">
      <WorkshopPanel onClose={onClose} onWhy={onWhy} />
    </Layer>
  );
}

interface WorkshopPanelProps {
  onClose: () => void;
  onWhy: (ref: StreamRef) => void;
}

const UNBUILT = "not built yet · phase 3";

function WorkshopPanel({ onClose, onWhy }: WorkshopPanelProps) {
  const [bench, setBench] = useState<Bench>("activity");
  const activity = useActivity();
  const { lastTrueAt } = useConnection();
  const overview = useOverview();

  // §5.2: live is not last-known. Not-live outranks paused; paused outranks the rate.
  const status = !activity.live
    ? `last true ${lastTrueAt ? hhmm(lastTrueAt) : "--:--"} · not live`
    : activity.paused
      ? `paused · ${activity.heldCount} new`
      : `live · ${evs(overview.data?.streams ?? {})} ev/s`;

  return (
    <>
      <header className="flex flex-col gap-2.5 px-4 pt-[62px]">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={onClose}
            className="-ml-2 flex h-11 items-center gap-1.5 px-2 text-[15px] font-medium"
            style={{ color: "var(--accent)" }}
          >
            <span
              aria-hidden="true"
              className="h-[9px] w-[9px] rotate-45"
              style={{ borderLeft: "1.5px solid currentColor", borderBottom: "1.5px solid currentColor" }}
            />
            Room
          </button>
          <span className="t-meta" data-testid="workshop-status">
            {status}
          </span>
        </div>
        <BenchSwitcher bench={bench} onChange={setBench} />
      </header>
      {bench === "activity" ? (
        <ActivityBench activity={activity} onWhy={onWhy} />
      ) : (
        <p className="t-meta flex flex-1 items-center justify-center">{UNBUILT}</p>
      )}
    </>
  );
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/workshop/BenchSwitcher.test.tsx src/workshop/Workshop.test.tsx`
Expected: 2 + 7 passed. Whole suite: `Test Files  50 passed (50)`, `Tests  707 passed (707)`.

- [ ] **Step 6: Lint, typecheck and commit**

```bash
cd web && npm run lint && npx tsc -b
git add src/workshop/BenchSwitcher.tsx src/workshop/BenchSwitcher.test.tsx src/workshop/Workshop.tsx src/workshop/Workshop.test.tsx
git commit -m "feat(web): Workshop layer with bench switcher and Activity"
```

## Task 8: `lib/trace.ts` — the causal thread

**Files:**
- Create: `web/src/lib/trace.ts`
- Create: `web/src/lib/trace.test.ts`

Spec §7: correlation is a client-side heuristic until the backend carries a correlation id. Every join is an id the server already holds — `event_id` (a reflex observation's `trigger_event` is the originating event's full dump, id included), `request_id` (action ↔ result ↔ confirmation notification's `pending_action_id`), `session_id` (request ↔ reply), `trigger_id` (trigger fired ↔ its observation) — plus two joins the schemas carry as names rather than ids: a reply's `actions_taken` names a tool, and an action's `parameters.entity_id` names the entity whose state then changes. Anything within five seconds of a joined node but joined to nothing is shown dashed — "adjacent in time only" — so the reader sees what else was happening without being told it is causal.

Pure function first (`buildThread`), then the eight-page read that feeds it (`fetchThreadCandidates`), then the composition (`fetchThread`). The Why sheet (Task 9) renders `nodes` and `searched`, nothing else.

- [ ] **Step 1: Write the failing tests**

`web/src/lib/trace.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import type { StreamPage } from "./types";
import type { StreamName, StreamRef } from "./streams";
import {
  buildThread,
  fetchThread,
  fetchThreadCandidates,
  JOIN_WINDOW_MS,
  MAX_ADJACENT,
  nodeMeta,
  type ThreadNode,
} from "./trace";

// 21:13:20 on the device's clock, so hhmmss reads the same under CI's UTC and on the phone.
const T0 = new Date(2026, 8, 7, 21, 13, 20).getTime();

function ref(stream: StreamName, ms: number, event: Record<string, unknown>): StreamRef {
  return { stream, entry: { id: `${ms}-0`, event } };
}

// The movie thread: the TV starts, the reflex dims the lights, the light reports, the result lands, the observation is written.
const TV = ref("home_state", T0, {
  event_id: "9c41e0aa",
  entity_id: "media_player.tv",
  old_state: "idle",
  new_state: "playing",
  domain: "media",
  source: "home-service",
});
const ACTION = ref("actions", T0 + 500, {
  event_id: "1f22b7c0",
  request_id: "4b1d9e00",
  tool_name: "home.light_set",
  parameters: { entity_id: "light.living_room", brightness: 30 },
  target_service: "home-service",
  source: "reflex-engine",
});
const LIGHT = ref("home_state", T0 + 1200, {
  event_id: "7d80aa31",
  entity_id: "light.living_room",
  old_state: "off",
  new_state: "on",
  domain: "home",
  source: "home-service",
});
const RESULT = ref("home_action_results", T0 + 1500, {
  event_id: "c3d4e5f6",
  request_id: "4b1d9e00",
  tool_name: "home.light_set",
  status: "success",
});
const ANCHOR = ref("reflex_observations", T0 + 2000, {
  event_id: "e8f9a0b1",
  origin: "state_change",
  trigger_event: TV.entry.event,
  action: ACTION.entry.event,
  result: { status: "success" },
  decision_context: "movie started, evening, user home",
});
const PARCEL = ref("notifications", T0 + 3000, {
  notification_id: "n-1",
  title: "Parcel",
  body: "Your parcel arrived",
  urgency: "informational",
  source: "trigger:trg_parcel",
});

function ids(nodes: { entry: { id: string } }[]): string[] {
  return nodes.map((node) => node.entry.id);
}

function linkOf(nodes: ThreadNode[], target: StreamRef): ThreadNode["link"] | undefined {
  return nodes.find((node) => node.stream === target.stream && node.entry.id === target.entry.id)?.link;
}

function node(nodes: ThreadNode[], target: StreamRef): ThreadNode {
  const found = nodes.find((n) => n.stream === target.stream && n.entry.id === target.entry.id);
  if (!found) throw new Error(`${target.stream}:${target.entry.id} is not in the thread`);
  return found;
}

describe("buildThread", () => {
  it("is the anchor alone when nothing joins it or is near it", () => {
    const far = ref("user_requests", T0 + 400_000, { event_id: "u1", session_id: "s_1", content: "hi" });
    expect(buildThread(ANCHOR, [far])).toEqual([{ ...ANCHOR, link: "anchor" }]);
  });

  it("joins the state change the observation was triggered by, by event_id", () => {
    expect(linkOf(buildThread(ANCHOR, [TV]), TV)).toEqual({ key: "event_id", value: "9c41e0aa" });
  });

  it("joins the action, its result and its confirmation by request_id", () => {
    const confirm = ref("notifications", T0 + 700, {
      notification_id: "n-2",
      title: "Confirm?",
      body: "Dim the lights?",
      urgency: "actionable",
      source: "domain-router",
      metadata: { pending_action_id: "4b1d9e00" },
    });
    const nodes = buildThread(ANCHOR, [ACTION, RESULT, confirm]);
    const byRequest = { key: "request_id", value: "4b1d9e00" };
    expect(linkOf(nodes, ACTION)).toEqual(byRequest);
    expect(linkOf(nodes, RESULT)).toEqual(byRequest);
    expect(linkOf(nodes, confirm)).toEqual(byRequest);
  });

  it("joins the state change of the entity the observation's action set", () => {
    // LIGHT shares no id with the anchor; it is the entity the observation's action named.
    expect(linkOf(buildThread(ANCHOR, [LIGHT]), LIGHT)).toEqual({
      key: "entity_id",
      value: "light.living_room",
    });
  });

  it("follows joins through nodes it has admitted: the reply naming the tool, then the request in its session", () => {
    const reply = ref("user_responses", T0 + 2500, {
      event_id: "r1",
      session_id: "s_9f3",
      text: "Dimmed.",
      actions_taken: ["home.light_set"],
    });
    const request = ref("user_requests", T0 - 8000, { event_id: "q1", session_id: "s_9f3", content: "Movie time" });

    // Only an `actions` entry can be named by a reply — without ACTION, neither joins.
    const without = buildThread(ANCHOR, [reply, request]);
    expect(linkOf(without, reply)).toBe("adjacent");
    expect(linkOf(without, request)).toBeUndefined();

    const nodes = buildThread(ANCHOR, [ACTION, reply, request]);
    expect(linkOf(nodes, reply)).toEqual({ key: "actions_taken", value: "home.light_set" });
    expect(linkOf(nodes, request)).toEqual({ key: "session_id", value: "s_9f3" });
  });

  it("does not credit a reply written before the action it would have named", () => {
    const earlier = ref("user_responses", T0 - 1000, {
      event_id: "r0",
      session_id: "s_9f3",
      text: "Earlier.",
      actions_taken: ["home.light_set"],
    });
    expect(linkOf(buildThread(ANCHOR, [ACTION, earlier]), earlier)).toBe("adjacent");
  });

  it("attributes a state change to an action only within a minute of it", () => {
    // 61 s after the observation, longer after the action it carries.
    const late = ref("home_state", T0 + 2000 + 61_000, LIGHT.entry.event);
    expect(linkOf(buildThread(ANCHOR, [ACTION, late]), late)).toBeUndefined();
  });

  it("ignores an id join outside the ten-minute window", () => {
    const stale = ref("home_action_results", T0 - JOIN_WINDOW_MS - 1, RESULT.entry.event);
    expect(ids(buildThread(ANCHOR, [stale]))).toEqual([ANCHOR.entry.id]);
  });

  it("shows what is merely near in time as adjacent, nearest the anchor first, at most six", () => {
    const near = Array.from({ length: 8 }, (_, i) =>
      ref("home_state", T0 + 2000 + (i + 1) * 100, { event_id: `n${i}`, entity_id: `sensor.${i}`, new_state: String(i) }),
    );
    const nodes = buildThread(ANCHOR, [...near, PARCEL]);
    const adjacent = nodes.filter((n) => n.link === "adjacent");
    expect(adjacent).toHaveLength(MAX_ADJACENT);
    expect(ids(adjacent)).toEqual(ids(near.slice(0, MAX_ADJACENT)));
    expect(linkOf(nodes, PARCEL)).toBeUndefined();
  });

  it("is adjacent to any joined node, not only the anchor", () => {
    // 4 s before TV (joined), 6 s before the anchor.
    const nearTv = ref("events", T0 - 4000, {
      event_id: "ev-1",
      event_type: "trigger_fired",
      trigger_id: "trg_x",
      trigger_name: "x",
    });
    expect(linkOf(buildThread(ANCHOR, [nearTv]), nearTv)).toBeUndefined();
    expect(linkOf(buildThread(ANCHOR, [TV, nearTv]), nearTv)).toBe("adjacent");
  });

  it("orders oldest first, catalogue order on a tie", () => {
    // The same event as TV, republished on `events` at the same millisecond.
    const twin = ref("events", T0, { event_id: "9c41e0aa", event_type: "state_changed" });
    const nodes = buildThread(ANCHOR, [RESULT, TV, ACTION, LIGHT, twin]);
    expect(nodes.map((n) => `${n.stream}@${n.entry.id}`)).toEqual([
      `events@${T0}-0`,
      `home_state@${T0}-0`,
      `actions@${T0 + 500}-0`,
      `home_state@${T0 + 1200}-0`,
      `home_action_results@${T0 + 1500}-0`,
      `reflex_observations@${T0 + 2000}-0`,
    ]);
  });
});

describe("nodeMeta", () => {
  it("stamps, names the stream, carries the summary meta and says how the node got here", () => {
    const nodes = buildThread(ANCHOR, [ACTION, PARCEL]);
    expect(nodeMeta(node(nodes, ANCHOR))).toBe(
      '21:13:22 · RX · home.light_set · request 4b1d · decision "movie started, evening, user home" · this row',
    );
    expect(nodeMeta(node(nodes, ACTION))).toBe(
      "21:13:20 · AC · request 4b1d · home-service · reflex-engine · joined by request_id 4b1d",
    );
    expect(nodeMeta(node(nodes, PARCEL))).toBe(
      "21:13:23 · NT · informational · trigger:trg_parcel · adjacent in time only",
    );
  });

  it("prints a named join in full and an id join short", () => {
    const nodes = buildThread(ANCHOR, [ACTION, LIGHT, TV]);
    expect(nodeMeta(node(nodes, LIGHT))).toContain("joined by entity_id light.living_room");
    expect(nodeMeta(node(nodes, TV))).toContain("joined by event_id 9c41");
  });
});

describe("fetchThreadCandidates", () => {
  const empty: StreamPage = { entries: [], next_before: null };

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads one page of every stream ending ten minutes after the anchor, and drops the anchor itself", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url.startsWith("/api/admin/streams/home_state?")) {
          return new Response(JSON.stringify({ entries: [TV.entry], next_before: null }), { status: 200 });
        }
        if (url.startsWith("/api/admin/streams/reflex_observations?")) {
          return new Response(JSON.stringify({ entries: [ANCHOR.entry], next_before: null }), { status: 200 });
        }
        if (url.startsWith("/api/admin/streams/events?")) {
          return new Response(JSON.stringify({ detail: "redis gone" }), { status: 503 });
        }
        return new Response(JSON.stringify(empty), { status: 200 });
      }),
    );

    const { candidates, searched } = await fetchThreadCandidates(ANCHOR);

    const before = `${T0 + 2000 + JOIN_WINDOW_MS + 1}-0`;
    expect(calls).toHaveLength(8);
    expect(calls[0]).toBe(`/api/admin/streams/user_requests?count=100&before=${before}`);
    expect(new Set(calls.map((url) => url.split("?")[1]))).toEqual(new Set([`count=100&before=${before}`]));
    expect(candidates).toEqual([TV]);
    expect(searched).toBe(7);
  });

  it("fails when no stream could be read, with the reason", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "redis gone" }), { status: 503 })),
    );

    await expect(fetchThreadCandidates(ANCHOR)).rejects.toThrow("redis gone");
  });

  it("composes into a thread", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const entries = url.startsWith("/api/admin/streams/home_state?")
          ? [TV.entry]
          : url.startsWith("/api/admin/streams/actions?")
            ? [ACTION.entry]
            : [];
        return new Response(JSON.stringify({ entries, next_before: null }), { status: 200 });
      }),
    );

    const thread = await fetchThread(ANCHOR);

    expect(ids(thread.nodes)).toEqual([TV.entry.id, ACTION.entry.id, ANCHOR.entry.id]);
    expect(thread.searched).toBe(8);
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/lib/trace.test.ts`
Expected: fails to import `./trace`.

- [ ] **Step 3: Write `web/src/lib/trace.ts`**

```ts
import { hhmmss, shortId } from "./format";
import {
  compareIds,
  fetchStreamPage,
  idMs,
  record,
  scalar,
  STREAM_INFO,
  STREAMS,
  strings,
  summarise,
  type StreamRef,
} from "./streams";

/** An id join may reach this far either side of the anchor. */
export const JOIN_WINDOW_MS = 600_000;
/** An unjoined entry this close to any joined node is shown dashed… */
export const ADJACENT_MS = 5_000;
/** …but no more than this many of them, nearest the anchor first. */
export const MAX_ADJACENT = 6;
/** A state change of the entity an action named, within this of the action, is that action's doing. */
export const ENTITY_MS = 60_000;
/** Entries read per stream: the page ending JOIN_WINDOW_MS after the anchor. */
export const CANDIDATE_COUNT = 100;

/**
 * How a node got into the thread. `anchor` and a `{ key, value }` join draw a
 * solid connector — an id the server holds, or a name one schema carries for
 * another. `adjacent` draws a dashed one: near in time, joined to nothing.
 */
export type Link = "anchor" | "adjacent" | { key: string; value: string };

export interface ThreadNode extends StreamRef {
  link: Link;
}

export interface Thread {
  /** Oldest first; the anchor is among them. */
  nodes: ThreadNode[];
  /** How many of the eight streams answered the candidate read. */
  searched: number;
}

export interface Candidates {
  candidates: StreamRef[];
  searched: number;
}

interface Token {
  key: string;
  value: string;
}

function refKey(ref: StreamRef): string {
  return `${ref.stream}:${ref.entry.id}`;
}

/**
 * The ids an entry can be joined on, from the fields `bus/schemas/events.py`
 * and `core/notifications/schema.py` carry. A reflex observation carries the
 * originating event's full dump, so it joins to that event's `event_id` (and
 * `trigger_id`) as well as to its own action's `request_id`. A notification is
 * not a bus event and has no `event_id`; a confirmation names its action.
 */
function tokens({ stream, entry: { event } }: StreamRef): Token[] {
  const out: Token[] = [];
  const add = (key: string, value: unknown) => {
    const text = scalar(value);
    if (text) out.push({ key, value: text });
  };
  add("event_id", event.event_id);
  switch (stream) {
    case "user_requests":
    case "user_responses":
      add("session_id", event.session_id);
      break;
    case "events":
      add("trigger_id", event.trigger_id);
      break;
    case "actions":
    case "home_action_results":
      add("request_id", event.request_id);
      break;
    case "reflex_observations": {
      const seen = record(event.trigger_event);
      add("event_id", seen?.event_id);
      add("trigger_id", seen?.trigger_id);
      add("request_id", record(event.action)?.request_id);
      break;
    }
    case "notifications":
      add("request_id", record(event.metadata)?.pending_action_id);
      break;
    case "home_state":
      // Joined by name only: the entity an action set (see `namedEntity`).
      break;
  }
  return out;
}

/** `user_responses.actions_taken` names `actions.tool_name`, and the action is no later than the reply. */
function namedTool(reply: StreamRef, action: StreamRef): Token | null {
  if (reply.stream !== "user_responses" || action.stream !== "actions") return null;
  const tool = scalar(action.entry.event.tool_name);
  if (!tool || !strings(reply.entry.event.actions_taken).includes(tool)) return null;
  if (compareIds(action.entry.id, reply.entry.id) > 0) return null;
  return { key: "actions_taken", value: tool };
}

function actedEntity({ stream, entry: { event } }: StreamRef): string | null {
  if (stream === "actions") return scalar(record(event.parameters)?.entity_id);
  if (stream === "reflex_observations") {
    return scalar(record(record(event.action)?.parameters)?.entity_id);
  }
  return null;
}

/**
 * An action (or a reflex observation's action) named an entity, and that
 * entity's state changed within ENTITY_MS. Either side: an action precedes
 * the change it causes, while an observation is written after its result —
 * which is after the change.
 */
function namedEntity(actor: StreamRef, state: StreamRef): Token | null {
  if (state.stream !== "home_state") return null;
  const entity = scalar(state.entry.event.entity_id);
  if (!entity || actedEntity(actor) !== entity) return null;
  if (Math.abs(idMs(state.entry.id) - idMs(actor.entry.id)) > ENTITY_MS) return null;
  return { key: "entity_id", value: entity };
}

/** The joins the schemas carry as names rather than ids, checked both ways round. */
function namedJoin(a: StreamRef, b: StreamRef): Token | null {
  return namedTool(a, b) ?? namedTool(b, a) ?? namedEntity(a, b) ?? namedEntity(b, a);
}

/**
 * The thread through `candidates` from `anchor` (spec §7). Everything joined
 * to the anchor, or to something joined to it, within JOIN_WINDOW_MS; then
 * whatever else sits within ADJACENT_MS of a joined node, dashed. Oldest
 * first, catalogue order on a tie.
 */
export function buildThread(anchor: StreamRef, candidates: StreamRef[]): ThreadNode[] {
  const anchorMs = idMs(anchor.entry.id);
  const inWindow = candidates.filter((ref) => Math.abs(idMs(ref.entry.id) - anchorMs) <= JOIN_WINDOW_MS);

  const byToken = new Map<string, StreamRef[]>();
  for (const ref of inWindow) {
    for (const { key, value } of tokens(ref)) {
      const id = `${key}=${value}`;
      byToken.set(id, [...(byToken.get(id) ?? []), ref]);
    }
  }

  const nodes = new Map<string, ThreadNode>([[refKey(anchor), { ...anchor, link: "anchor" }]]);
  const queue: StreamRef[] = [anchor];
  const admit = (ref: StreamRef, link: Token) => {
    const key = refKey(ref);
    if (nodes.has(key)) return;
    nodes.set(key, { ...ref, link });
    queue.push(ref);
  };
  for (let node = queue.shift(); node; node = queue.shift()) {
    for (const token of tokens(node)) {
      for (const ref of byToken.get(`${token.key}=${token.value}`) ?? []) admit(ref, token);
    }
    for (const ref of inWindow) {
      const named = namedJoin(node, ref);
      if (named) admit(ref, named);
    }
  }

  const joined = [...nodes.values()];
  const distance = (ref: StreamRef) => Math.abs(idMs(ref.entry.id) - anchorMs);
  const adjacent = inWindow
    .filter((ref) => !nodes.has(refKey(ref)))
    .filter((ref) => joined.some((node) => Math.abs(idMs(ref.entry.id) - idMs(node.entry.id)) <= ADJACENT_MS))
    .sort((a, b) => distance(a) - distance(b))
    .slice(0, MAX_ADJACENT);
  for (const ref of adjacent) nodes.set(refKey(ref), { ...ref, link: "adjacent" });

  return [...nodes.values()].sort(
    (a, b) => compareIds(a.entry.id, b.entry.id) || STREAMS.indexOf(a.stream) - STREAMS.indexOf(b.stream),
  );
}

/** Ids are shown short, the way rows show them; a tool or entity name is shown whole. */
const SHORT_KEYS = new Set(["event_id", "request_id", "session_id"]);

/** `21:13:20 · AC · request 4b1d · home-service · joined by request_id 4b1d` — the node's meta line. */
export function nodeMeta(node: ThreadNode): string {
  const { link } = node;
  const how =
    link === "anchor"
      ? "this row"
      : link === "adjacent"
        ? "adjacent in time only"
        : `joined by ${link.key} ${SHORT_KEYS.has(link.key) ? shortId(link.value) : link.value}`;
  return [
    hhmmss(idMs(node.entry.id)),
    STREAM_INFO[node.stream].mono,
    summarise(node.stream, node.entry.event).meta,
    how,
  ]
    .filter((part) => part.length > 0)
    .join(" · ");
}

/**
 * One page of every stream, ending JOIN_WINDOW_MS after the anchor (`before`
 * is exclusive) and reaching CANDIDATE_COUNT entries back. A stream that
 * cannot be read is skipped and not counted in `searched`; the anchor's own
 * entry is dropped. When nothing could be read there is no thread to show —
 * the first failure is the error.
 */
export async function fetchThreadCandidates(anchor: StreamRef): Promise<Candidates> {
  const before = `${idMs(anchor.entry.id) + JOIN_WINDOW_MS + 1}-0`;
  const results = await Promise.allSettled(
    STREAMS.map(async (stream) => ({ stream, page: await fetchStreamPage(stream, before, CANDIDATE_COUNT) })),
  );
  const self = refKey(anchor);
  const candidates: StreamRef[] = [];
  let searched = 0;
  for (const result of results) {
    if (result.status !== "fulfilled") continue;
    searched += 1;
    const { stream, page } = result.value;
    for (const entry of page.entries) {
      const ref = { stream, entry };
      if (refKey(ref) !== self) candidates.push(ref);
    }
  }
  if (searched === 0) {
    for (const result of results) if (result.status === "rejected") throw result.reason;
  }
  return { candidates, searched };
}

export async function fetchThread(anchor: StreamRef): Promise<Thread> {
  const { candidates, searched } = await fetchThreadCandidates(anchor);
  return { nodes: buildThread(anchor, candidates), searched };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/lib/trace.test.ts`
Expected: 16 passed. Whole suite: `Test Files  51 passed (51)`, `Tests  723 passed (723)`.

- [ ] **Step 5: Lint, typecheck and commit**

```bash
cd web && npm run lint && npx tsc -b
git add src/lib/trace.ts src/lib/trace.test.ts
git commit -m "feat(web): trace — join a reflex observation to what shares its ids"
```

## Task 9: `WhySheet` — "Why Alfred did that"

**Files:**
- Create: `web/src/sheets/WhySheet.tsx`
- Create: `web/src/sheets/WhySheet.test.tsx`

The handoff's Why sheet: title `Why Alfred did that`, one intro sentence, a column of nodes (monogram tile, a connector below it, the row's line and a meta line), and a footnote saying how much was searched. It is a `Sheet` (z-20), so it paints over the Workshop (z-10) and the Room alike — the same component serves a `why?` on a Room act row (Task 10) and `Why · causal thread` on an Activity row (Task 7).

`anchor` is the row asked about, `null` when closed. The sheet keeps the last anchor while it leaves (380 ms), the way `HeldBackSheet` keeps its state, so the column does not vanish mid-animation. The thread is a `useQuery` keyed by the anchor, so re-asking about the same row inside `staleTime` does not re-read eight streams.

- [ ] **Step 1: Write the failing tests**

`web/src/sheets/WhySheet.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { STREAMS, type StreamRef } from "@/lib/streams";
import type { StreamEntry } from "@/lib/types";
import { reflexObservationsPage } from "@/test/fixtures";
import { WhySheet } from "./WhySheet";

// obs-1: the observation that dimmed the lights when the TV started (request 4b1d).
const ANCHOR: StreamRef = { stream: "reflex_observations", entry: reflexObservationsPage.entries[2] };
const ANCHOR_MS = 1788800280000;

const ACTION: StreamEntry = {
  id: `${ANCHOR_MS - 1000}-0`,
  event: {
    event_id: "1f22b7c0",
    request_id: "4b1d",
    tool_name: "home.light_set",
    parameters: { entity_id: "light.living_room", brightness: 30 },
    target_service: "home-service",
    source: "reflex-engine",
  },
};
const RESULT: StreamEntry = {
  id: `${ANCHOR_MS - 500}-0`,
  event: { event_id: "c3d4e5f6", request_id: "4b1d", tool_name: "home.light_set", status: "success" },
};
// A second later, joined to nothing: the front door sensor.
const DOOR: StreamEntry = {
  id: `${ANCHOR_MS + 1000}-0`,
  event: {
    event_id: "7d80aa31",
    entity_id: "binary_sensor.front_door",
    old_state: "off",
    new_state: "on",
    domain: "home",
    source: "home-service",
  },
};

/** Entries per stream, or an HTTP status to fail that stream with. Unlisted streams are empty. */
let pages: Record<string, StreamEntry[] | number> = {};

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const name = String(input).replace("/api/admin/streams/", "").split("?")[0];
      const page = pages[name] ?? [];
      if (typeof page === "number") {
        return new Response(JSON.stringify({ detail: "redis gone" }), { status: page });
      }
      return new Response(JSON.stringify({ entries: page, next_before: null }), { status: 200 });
    }),
  );
}

function mount(anchor: StreamRef | null) {
  const onClose = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (ref: StreamRef | null) => (
    <QueryClientProvider client={client}>
      <WhySheet anchor={ref} onClose={onClose} />
    </QueryClientProvider>
  );
  const view = render(tree(anchor));
  return { onClose, setAnchor: (ref: StreamRef | null) => view.rerender(tree(ref)) };
}

const FOOTNOTE = "searched 8 streams · 100 entries each · ±10 min";

beforeEach(() => {
  pages = {};
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WhySheet", () => {
  it("renders nothing until a row asks, then explains the drawing and reads", async () => {
    const { setAnchor } = mount(null);
    expect(screen.queryByRole("dialog")).toBeNull();

    setAnchor(ANCHOR);
    expect(screen.getByRole("dialog", { name: "Why Alfred did that" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Every link drawn solid is joined by an id the server holds. Dashed means adjacent in time only.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("reading 8 streams…");

    await screen.findByText(FOOTNOTE);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("draws the thread oldest first: solid into joined nodes, dashed into neighbours", async () => {
    pages = { actions: [ACTION], home_action_results: [RESULT], home_state: [DOOR] };
    mount(ANCHOR);
    await screen.findByText(FOOTNOTE);

    const items = screen.getAllByRole("listitem");
    expect(items.map((item) => item.querySelector(".t-row")?.textContent)).toEqual([
      "home.light_set light.living_room",
      "home.light_set success",
      "observed media_player.tv · acted",
      "binary_sensor.front_door → on",
    ]);
    const metas = items.map((item) => item.querySelector(".t-meta")?.textContent ?? "");
    expect(metas[0]).toContain("AC · request 4b1d · home-service · reflex-engine · joined by request_id 4b1d");
    expect(metas[1]).toContain("HR · request 4b1d · joined by request_id 4b1d");
    expect(metas[2]).toContain(
      'RX · home.light_set · request 4b1d · decision "movie started, evening, user home" · this row',
    );
    expect(metas[3]).toContain("HS · home · was off · via home-service · adjacent in time only");
    expect(screen.getAllByTestId("connector").map((node) => node.dataset.dashed)).toEqual([
      "false",
      "false",
      "true",
    ]);
  });

  it("says how many streams it could search", async () => {
    pages = { events: 503, notifications: 503 };
    mount(ANCHOR);
    await screen.findByText("searched 6 of 8 streams · 100 entries each · ±10 min");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("reports when no stream could be read", async () => {
    pages = Object.fromEntries(STREAMS.map((name) => [name, 503]));
    mount(ANCHOR);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("redis gone"));
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("keeps the thread on screen while it leaves", async () => {
    pages = { actions: [ACTION] };
    const { setAnchor } = mount(ANCHOR);
    await screen.findByText("home.light_set light.living_room");

    setAnchor(null);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("home.light_set light.living_room")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web && npx vitest run src/sheets/WhySheet.test.tsx`
Expected: fails to import `./WhySheet`.

- [ ] **Step 3: Write `web/src/sheets/WhySheet.tsx`**

```tsx
import { useState } from "react";
import { skipToken, useQuery } from "@tanstack/react-query";
import { failureText } from "@/lib/auth";
import { ring, STREAM_INFO, STREAMS, summarise, type StreamRef } from "@/lib/streams";
import { CANDIDATE_COUNT, fetchThread, JOIN_WINDOW_MS, nodeMeta, type Thread } from "@/lib/trace";
import { Sheet } from "@/shell/Sheet";

const INTRO =
  "Every link drawn solid is joined by an id the server holds. Dashed means adjacent in time only.";

export interface WhySheetProps {
  /** The row asked about; `null` closes the sheet. */
  anchor: StreamRef | null;
  onClose: () => void;
}

function footnote(searched: number): string {
  const streams =
    searched === STREAMS.length ? `${STREAMS.length} streams` : `${searched} of ${STREAMS.length} streams`;
  return `searched ${streams} · ${CANDIDATE_COUNT} entries each · ±${JOIN_WINDOW_MS / 60_000} min`;
}

/**
 * The handoff's "Why Alfred did that": the thread `lib/trace.ts` draws
 * through the eight streams from one row (spec §7). A `Sheet`, so it sits over
 * the Workshop and the Room alike.
 */
export function WhySheet({ anchor, onClose }: WhySheetProps) {
  // The sheet is still on screen for its leave after `anchor` goes null; keep
  // drawing the last thread rather than emptying the column mid-animation.
  // Adjusted during render, as `usePresence` does.
  const [shown, setShown] = useState(anchor);
  if (anchor !== null && anchor !== shown) setShown(anchor);

  const thread = useQuery<Thread>({
    queryKey: ["trace", shown?.stream, shown?.entry.id],
    // Read only while open; the cached thread for `shown` survives the leave.
    queryFn: anchor ? () => fetchThread(anchor) : skipToken,
  });

  const nodes = thread.data?.nodes ?? [];

  return (
    <Sheet open={anchor !== null} title="Why Alfred did that" onClose={onClose}>
      <p className="text-[13.5px] leading-[1.5]" style={{ color: "var(--fg2)" }}>
        {INTRO}
      </p>

      {thread.isLoading ? (
        <p role="status" className="t-meta">
          reading {STREAMS.length} streams…
        </p>
      ) : null}

      {thread.isError ? (
        <p role="status" className="t-meta">
          {failureText(thread.error)}
        </p>
      ) : null}

      {thread.data ? (
        <>
          <ol className="m-0 list-none p-0 pt-1.5">
            {nodes.map((node, index) => {
              const next = nodes[index + 1];
              const { mono, hue } = STREAM_INFO[node.stream];
              // A segment touching an adjacent node is dashed; every other is a join.
              const dashed = node.link === "adjacent" || next?.link === "adjacent";
              return (
                <li key={`${node.stream}:${node.entry.id}`} className="grid grid-cols-[22px_1fr] gap-3">
                  <div className="flex flex-col items-center">
                    <span
                      aria-hidden="true"
                      className="t-monogram h-[22px] w-[22px] shrink-0 rounded-md text-center"
                      style={{ background: ring(hue), color: "#fff" }}
                    >
                      {mono}
                    </span>
                    {next ? (
                      <span
                        aria-hidden="true"
                        data-testid="connector"
                        data-dashed={dashed}
                        className="my-1 min-h-[22px] w-0 flex-1"
                        style={{ borderLeft: `1.5px ${dashed ? "dashed" : "solid"} var(--line)` }}
                      />
                    ) : null}
                  </div>
                  <div className="flex min-w-0 flex-col gap-0.5 pb-3.5">
                    <div className="t-row">{summarise(node.stream, node.entry.event).text}</div>
                    <div className="t-meta break-words">{nodeMeta(node)}</div>
                  </div>
                </li>
              );
            })}
          </ol>
          <p className="t-meta">{footnote(thread.data.searched)}</p>
        </>
      ) : null}
    </Sheet>
  );
}
```

`skipToken` (TanStack Query ≥ 5.25; the repo has 5.101) is the typed way to disable a query without an `enabled` flag and a non-null assertion in the `queryFn`. While the sheet is closed the query is idle but its cached data for `shown` is still returned, which is what the leave test relies on.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/sheets/WhySheet.test.tsx`
Expected: 5 passed. Whole suite: `Test Files  52 passed (52)`, `Tests  728 passed (728)`.

- [ ] **Step 5: Lint, typecheck and commit**

```bash
cd web && npm run lint && npx tsc -b
git add src/sheets/WhySheet.tsx src/sheets/WhySheet.test.tsx
git commit -m "feat(web): Why sheet — the causal thread as a column"
```

---

## Task 10: The Room's handle, `why?` on reflex rows, Workshop and Why sheet mounted

Everything so far is built and tested in isolation; nothing in the app reaches it. This task wires it in: the handle under the composer opens the Workshop, a reflex act row in the Room grows the handoff's `why?` button, and one `WhySheet` in `Room.tsx` serves both the Room's `why?` and the Workshop's `Why · causal thread`.

**Where the handle lives.** The handoff draws it under the composer and hides it while the keyboard is up. `Composer`'s outer element already owns the keyboard-and-safe-area padding (`.pb-keyboard`), so the handle goes *inside* that element, after the row — a `handle` slot, like the existing `hold` slot — and the safe-area inset falls below the handle instead of between the row and the handle. `Composer` already knows whether the keyboard is up; it renders the slot only while it is down, and `WorkshopHandle` stays a dumb button.

**Files:**
- Create: `web/src/room/WorkshopHandle.tsx`, `web/src/room/WorkshopHandle.test.tsx`
- Modify: `web/src/room/Composer.tsx`, `web/src/room/Composer.test.tsx`, `web/src/room/rows/ActRow.tsx`, `web/src/room/Timeline.tsx`, `web/src/room/Timeline.test.tsx`, `web/src/lib/history.ts`, `web/src/lib/history.test.ts`, `web/src/room/Room.tsx`, `web/src/sheets/HeldBackSheet.tsx`, `web/src/index.css`, `web/src/App.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `web/src/room/WorkshopHandle.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkshopHandle } from "./WorkshopHandle";

describe("WorkshopHandle", () => {
  it("reads workshop under a chevron, over the bar", () => {
    render(<WorkshopHandle onOpen={() => {}} />);
    const handle = screen.getByRole("button", { name: "Open the Workshop" });
    expect(handle).toHaveTextContent("workshop");
    expect(handle).toHaveClass("min-h-11");
    expect(handle.querySelector("[data-testid=handle-bar]")).toHaveStyle({ background: "var(--fg)" });
  });

  it("opens on a tap", () => {
    const onOpen = vi.fn();
    render(<WorkshopHandle onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button", { name: "Open the Workshop" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
```

Append to the `describe("Composer", …)` block in `web/src/room/Composer.test.tsx`, after `"keeps the inset while the keyboard is down"`:

```tsx
  it("shows the handle under the row while the keyboard is down", () => {
    setViewport(852);
    const { container } = render(
      <Composer online onSend={() => {}} hold={null} handle={<button type="button">workshop</button>} />,
    );
    const handle = screen.getByRole("button", { name: "workshop" });
    // Inside the padded element, so the safe-area inset falls below the handle.
    expect(container.querySelector(".pb-keyboard")).toContainElement(handle);
  });

  it("hides the handle while the keyboard is up", () => {
    setViewport(500);
    render(<Composer online onSend={() => {}} hold={null} handle={<button type="button">workshop</button>} />);
    expect(screen.queryByRole("button", { name: "workshop" })).toBeNull();
  });
```

In `web/src/lib/history.test.ts`, the test `"reads a reflex act as its decision, in the RX hue"` gets the new field — the act now points back at the observation it was made from:

```ts
  it("reads a reflex act as its decision, in the RX hue", () => {
    expect(items[0]).toEqual({
      kind: "act",
      id: "rx:1788800280000-0",
      at: "2026-09-07T17:58:00",
      hue: 210,
      text: "movie started, evening, user home",
      meta: "17:58 · reflex · home.light_set",
      why: { stream: "reflex_observations", entry: reflexObservationsPage.entries[2] },
    });
  });
```

(`"renders a notification as its body, in the NT hue"` keeps its exact `toEqual` and so proves a notification carries no `why`.)

Append to `web/src/room/Timeline.test.tsx`, inside the `describe("Timeline", …)` block after the `it.each([120, 210, 255] …)` test:

```tsx
  const observation: TimelineItem = {
    kind: "act",
    id: "rx:1788800280000-0",
    at: you.at,
    hue: 210,
    text: "movie started, evening, user home",
    meta: "17:58 · reflex · home.light_set",
    why: {
      stream: "reflex_observations",
      entry: { id: "1788800280000-0", event: { event_type: "reflex_observation" } },
    },
  };

  it("offers why? on a reflex act row and hands back its observation", () => {
    const onWhy = vi.fn();
    render(<Timeline items={[observation]} firstDayGreeting={null} onWhy={onWhy} />);
    const why = screen.getByRole("button", { name: "why?" });
    expect(why).toHaveClass("h-11");
    expect(why).toHaveStyle({ color: "var(--accent)" });
    fireEvent.click(why);
    expect(onWhy).toHaveBeenCalledWith(observation.why);
  });

  it("shows no why? on a row with nothing to trace, or when nobody is listening", () => {
    const { rerender } = render(
      <Timeline items={[{ ...observation, why: undefined }]} firstDayGreeting={null} onWhy={() => {}} />,
    );
    expect(screen.queryByRole("button", { name: "why?" })).toBeNull();
    rerender(<Timeline items={[observation]} firstDayGreeting={null} />);
    expect(screen.queryByRole("button", { name: "why?" })).toBeNull();
  });
```

In `web/src/App.test.tsx`, first give the telemetry mock the method the Workshop calls when it leaves (the Door only ever subscribes; the Workshop is the first thing that lets go):

```tsx
vi.mock("@/lib/telemetry-socket", () => ({
  TelemetrySocket: class {
    onstatus = () => {};
    connect() {}
    close() {}
    subscribe() {}
    unsubscribe() {}
    listen() {
      return () => {};
    }
  },
}));
```

Then append to the first `describe("App", …)` block, after `"greets a house on its first day, and keeps up with the clock"`:

```tsx
  it("opens the Workshop from the handle and returns to the Room", async () => {
    render(<App />);
    await screen.findByText("What have I got tomorrow morning?");

    fireEvent.click(screen.getByRole("button", { name: "Open the Workshop" }));
    const workshop = await screen.findByRole("dialog", { name: "Workshop" });
    expect(within(workshop).getByRole("tab", { name: "Activity" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // The bench reads the eight streams' heads; the four the Room also reads
    // come from the same routes, so the reflex observation is a row here too.
    expect(
      await within(workshop).findByText("observed media_player.tv · acted"),
    ).toBeInTheDocument();

    fireEvent.click(within(workshop).getByRole("button", { name: "Room" }));
    act(() => vi.advanceTimersByTime(400));
    expect(screen.queryByRole("dialog", { name: "Workshop" })).toBeNull();
    expect(screen.getByRole("log", { name: "Conversation" })).toBeInTheDocument();
  });

  it("asks why on a reflex act row and sees the thread sheet", async () => {
    vi.setSystemTime(new Date(2026, 8, 7, 21, 20));
    render(<App />);
    const decision = await screen.findByText("movie started, evening, user home");
    // ActRow: outer row > [mark, text column > [line, meta], why?]
    const row = decision.parentElement!.parentElement!;

    fireEvent.click(within(row).getByRole("button", { name: "why?" }));
    const sheet = await screen.findByRole("dialog", { name: "Why Alfred did that" });
    // Nothing else the house answers shares an id with it, so the column is
    // the observation alone — over eight fully searched streams.
    expect(await within(sheet).findByText("observed media_player.tv · acted")).toBeInTheDocument();
    expect(within(sheet).getAllByRole("listitem")).toHaveLength(1);
    expect(
      within(sheet).getByText("searched 8 streams · 100 entries each · ±10 min"),
    ).toBeInTheDocument();
    expect(fetched().filter((url) => url.includes("count=100&before=1788800880001-0"))).toHaveLength(8);

    fireEvent.click(within(sheet).getByRole("button", { name: "Done" }));
    act(() => vi.advanceTimersByTime(380));
    expect(screen.queryByRole("dialog", { name: "Why Alfred did that" })).toBeNull();
  });
```

`1788800880001-0` is the observation's id plus `JOIN_WINDOW_MS + 1` — the exclusive `before` Task 8's `fetchThreadCandidates` asks for. The eight candidate reads land on paths the harness does not list, so they answer `{}` and `fetchStreamPage` normalises them to empty pages; that is what makes the thread the anchor alone.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npx vitest run src/room/WorkshopHandle.test.tsx src/room/Composer.test.tsx src/lib/history.test.ts src/room/Timeline.test.tsx src/App.test.tsx`
Expected: `WorkshopHandle.test.tsx` fails to import; the first Composer handle test fails (`Composer` has no `handle` slot yet, so `getByRole` finds no `workshop` button — the second passes vacuously for the same reason); the history `toEqual` fails on the missing `why`; the first Timeline test fails with no `why?` button (vitest runs the file although TypeScript rejects `onWhy` and `why` — `tsc -b` in Step 8 is where that is checked); both App tests fail on `Open the Workshop` and `why?` not found. Everything else still passes.

- [ ] **Step 3: The handle and its slot**

Create `web/src/room/WorkshopHandle.tsx`:

```tsx
export interface WorkshopHandleProps {
  onOpen: () => void;
}

/**
 * The way into the Workshop (handoff, Room): a 44 px button, mono 11 muted
 * `workshop` under a small up-chevron, then a 139×5 radius-3 bar in `fg`.
 * `Composer` renders it under the row and hides it while the keyboard is up.
 */
export function WorkshopHandle({ onOpen }: WorkshopHandleProps) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label="Open the Workshop"
      className="flex min-h-11 w-full flex-col items-center gap-2.5 border-0 bg-transparent pt-0.5 pb-2"
      style={{ color: "var(--muted)" }}
    >
      <span className="t-meta flex flex-col items-center gap-0.5">
        <span
          aria-hidden="true"
          className="block h-2 w-2 border-t-[1.5px] border-l-[1.5px] border-current"
          style={{ transform: "rotate(45deg)" }}
        />
        workshop
      </span>
      <span
        aria-hidden="true"
        data-testid="handle-bar"
        className="block h-[5px] w-[139px] rounded-[3px]"
        style={{ background: "var(--fg)" }}
      />
    </button>
  );
}
```

Modify `web/src/room/Composer.tsx` — the props, the signature, and the outer element:

```tsx
export interface ComposerProps {
  online: boolean;
  onSend: (text: string) => void;
  /** The hold-to-talk button, shown whenever there is no draft. */
  hold: ReactNode;
  /** The Workshop handle, under the row — and gone while the keyboard is up (handoff). */
  handle?: ReactNode;
}
```

```tsx
export function Composer({ online, onSend, hold, handle }: ComposerProps) {
```

```tsx
  return (
    // Two elements on purpose: the outer one owns the keyboard and safe-area
    // padding (a Tailwind padding utility on the same element would out-rank the
    // `@layer components` rule and silently drop the inset), the inner one the
    // handoff's own `0 20 8`. The handle sits between them so the safe-area
    // inset falls below it, not between it and the row.
    <div className={`pb-keyboard relative z-[1] ${keyboardOpen ? "keyboard-up" : ""}`}>
      <div className="flex items-center gap-2.5 px-5 pb-2">
        {/* … the field and the send/hold slot, unchanged … */}
      </div>
      {keyboardOpen ? null : handle}
    </div>
  );
```

(The `{/* … */}` marks the existing inner row; leave its contents exactly as they are.)

- [ ] **Step 4: `why?` on act rows, from the history item up**

`web/src/lib/history.ts` — import the ref type and add the field:

```ts
import type { StreamRef } from "./streams";
```

```ts
  /** hue 120 trigger (EV) · 210 reflex (RX) · 255 notification (NT) — the handoff's stream hues. */
  | {
      kind: "act";
      id: string;
      at: string;
      hue: 120 | 210 | 255;
      text: string;
      meta: string;
      /** Set only on reflex acts: what `why?` opens the causal thread on. */
      why?: StreamRef;
    }
```

In `reflexItem`, the returned object gains one line:

```ts
  return {
    kind: "act",
    id: `rx:${entry.id}`,
    at,
    hue: 210,
    text: str(entry.event.decision_context) ?? tool,
    meta: `${hhmm(at)} · reflex · ${tool}${failed ? " · failed" : ""}`,
    why: { stream: "reflex_observations", entry },
  };
```

Replace `web/src/room/rows/ActRow.tsx` entirely:

```tsx
import { ring } from "@/lib/streams";

export interface ActRowProps {
  /** 120 trigger (EV) · 210 reflex (RX) · 255 notification (NT). */
  hue: 120 | 210 | 255;
  text: string;
  meta: string;
  /** Present only on rows with a cause to trace: renders the `why?` button. */
  onWhy?: () => void;
}

/**
 * Something Alfred did while you were not asking. Same thread, quieter voice:
 * `fg2` at 14.5, hairlines above and below, and an 8 px mark in the stream's own
 * hue so which mind acted is legible at a glance (spec §5.2.4). The optional
 * `why?` is the handoff's: accent 13/500 with a 44 px hit area, pulled up and
 * out by its own padding so the row's rhythm does not change.
 */
export function ActRow({ hue, text, meta, onWhy }: ActRowProps) {
  return (
    <div
      className="flex items-start gap-3 py-2.5"
      style={{ borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)" }}
    >
      <span
        aria-hidden="true"
        data-testid="act-mark"
        data-hue={hue}
        className="mt-1.5 h-2 w-2 shrink-0 rounded-[2px]"
        style={{ background: ring(hue) }}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="t-row" style={{ color: "var(--fg2)" }}>
          {text}
        </div>
        <div className="t-meta">{meta}</div>
      </div>
      {onWhy ? (
        <button
          type="button"
          onClick={onWhy}
          className="-mt-2.5 -mr-1.5 h-11 shrink-0 border-0 bg-transparent px-2.5 text-[13px] font-medium"
          style={{ color: "var(--accent)" }}
        >
          why?
        </button>
      ) : null}
    </div>
  );
}
```

`web/src/room/Timeline.tsx` — the props, `Row`, and the map:

```tsx
import type { StreamRef } from "@/lib/streams";
```

```tsx
export interface TimelineProps {
  items: TimelineItem[];
  /** Non-null only on a first run with nothing in the thread at all. */
  firstDayGreeting: string | null;
  /** A reflex act row's `why?`. Without it no row offers one. */
  onWhy?: (ref: StreamRef) => void;
}

function Row({ item, onWhy }: { item: TimelineItem; onWhy?: (ref: StreamRef) => void }) {
  switch (item.kind) {
```

```tsx
    case "act": {
      const why = item.why;
      return (
        <ActRow
          hue={item.hue}
          text={item.text}
          meta={item.meta}
          onWhy={why && onWhy ? () => onWhy(why) : undefined}
        />
      );
    }
```

```tsx
export function Timeline({ items, firstDayGreeting, onWhy }: TimelineProps) {
```

```tsx
        {items.map((item) => (
          <Row key={item.id} item={item} onWhy={onWhy} />
        ))}
```

The `const why = item.why` is not decoration: inside the arrow function TypeScript no longer narrows `item.why`, so the closure needs its own `const`.

- [ ] **Step 5: One `ring` for the Room's marks too**

`web/src/sheets/HeldBackSheet.tsx` — import and the mark:

```tsx
import { ring } from "@/lib/streams";
```

```tsx
          <span
            aria-hidden="true"
            className="mt-1.5 h-2 w-2 shrink-0 rounded-[2px]"
            style={{ background: ring(255) }}
          />
```

`web/src/index.css` — the comment above the theme-independent `:root` block, which described the phase-1 literals:

```css
/* ---------------------------------------------------- theme-independent bits
   The easing curves, the font stacks, and the two viewport variables
   installViewportVars() overwrites on the same element. The stream hues
   (h = 30 + 45·i in the handoff) are not tokens: `ring(hue)` in lib/streams.ts
   paints them, from STREAM_INFO, wherever a stream needs its colour. */
```

- [ ] **Step 6: Mount it all in the Room**

`web/src/room/Room.tsx` — imports (keep the list alphabetical by path, as it is):

```tsx
import type { StreamRef } from "@/lib/streams";
import { WorkshopHandle } from "@/room/WorkshopHandle";
import { WhySheet } from "@/sheets/WhySheet";
import { Workshop } from "@/workshop/Workshop";
```

State, next to `sheetOpen`:

```tsx
  const [sheetOpen, setSheetOpen] = useState(false);
  const [workshopOpen, setWorkshopOpen] = useState(false);
  /** The row a `why?` was asked on — the Room's or the Workshop's. */
  const [why, setWhy] = useState<StreamRef | null>(null);
```

The timeline and the composer:

```tsx
      <Timeline
        items={room.items}
        firstDayGreeting={firstRun && room.items.length === 0 ? greetingFor(hour) : null}
        onWhy={setWhy}
      />
```

```tsx
      <Composer
        online={online}
        onSend={room.sendText}
        hold={
          <HoldToTalk
            signal={signal}
            online={online}
            onHoldingChange={setHolding}
            onAudio={room.sendAudio}
          />
        }
        handle={<WorkshopHandle onOpen={() => setWorkshopOpen(true)} />}
      />
```

After `<HeldBackSheet …/>`, before `<DoorLayer …/>` — the sheet is a portal, the Workshop a layer at z-10 under the sheets (z-20) and under the Door:

```tsx
      <HeldBackSheet open={sheetOpen} onClose={() => setSheetOpen(false)} />
      <WhySheet anchor={why} onClose={() => setWhy(null)} />

      <Workshop
        open={workshopOpen}
        onClose={() => setWorkshopOpen(false)}
        onWhy={setWhy}
      />
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd web && npx vitest run`
Expected: `Test Files  53 passed (53)`, `Tests  736 passed (736)` — the eight new tests are 2 (handle) + 2 (composer) + 2 (timeline) + 2 (app); the history change edits a test rather than adding one.

- [ ] **Step 8: Lint, typecheck and commit**

```bash
cd web && npm run lint && npx tsc -b
git add src/room/WorkshopHandle.tsx src/room/WorkshopHandle.test.tsx src/room/Composer.tsx src/room/Composer.test.tsx src/room/rows/ActRow.tsx src/room/Timeline.tsx src/room/Timeline.test.tsx src/lib/history.ts src/lib/history.test.ts src/room/Room.tsx src/sheets/HeldBackSheet.tsx src/index.css src/App.test.tsx
git commit -m "feat(web): the Room's handle, why? on reflex rows, Workshop and Why sheet mounted"
```

---

## Task 11: Docs, backlog and the phone checklist

No code. The two working-notes documents still say phase 1 is all there is; the phase-1 backlog carries two items this phase resolves or inherits; and the manual QA half of spec §7 needs a phase-2 script.

**Files:**
- Modify: `web/README.md`, `docs/web-frontend.md`, `docs/backlog/low/pwa-phase1-followups.md`
- Create: `docs/backlog/low/pwa-phase2-followups.md`, `docs/superpowers/qa/2026-09-10-pwa-phase2-ios-checklist.md`

- [ ] **Step 1: `web/README.md`**

The opening paragraph:

```markdown
The phone-first PWA that replaced the Mission Control SPA. One screen (the Room),
one interrupt (the Door), four identity gates over the top of them — and, under
the Room, the Workshop, where the house's streams are read as they happen.
```

The layout block:

```
src/lib/        no React: api, sockets, formatters, the presence physics,
                the action reducer, the slide maths, audio and recording,
                the stream catalogue (streams.ts), the feed reducer (feed.ts)
                and the causal thread (trace.ts)
src/shell/      providers and the two surfaces everything rises on
src/gates/      setup, sign-in, expired, denied — and the router between them
src/room/       the one screen: presence field, headline, timeline, composer,
                and the handle into the Workshop
src/door/       the approval interrupt: banner, fuse, slide, deep link
src/workshop/   the Workshop layer: bench switcher, the Activity bench and its hook
src/sheets/     the held-back queue and `Why Alfred did that`
src/test/       jsdom setup and shared fixtures
```

The status-vocabulary bullet under "Conventions":

```markdown
- The status vocabulary is closed (spec §10). The Room says `queued`, `applied`,
  `last true HH:MM` and `expired · not done`; the Workshop's status line adds the
  handoff's `live · N ev/s`, `paused · N new` and `last true HH:MM · not live`.
  `unknown since HH:MM`, `takes effect within 60 s`, `hot / cold` and
  `candidate · active · dormant · archived` are the phase-3 benches' words and arrive
  with them. Mono, lower case — the one exception is the Door's phase pill
  (`Confirmed · queued`, `Applied`, `Expired`, `Answered`), set in the layer's own type,
  ink on paper. Do not invent new words for system state.
```

Replace the "What phase 1 covers" section:

```markdown
## What phases 1 and 2 cover

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
```

The first bullet of "What it does not":

```markdown
- **Memory, Triggers and System** — phase 3. The switcher has their tabs; each says
  `not built yet · phase 3`.
```

Append to "Things worth knowing before you change something":

```markdown
- The Workshop keeps time by the Redis entry id (`idMs` in `lib/streams.ts`), never
  by `event.timestamp` — the id is the server's clock and has a zone; the ISO stamp
  has neither. The Room still reads `timestamp`, as it always has.
- Telemetry subscriptions are reference-counted (`TelemetrySocket.subscribe` /
  `unsubscribe`): the Door holds `home_action_results` for the app's lifetime and the
  Workshop holds all eight only while it is up. Always pair them.
- The causal thread (`lib/trace.ts`) is a client-side heuristic over one page per
  stream and a ±10-minute window; solid links are id joins, dashed links are
  adjacency in time, and the footnote says exactly what was searched. It never says
  "not caused by".
```

- [ ] **Step 2: `docs/web-frontend.md`**

The paragraph after "Client-side working notes" in the Overview:

```markdown
Phases 1 and 2 of six. The Memory, Triggers and System benches, the service worker,
icons, Web Push and the desktop composition are later phases — see
"What phases 1 and 2 do not cover" at the end.
```

In the directory map, add to `lib/` after `actions.ts` and `slide.ts`:

```
      streams.ts         # STREAMS, STREAM_INFO, ring, idMs/compareIds, fetchStreamPage,
                         # summarise — the eight streams and one line for any event
      feed.ts            # feedReducer, mergeRows, olderTargets — eight paged lists,
                         # pause/hold, the horizon; MAX_PER_STREAM
      trace.ts           # fetchThread, buildThread, nodeMeta — the causal thread
```

Change the `telemetry-socket.ts` line to:

```
      telemetry-socket.ts# TelemetrySocket over /ws/telemetry; subscriptions refcounted
```

Change the `Layer.tsx` line to:

```
      Layer.tsx              # Portal + rise in/out + focus trap + inert + level
                             # (workshop z-10 · layer z-30 · gate z-40)
```

Add to `room/` after `HoldToTalk.tsx`:

```
      WorkshopHandle.tsx # The 44px handle under the composer; hidden with the keyboard up
```

Change `sheets/` to:

```
    sheets/
      HeldBackSheet.tsx # ["deferred"] + drain
      WhySheet.tsx      # ["trace", stream, id] — the causal thread as a column
```

Add a `workshop/` block between `door/` and `sheets/`:

```
    workshop/      # The layer under the Room
      Workshop.tsx      # Layer level "workshop": ‹ Room, status line, switcher, benches
      BenchSwitcher.tsx # The four-tab segmented control (role="tablist")
      ActivityBench.tsx # Stale banner, chips, ↑ older, the list, the footer
      EventRow.tsx      # Monogram, line, meta; expanded payload and pills
      StreamChips.tsx   # Eight chips with counts; tap to solo
      useActivity.ts    # Head reads, the subscription, rehydrate on return, paging
```

In "Type scale", the `.t-monogram` clause becomes:

```markdown
`.t-label`, `.t-meta`, `.t-fuse`, `.t-status` — and `.t-monogram`, the Workshop's 9 px
stream monogram (`EventRow`, the Why sheet). The sizes outside the scale are written as
```

In "The closed status vocabulary", the sentence beginning `Phase 1 uses the first three`:

```markdown
`takes effect within 60 s`. The Room uses the first three and `expired · not done`; the
Workshop's status line adds the handoff's `live · N ev/s`, `paused · N new` and
`last true HH:MM · not live`; the rest are the phase-3 benches' and arrive with them. The
one exception to mono-and-lower-case is
```

In "The Room", the history bullet's `older turns are the Activity view's (phase 2)` becomes `older turns are the Activity bench's — open the Workshop`.

In "Telemetry (`/ws/telemetry`)", replace the opening paragraph and the `unsubscribe` note:

```markdown
Used by `TelemetrySocket` (`lib/telemetry-socket.ts`). Provides a live push of Redis
stream entries. The Door subscribes to one stream, `home_action_results`, for the app's
lifetime — it is what turns a queued approval into an applied one; the Workshop
subscribes to all eight while it is up and lets them go when it leaves.
```

```markdown
The server also takes `{"type": "unsubscribe", "streams": [...]}`. `TelemetrySocket`
counts wanters per stream: the frame goes out when a stream's count reaches zero, and
`subscribe` goes out only when it leaves zero — so the Workshop closing never takes the
Door's stream with it.
```

Insert a new section between "The Door" and "Routes":

```markdown
## The Workshop

A `Layer` at level `workshop` (z-10): under the sheets (z-20), the Door (z-30) and the
gates (z-40), over the Room. It is mounted in `Room.tsx` next to `DoorLayer`, opened by
the handle under the composer and closed by `‹ Room`. Its status line is the socket's
word first (`last true HH:MM · not live` while the telemetry socket is down), then the
feed's (`paused · N new`), then the overview's rate (`live · N ev/s`). Memory, Triggers
and System are tabs that say `not built yet · phase 3`.

**Activity** is `useActivity` over `feedReducer` (`lib/feed.ts`). Opening the bench
reads each stream's head page (`GET /api/admin/streams/{name}?count=50`) and subscribes
to all eight; `entry` frames are inserted at the top, or held while paused (`paused · N
new`, released on resume). Everything is keyed and ordered by the Redis id — `idMs`
is the millisecond half — never by `event.timestamp`. Each stream keeps its newest
`MAX_PER_STREAM` (400) entries; older ones fall off the bottom and the cursor moves up
to match.

**The horizon.** The list is a merge of up to eight independently paged streams. A
stream that can still page (`next_before` set) is only known back to its oldest loaded
entry, so rows from any stream older than the *shallowest* such stream are withheld —
otherwise one stream's past would sit next to another's silence and read as a quiet
spell. `↑ older` fetches the streams sitting at the horizon, and the button's label is
that cursor. Solo-ing a chip narrows the merge (and the horizon) to one stream.

**Rehydrate on return** (§4.10): the telemetry socket starts at `$` and replays nothing,
so `useActivity` re-reads every head page on `visibilitychange`. The reducer merges an
overlapping page in place and starts a stream over when the page does not reach the
entries it already had — a gap it cannot see across is not papered over.

**Causality.** A reflex act row in the Room (`why?`) or an RX row in Activity
(`Why · causal thread`) opens `WhySheet` with a `StreamRef`. `lib/trace.ts` reads one
page of 100 from every stream up to ten minutes after the observation
(`fetchThreadCandidates`), then `buildThread` joins from the observation outward on the
ids events actually carry — `event_id` (an observation's `trigger_event` is the
originating event's full dump), `request_id`, `session_id`, `trigger_id`, a reply's
`actions_taken` naming an action's tool, and an action's `entity_id` against a state
change within a minute — transitively through anything admitted. Up to six unjoined
entries within five seconds of a joined one are added as `adjacent in time only`, drawn
with a dashed connector. The footnote states what was searched (`searched 8 streams ·
100 entries each · ±10 min`, or `N of 8` when some read failed). The sheet never says
"not caused by" — the client cannot prove that. A server-side correlation id is the
follow-up (spec §7, `docs/backlog/low/pwa-phase2-followups.md`).
```

Rename and rewrite the closing section:

```markdown
## What phases 1 and 2 do not cover

- **Memory, Triggers and System** — phase 3. The Workshop's switcher has their tabs;
  each says `not built yet · phase 3`. Telemetry `status`/`error` frames still reach
  only the console until System exists.
- **Install, standalone and Reach gates, the service worker, icons and Web Push** —
  phases 4 and 5. `web/public/manifest.json` ships SVG only and carries the phase-1
  palette's dark ground; it cannot follow the theme the way `applyTheme` rewrites the
  `theme-color` meta, so a light-hour install gets a dark splash until phase 4 says
  otherwise.
- **Desktop** — phase 6. The client is phone-first and there is no wide composition.

Open follow-ups: `docs/backlog/low/pwa-phase1-followups.md` and
`docs/backlog/low/pwa-phase2-followups.md`.
```

- [ ] **Step 3: The backlog — phase 1's items this phase touches, and phase 2's own**

In `docs/backlog/low/pwa-phase1-followups.md`:

§3 — append to the paragraph: `Phase 2 added a third copy, ` `STREAMS` ` in ` `web/src/lib/streams.ts` `, and the chip order, monograms and hues hang off it; the acceptance stands.`

§4 — replace the body:

```markdown
From `web-activity-virtualized-list.md`. Phase 2 built the Activity bench with plain DOM
on purpose: `MAX_PER_STREAM = 400` per stream, 3 200 rows at the worst, and no
`@tanstack/react-virtual`. Carried to `pwa-phase2-followups.md` §1 with the numbers.
```

§6 — append after the list: `Phase 2 gives the first of these a screen again; the reconnect half is in ` `docs/superpowers/qa/2026-09-10-pwa-phase2-ios-checklist.md` `.`

§9 — append: `**Phase 2:** the Activity bench shows them — ` `notifications` ` is one of the eight streams, paged as far back as ` `↑ older` ` goes. Push (phase 5) is still the only thing that would *tell* you.`

Create `docs/backlog/low/pwa-phase2-followups.md`:

```markdown
# PWA phase 2 follow-ups

**Priority:** low
**Source:** `docs/superpowers/plans/2026-09-10-pwa-phase2-workshop-and-activity.md` —
the decisions it took knowingly and the limits it shipped with.

## 1. The Activity bench is plain DOM

Carried from `pwa-phase1-followups.md` §4. Each stream keeps its newest
`MAX_PER_STREAM = 400` entries (`web/src/lib/feed.ts`); with nothing solo'd that is up to
3 200 `EventRow`s mounted, each with its own expand state. Fine on an iPhone 15 with a
house that writes a few events a second; measure on the oldest phone in use before raising
the cap. **Acceptance:** scroll stays smooth with all eight streams full, or the list moves
to `@tanstack/react-virtual` with the expand state lifted out of the rows.

## 2. Causality is a client-side heuristic

Spec §7 says so; this is the ticket for the other half. `web/src/lib/trace.ts` joins on
ids the events carry (`event_id`, `request_id`, `session_id`, `trigger_id`,
`actions_taken`, `entity_id`) within a page of 100 per stream and a ±10-minute window,
and marks up to six nearby unjoined entries as `adjacent in time only`. It cannot see a
cause that is more than 100 entries back in a busy stream (`home_state` on a large
house), it cannot tell an entity's state change *caused by* an action from one that
merely followed it within a minute, and it never says "not caused by". **Acceptance:** the
bus stamps a `correlation_id` on every event a request fans out into
(`bus/schemas/events.py`, `core/reflex/runner.py`), `/api/admin/streams/{name}` can
filter by it, and the sheet's solid links become that id — the heuristic stays for
events from before the stamp.

## 3. Memory, Triggers and System are tabs that say so

`BenchSwitcher` has all four tabs; three render `not built yet · phase 3`. Phase 3's plan
replaces the placeholder in `Workshop.tsx`'s `WorkshopPanel` and takes the rest of spec
§10's vocabulary (`unknown since`, `takes effect within 60 s`, `hot / cold`,
`candidate · active · dormant · archived`) with it. Telemetry `status` / `error` frames
(`redis_error`, `invalid JSON`) reach only the console until System exists.

## 4. Stream names, again

`pwa-phase1-followups.md` §3, one copy larger: `STREAMS` in `web/src/lib/streams.ts` now
carries the chip order, the monograms and the hues, next to `ROOM_STREAMS` in
`history.ts` and `RESULT_STREAM` in `DoorProvider.tsx`. A rename in
`core/channels/stream_catalog.py` still fails silently on the client.

## 5. The Workshop's status rate is the overview's

`live · N ev/s` is `evs()` over the overview's `streams` counts, polled every 30 s — the
same number as the Room's status line, not a rate measured from the frames the feed is
receiving. Good enough to tell live from dead; not a throughput meter.
```

- [ ] **Step 4: The phone checklist**

Create `docs/superpowers/qa/2026-09-10-pwa-phase2-ios-checklist.md`:

```markdown
# PWA phase 2 — manual iOS checklist

Spec §7's manual half, for the Workshop and the Activity bench. The phase-1 checklist
(`2026-09-07-pwa-phase1-ios-checklist.md`) still applies to the Room and the Door; this
one is only what phase 2 added. Gate (spec §8): *debugging is usable on the phone*.

Run against the deployed build at `https://alfred.example.com`, signed in, with a house
that is running and writing events. Record the device, iOS version and date at the bottom.

## Before you start

- [ ] `npm run build` output is what is deployed (`git log -1` on the deploy host)
- [ ] Something in the house produces events on demand (flip a light; the
      `home_state` stream should move)
- [ ] A reflex observation with an action exists from today (start a media player
      with a reflex rule on it, or wait for one)

## The handle and the layer

- [ ] Under the composer: a small chevron, mono `workshop`, and the bar. The whole
      thing is one tap target at least 44 px tall
- [ ] Tap the field: the keyboard rises and the handle is **gone**, the composer sits
      on the keyboard with no empty strip below it. Dismiss the keyboard: the handle is
      back and the home-indicator gap is not doubled
- [ ] Tap the handle: the Workshop rises over the Room in about 400 ms and the Room
      cannot be tapped through it
- [ ] `‹ Room` closes it, the Room is exactly as it was left (scroll position, draft
      in the field)
- [ ] Reduce Motion on: the Workshop cross-fades in about 200 ms instead of rising
- [ ] With the Workshop up, produce a pending critical action: the Door rises **over**
      the Workshop; leaving it returns to the Workshop, not the Room

## The status line

- [ ] Reads `live · N ev/s` with the socket up (`N` may be `0.0` on a quiet house)
- [ ] Pause: reads `paused · 0 new`, then counts as events arrive
- [ ] Turn off Wi-Fi and mobile data: within a few seconds it reads
      `last true HH:MM · not live` with a real time, and the bench shows the stale
      banner `Feed stopped at HH:MM. Nothing below is live.` Turn the network on: both
      clear on their own, and rows arrive again
- [ ] Open the Workshop while already offline: the banner reads
      `Feed has not been live yet. Nothing below is live.`

## The bench

- [ ] Eight chips in the handoff's order (`UR AL EV AC RX NT HS HR`), each with a count
- [ ] Flip a light: an `HS` row appears at the top within a second, monogram in the
      stream's hue, line and meta in mono
- [ ] Tap a chip: only that stream's rows remain and the chip reads as selected; tap it
      again: all eight are back
- [ ] Tap a row: it expands to its payload, wrapped, never wider than the screen; the
      RX row carries `Why · causal thread`; tap again to collapse
- [ ] An observation the Reflex did not act on reads `observed … · watched, took no
      action` (spec §10) — the Room never shows these, the bench always does
- [ ] `Pause feed`: new rows stop appearing and the status counts them; `Resume`: they
      slot in at the top, newest first, none lost, none twice
- [ ] `↑ older` at the top of the list: its label is a cursor id; tapping appends
      older rows at the **bottom** and moves the label; the list does not jump to the
      top. It disappears when nothing can page further
- [ ] Solo a stream with three entries: no `↑ older` button
- [ ] A stream that has never been written reads `XX · 0 entries · nothing has been
      written` when solo'd
- [ ] Scroll the list to the bottom: the footer sits above the home indicator with a
      clear gap; the list does not bounce the whole layer
- [ ] Background the app for two minutes while the house is busy, return: the top of
      the list catches up on its own, no manual refresh; no row appears twice

## Why Alfred did that

- [ ] In the Room, a reflex act row (RX mark) carries `why?` at the right; a
      notification row (NT mark) does not
- [ ] Tap `why?`: the sheet rises **over** the Room in about 380 ms; its title is
      `Why Alfred did that`, then the intro, then `reading 8 streams…`, then the column
- [ ] The column: the state change that triggered it, the action, its result, and the
      observation — each with a monogram in its stream's hue, time to the second, and a
      meta line ending in how it was joined (`joined by request_id 4b1d` /
      `this row` / `adjacent in time only`); solid connectors between joined rows,
      dashed to and from adjacent ones
- [ ] The footnote reads `searched 8 streams · 100 entries each · ±10 min`
- [ ] `Done` and the scrim both close it; the Room is under it unchanged
- [ ] From the Workshop, expand an RX row and tap `Why · causal thread`: the same sheet
      opens **over the Workshop**; `Done` returns to the Workshop with the row still
      expanded
- [ ] Turn the network off, tap `why?`: the sheet shows the intro and a one-line
      failure (`Unreachable.` or the fetch error), no column, and still closes

## Nothing lies

- [ ] Nowhere does the Workshop show a word outside spec §10 and the handoff's status
      line (`live`, `paused`, `last true … · not live`)
- [ ] Nothing pretends to be a badge, a count of unread, or a throughput meter beyond
      `ev/s`
- [ ] No screen is reachable that has no way out

---

Device: ______________  iOS: ______  Build: ______________  Date: ____________
Tester: ______________
```

- [ ] **Step 5: Check and commit**

Run the runbook's two hygiene greps (Conventions, above) scoped with `-- docs web/README.md`. Expected: no output from either. Then:

```bash
git add web/README.md docs/web-frontend.md docs/backlog/low/pwa-phase1-followups.md docs/backlog/low/pwa-phase2-followups.md docs/superpowers/qa/2026-09-10-pwa-phase2-ios-checklist.md
git commit -m "docs(web): phase 2 — the Workshop, the Activity bench and the causal thread"
```

---

## Task 12: The whole branch, green, and the PR body

No new code. The branch is verified end to end and a PR body is drafted for the user — **do not push, do not open the PR**; the user decides when.

- [ ] **Step 1: Everything the CI job runs, in its order**

```bash
cd ~/code/.worktrees/alfred/pwa-phase2-activity/web
npm run lint && npx vitest run && npm run build
```

Expected: eslint prints nothing; `Test Files  53 passed (53)`, `Tests  736 passed (736)`; `✓ built in …` with no TypeScript errors. A `tsc -b` error here that Step 8 of a task missed is that task's bug — fix it in a new commit on that task's file, never `--amend` and never `--force`.

- [ ] **Step 2: The Python side is untouched**

```bash
cd ~/code/.worktrees/alfred/pwa-phase2-activity
git diff --stat origin/master -- . ':!web' ':!docs'
```

Expected: empty. Phase 2 is client and docs only; if anything under `core/`, `bus/` or `tests/` shows here, it was not part of the plan.

- [ ] **Step 3: The public-repo checks, over the whole tree**

Run the runbook's two hygiene greps (Conventions, above) over the whole tree, from `~/code/.worktrees/alfred/pwa-phase2-activity`. Expected: both empty. The second catches the escaped forms the first does not.

- [ ] **Step 4: The branch reads as the plan**

```bash
git log --oneline origin/master..HEAD
```

Expected, oldest first: the plan commit, then one commit per task in this order — plus any review-fix commits after the task they fix:

```
docs(web): plan PWA phase 2 — Workshop shell, Activity bench, causal thread
feat(web): name the eight streams, read a page of one, summarise any event
feat(web): feed reducer — merged pages, live frames, pause, one horizon
feat(web): count telemetry subscriptions per stream; workshop layer level
feat(web): useActivity — eight head pages, live frames, solo, pause, older
feat(web): Activity event row and stream chips
feat(web): Activity bench — banner, list with older button, chips, pause
feat(web): Workshop layer with bench switcher and Activity
feat(web): trace — join a reflex observation to what shares its ids
feat(web): Why sheet — the causal thread as a column
feat(web): the Room's handle, why? on reflex rows, Workshop and Why sheet mounted
docs(web): phase 2 — the Workshop, the Activity bench and the causal thread
```

- [ ] **Step 5: Draft the PR body — paste it in chat, do not open the PR**

```markdown
## Summary

PWA phase 2 (spec §8): the Workshop and the Activity bench.

- **The handle and the Workshop.** A 44 px handle under the composer (hidden with the
  keyboard up) opens a new `Layer` level (`workshop`, z-10) under the sheets, the Door
  and the gates. `‹ Room`, a status line that says `last true HH:MM · not live` before
  anything else, and the four-bench switcher — Memory, Triggers and System say
  `not built yet · phase 3`.
- **Activity.** All eight streams live, newest first, keyed by the Redis id: chips to
  solo one, pause/resume with a held count, `↑ older` paging by cursor, rows that expand
  to their payload. A pure reducer (`lib/feed.ts`) owns the merge and its *horizon* —
  rows older than the shallowest still-pageable stream are withheld, so the interleave
  never reads a gap as a quiet spell. Head pages are re-read on return (§4.10).
  Telemetry subscriptions are now reference-counted so the Workshop leaving never takes
  the Door's stream with it.
- **Why Alfred did that.** `why?` on the Room's reflex rows and `Why · causal thread` on
  RX rows open a sheet with the causal thread: `lib/trace.ts` reads a page per stream
  around the observation and joins on the ids events carry (`event_id`, `request_id`,
  `session_id`, `trigger_id`, `actions_taken`, `entity_id`), transitively; unjoined
  neighbours in time are dashed `adjacent in time only`. The footnote states what was
  searched. It never claims "not caused by" — a server-side correlation id is the
  follow-up (backlog).

Deviations from the handoff are listed in the plan
(`docs/superpowers/plans/2026-09-10-pwa-phase2-workshop-and-activity.md`, "Deviations")
— each one is the client refusing to print something it cannot vouch for.

## Test plan

- [ ] `cd web && npm run lint && npx vitest run && npm run build` — 53 files / 736 tests
- [ ] Both public-repo greps (regex and `-F`) empty
- [ ] Phone: `docs/superpowers/qa/2026-09-10-pwa-phase2-ios-checklist.md`
- [ ] Phase-1 checklist's Room and Door sections still pass (the composer changed)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Then report, in chat: the counts, the two grep results, and that the branch is ready for the user to push.
