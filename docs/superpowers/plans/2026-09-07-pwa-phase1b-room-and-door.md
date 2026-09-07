# PWA Phase 1b — The Room and the Door

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the shell that `docs/superpowers/plans/2026-09-07-pwa-phase1a-shell-and-gates.md` built. Tasks 15–28 add Alfred's presence field, the headline and status rows, the merged timeline with history, the composer and hold-to-talk, notifications and the Held-back sheet, and the Door — banner, fuse, slide-to-confirm, tombstones and the `/actions/:id` deep link — then rewrite `web/README.md`, write the iOS QA checklist and open the phase 1 PR.

**Architecture:** Everything on screen is one timeline. `useRoomHistory` reads four Redis stream pages once and turns them into `TimelineItem`s; `useRoom` holds the live ones (what you sent, what Alfred said, what he did while you watched) and merges the two lists by timestamp. Nothing in the Room polls except the overview. The Door is a separate reducer over `TrackedAction[]` fed from three independent places — a `["pending-actions"]` read, a chat `notification` frame carrying `metadata.pending_action_id`, and the telemetry socket's `home_action_results` stream — so a pending approval survives a cold launch, a notification tap and a suspended app. Confirmation is a POST; **applied** is only ever the telemetry stream saying so.

**Tech Stack:** Unchanged from 1a. Vite 8 · React 19 · TypeScript 6 · Tailwind v4 · TanStack Query 5 · react-router 7 · Vitest 4 + Testing Library + jsdom · ESLint 10. No new dependencies: the presence field is a `<canvas>`, the fuse is a `conic-gradient`, and the slide is two pointer handlers.

**Spec:** `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md` — §4 (the twelve iOS constraints), §5.1 (conversation, notifications, confirmations), §5.2 (honesty), §7, §10.
**Design handoff:** `docs/design/2026-09-04-pwa-client-handoff/README.md` (tokens, type scale, copy — final) and `Alfred.dc.html` (`signal()`, `presence()`, `slideMove()`, `holdEnd()` — the maths, and the source for it).
**Predecessor:** `docs/superpowers/plans/2026-09-07-pwa-phase1a-shell-and-gates.md`. Every name it lists under "The contract 1a hands to 1b" is used here exactly as spelled there.

---

## Product decisions already taken (do not reopen)

1. **1a's decisions all still hold** — hard cut, no Workshop, no PWA plumbing, plain-text replies, 401/403 as gates, public repo (`alfred.example.com`, `192.168.1.x`, never a real address).
2. **No `why?` buttons anywhere.** The act row in the handoff has an optional `why?` affordance; it opens the causal-thread sheet, which is Phase 2. Act rows in this plan render mark, text and meta only.
3. **Alfred's replies are plain text.** `white-space: pre-line`, never markdown.
4. **Door "Applied" comes from the telemetry socket**, subscribed to `home_action_results` only. A `200` from `POST /api/actions/{id}/confirm` means **queued**, never applied — the endpoint republishes the action and returns before anything runs.
5. **The Door title is derived, not sent.** `title = humaniseTool(tool_name)` (`home.lock_unlock` → `Lock unlock`); the reason paragraph is `action.reason` when the conscious engine supplied one, else the sentence the confirmation notification already uses; the raw-call box is `rawCall(tool_name, parameters)`.
6. **Timeline history is four stream pages**, merged by `timestamp`: `user_requests`, `user_responses`, `reflex_observations` (only entries whose `action` is not null — spec §10, "The Room renders only reflex observations that carry an `action`") and `notifications` (only entries whose `metadata.pending_action_id` is absent — a confirmation request belongs to the Door, not the thread).
7. **The presence field is ported, not reinvented.** `lib/presence-signal.ts` and `room/PresenceField.tsx` carry `signal()` and `presence()` from `Alfred.dc.html` line for line, including every magic constant.

### Deviations from the handoff (deliberate; flag them in review)

| # | Where | Handoff says | This plan ships | Why |
|---|---|---|---|---|
| 1 | Room headline | `Quiet until 08:30.` | adds `Quiet until further notice.` | DND with `until: null` never auto-drains (spec §5.2.6). 1a fixed this string; 1b implements it. |
| 2 | Headline priority | first-day greeting wins over everything | offline and reconnecting win over the greeting | An unreachable house on its first morning must say `Unreachable.`, not `Good morning, sir.` The prototype has no real socket, so the question never arose. |
| 3 | Act row (live notification) | meta `HH:MM · {source} · {urgency}` | live frames read `HH:MM · live · {urgency}` | The `/ws` notification frame carries no `source` (`core/notifications/adapters/websocket.py`). `live` says truthfully where the row came from; the same notification re-read from the stream later shows its real source. |
| 4 | Door state pill | `Pending` · `Confirmed · queued` · `Applied` · `Expired` | adds `Answered` | The handoff's own "already consumed (404 on confirm)" case needs a word. `Answered` with the paper-muted dot, matching its tombstone. |
| 5 | Alfred row meta | `mood · actions_taken · HH:MM` | error replies read `error · HH:MM` | An error frame has no mood and ran no tools; printing `neutral · no tools` for it would be a small lie. |
| 6 | Thinking row meta | `conscious mind · calendar.today running` | `conscious mind · working` | The client cannot know which tools a turn will run until the reply arrives with `actions_taken`. |
| 7 | Fuse ring | 300 s TTL | `ttl_seconds` from the server | `pending_action_payload` returns the real TTL; hard-coding 300 would misdraw a shorter one. The expiry sentence still spells the number out (`five` / `ten` / `{n}` minutes). |
| 8 | `signal()` band loop | `s / (hi - lo) / 255` | `s / Math.max(1, hi - lo) / 255` | With `fftSize 256` the top band is empty (`lo` and `hi` both 128) and the prototype divides by zero, turning `level` and every dot coordinate into `NaN` the instant a real microphone is attached. Identical for every band that has width. Task 15 explains it in full. |
| 9 | Door foot, queued | `Sent to Home Assistant. Waiting for the lock to report (request a91f).` | `… Waiting for it to report (request a91f).` | The same Door renders a light, a media player or a lock; "the lock" is only right for the handoff's own example. One word changed, nothing else. |
| 10 | Slide hint | `Slide to unlock the door` | `Slide to confirm` | Same reason as row 9: the hint is rendered for every tool, not only the front door. |

---

## Before you start

1. **Plan 1a must be finished, in this same worktree, on this same branch.** 1b adds commits after 1a's last one; it does not branch.

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
git log --oneline origin/master..HEAD | wc -l
git log --oneline -1
```

Expected: `14`, and `feat(web): compose the shell — providers, routes and the room's frame`. Anything else means 1a is incomplete — finish it first; every task below imports something 1a defines.

2. **Confirm the shell is green before adding to it.**

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client/web
npm run lint && npm test && npm run build
```

Expected: eslint prints nothing, `Test Files  21 passed (21)`, vite prints `✓ built in …`. A red baseline is 1a's problem, not yours.

3. **How to run things** (all from `web/`, except the SPA gate):

| Command | What it does |
|---|---|
| `npm test` | The whole vitest suite, once |
| `npm test -- src/lib/history.test.ts` | One file |
| `npm test -- -t "expires a pending action"` | One test by name |
| `npm run lint` | ESLint over `web/` |
| `npm run build` | `tsc -b` then `vite build` — the type check lives here, not in lint |
| `cd .. && uv run pytest tests/core/channels/test_spa_ci.py -q` | The CI gate that serves the built `web/dist` |

4. **Test-file count as you go.** 1a ends at 21 files. Each task below states the count it should reach, so a missing or duplicated file is caught the moment it happens: 15 → 23 · 16 → 25 · 17 → 26 · 18 → 27 · 19 → 28 · 20 → 29 · 21 → 31 · 22 → 32 · 23 → 33 · 24 → 35 · 25 → 36 · 26 → 38 · 27 → 39 · 28 → 39.

5. **The backend this plan calls** — all of it is on `origin/master`; check if anything 404s at runtime:

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
git show origin/master:core/channels/web_server.py | grep -n 'api/actions'
git show origin/master:core/channels/admin_api.py | grep -n 'notifications/deferred\|notifications/drain\|streams/{name}'
git show origin/master:core/channels/stream_catalog.py | grep -n '"user_requests"\|"home_action_results"'
```

Expected: three `/api/actions/…` routes, the two notification routes and the stream route, and both stream names in the catalog.

---

## Conventions

Identical to 1a, repeated here because they are load-bearing:

- **TDD, always.** Failing test → run it and read the failure → implement → run it green → commit.
- **Conventional commits**, one per task minimum. The `pr-title` CI job enforces the same shape on the PR title.
- **Imports use the `@/` alias** everywhere except inside `src/lib/`, where siblings are relative (`./format`, `./types`).
- **Named exports** everywhere except `App.tsx`.
- **Colours come from CSS custom properties** — `style={{ color: "var(--muted)" }}` or a `@theme inline` utility (`bg-surface`, `text-fg2`). Never a Tailwind palette colour.
- **Type comes from the `.t-*` classes.** Sizes outside the scale (13 px note, 10.5 px unsent stamp, 11.5 px raw call) are written as explicit arbitrary values.
- **Tests live next to the source.** One test file may cover a small family that ships together (as 1a's `Layer.test.tsx` covers `Sheet`); that is stated per task.
- **Each test file builds its own providers** — a fresh `QueryClient` per file, no shared render helper.
- **`react-refresh/only-export-components`** fires on a file exporting a component *and* a hook. Silence it per line, exactly as 1a does.
- **The status vocabulary is closed** (handoff): `queued`, `applied`, `last true HH:MM`, `unknown since HH:MM`, `hot / cold`, `candidate · active · dormant · archived`, `expired · not done`, `takes effect within 60 s`. Always mono, always lower case.
- **No placeholders, no dead code.**

---

## File Structure

Everything under `web/` unless noted. Files 1a created and 1b only reads are not listed.

| File | Change | Responsibility |
|---|---|---|
| `src/lib/presence-signal.ts` | Create (15) | `PresenceSignal` — the ported `signal()` envelopes |
| `src/lib/headline.ts` | Create (16) | `pickHeadline(input)` — the whole priority table, pure |
| `src/lib/history.ts` | Create (17) | `TimelineItem`, `fetchRoomHistory`, `toTimelineItems`, `withDividers`, `pendingActionTitles` |
| `src/lib/audio.ts` | Rewrite (21) | One unlocked `AudioContext`; `installAudioUnlock`, `getAudioContext`, `playWavBase64` |
| `src/lib/recorder.ts` | Create (21) | `pickMimeType`, `Recorder`, `blobToDataUrl` |
| `src/lib/actions.ts` | Create (24), modify (25) | `actionReducer`, `fuseRemaining`, the three fetches, `tombstoneItems` |
| `src/lib/slide.ts` | Create (26) | `CONFIRM_RATIO`, `slideKnob`, `hintOpacity` |
| `src/room/PresenceField.tsx` | Create (15) | The 393×190 canvas, the ported `presence()` |
| `src/room/Headline.tsx` | Create (16) | 26 px headline |
| `src/room/OfflineNote.tsx` | Create (16) | Offline / reconnecting note |
| `src/room/DndRow.tsx` | Create (16) | DND row + `{n} held ›` |
| `src/room/useOverview.ts` | Modify (16) | adds `isFirstRun(overview)` |
| `src/room/StatusLine.tsx` | Modify (16) | reads `isFirstRun` instead of its inline copy |
| `src/room/useRoomHistory.ts` | Create (17) | `["room-history"]` |
| `src/room/rows/*.tsx` | Create (18) | `Divider`, `YouBubble`, `AlfredRow`, `ActRow`, `Tombstone`, `TranscribingBubble`, `ThinkingRow`, `FirstDay` |
| `src/room/Timeline.tsx` | Create (18) | The list, and the scroll anchoring |
| `src/room/useRoom.ts` | Create (19) | Live timeline state, send, the unsent queue, the no-reply timeout |
| `src/room/Composer.tsx` | Create (20) | 50 px field, send / hold slot, keyboard padding |
| `src/room/HoldToTalk.tsx` | Create (22) | Pointer hold, mic, caption, bars |
| `src/room/Room.tsx` | Rewrite (28) | Composes all of it |
| `src/sheets/HeldBackSheet.tsx` | Create (23) | `["deferred"]` + drain |
| `src/door/DoorProvider.tsx` | Create (24) | `useDoor()` |
| `src/door/FuseRing.tsx` | Create (25) | 34 px and 168 px conic ring |
| `src/door/DoorBanner.tsx` | Create (25) | The ink banner above the composer |
| `src/door/DoorLayer.tsx` | Create (26) | The full inverted layer |
| `src/door/SlideToConfirm.tsx` | Create (26) | Slide with travel |
| `src/door/useActionRoute.ts` | Create (27) | `/actions/:id` |
| `src/index.css` | Modify (20, 25) | `.keyboard-up` padding rule; the fuse arc and its two masks |
| `src/App.tsx`, `src/main.tsx` | Modify (28) | `DoorProvider`, `installAudioUnlock()` |
| `src/App.test.tsx` | Modify (28) | The room it boots into is now the real one |
| `src/test/setup.ts` | Modify (22) | `PointerEvent`, pointer capture |
| `src/test/fixtures.ts` | Modify (17, 23, 24) | Stream pages, deferred queue, pending actions, result frames |
| `web/README.md` | Rewrite (28) | What this client is and how to work on it |
| `docs/superpowers/qa/2026-09-07-pwa-phase1-ios-checklist.md` | Create (28) | Every spec §4 constraint → a step on a real phone |

---

## What 1b adds to the contract

These names do not exist until the task in brackets creates them. Nothing later may rename them.

```ts
// src/lib/history.ts (Task 17)
export type TimelineItem =
  | { kind: "divider"; id: string; at: string; label: string }
  | { kind: "you"; id: string; at: string; text: string; state: "sent" | "unsent" }
  | { kind: "alfred"; id: string; at: string; text: string; mood?: Mood; actions: string[]; error?: boolean }
  | { kind: "act"; id: string; at: string; hue: 120 | 210 | 255; text: string; meta: string }
  | { kind: "tombstone"; id: string; at: string; title: string; meta: string }
  | { kind: "transcribing"; id: string; at: string; seconds: number }
  | { kind: "thinking"; id: string; at: string; detail: string };

export const ROOM_STREAMS = ["user_requests", "user_responses", "reflex_observations", "notifications"] as const;
export type RoomStream = (typeof ROOM_STREAMS)[number];
export type RoomHistory = Record<RoomStream, StreamEntry[]>;
export function fetchRoomHistory(): Promise<RoomHistory>;
export function toTimelineItems(history: RoomHistory): TimelineItem[];
export function withDividers(items: TimelineItem[], now: Date): TimelineItem[];
export function pendingActionTitles(history: RoomHistory | undefined): Record<string, string>;

// src/lib/actions.ts (Tasks 24, 25)
export type ActionPhase = "pending" | "queued" | "applied" | "expired" | "answered";
export interface TrackedAction { action: PendingAction; phase: ActionPhase; confirmedAt?: string; appliedAt?: string; result?: ActionResultEvent }
export type ActionEvent =
  | { type: "loaded"; actions: PendingAction[] }
  | { type: "arrived"; action: PendingAction }
  | { type: "confirm-sent"; id: string; at: string }
  | { type: "confirm-404"; id: string }
  | { type: "result"; result: ActionResultEvent; at: string }
  | { type: "tick"; now: number };
export function actionReducer(state: TrackedAction[], ev: ActionEvent): TrackedAction[];
export function fuseRemaining(action: PendingAction, now: number): number;
export function tombstoneItems(actions: TrackedAction[]): TimelineItem[];
export function fetchPending(): Promise<PendingAction[]>;
export function fetchAction(id: string): Promise<PendingAction>;
export function confirmAction(id: string): Promise<void>;

// src/lib/slide.ts (Task 26)
export const CONFIRM_RATIO = 0.85;
export function slideKnob(dx: number, max: number): number;
export function hintOpacity(knob: number, max: number): number;

// src/lib/headline.ts (Task 16)
export interface HeadlineInput { online: boolean; reconnecting: boolean; firstRun: boolean; dnd: { active: boolean; until?: string | null }; holding: boolean; busy: boolean; hour: number }
export function greetingFor(hour: number): string;
export function pickHeadline(input: HeadlineInput): string;

// src/lib/presence-signal.ts (Task 15)
export interface SignalFrame { level: number; bands: number[]; think: number }
export class PresenceSignal {
  attach(analyser: AnalyserNode | null): void;
  setHolding(holding: boolean): void;
  setThinking(thinking: boolean): void;
  setOffline(offline: boolean): void;
  tick(nowSeconds: number): SignalFrame;
}

// src/lib/audio.ts (Task 21) · src/lib/recorder.ts (Task 21)
export function installAudioUnlock(): () => void;
export function getAudioContext(): AudioContext | null;
export function playWavBase64(base64: string): void;
export const RECORDER_MIME_TYPES: readonly string[];
export function pickMimeType(isSupported?: (type: string) => boolean): string;
export interface Recording { blob: Blob; mimeType: string; durationMs: number }
export class Recorder { analyser: AnalyserNode | null; start(): Promise<void>; stop(): Promise<Recording | null> }
export function blobToDataUrl(blob: Blob): Promise<string>;

// src/room/useRoom.ts (Task 19)
export const UNSENT_KEY = "alfred.unsent";
export const NO_REPLY_MS = 60_000;
export interface UseRoomOptions { history: TimelineItem[]; tombstones?: TimelineItem[] }
export interface RoomValue { items: TimelineItem[]; thinking: boolean; sendText: (text: string) => void; sendAudio: (dataUrl: string, seconds: number) => void }
export function useRoom(options: UseRoomOptions): RoomValue;

// src/door/DoorProvider.tsx (Task 24)
export interface DoorValue {
  actions: TrackedAction[];
  pending: TrackedAction[];
  current: TrackedAction | null;
  open: boolean;
  openAction: (id: string) => void;
  close: () => void;
  confirm: (id: string) => void;
  arrived: (action: PendingAction) => void;
  now: number;
}
export function useDoor(): DoorValue;
```

New `localStorage` key: `alfred.unsent` (a `TimelineItem[]` of unsent `you` rows).
New query keys: `["room-history"]`, `["deferred"]`, `["pending-actions"]`.

---

### Task 15: Alfred's presence — the signal and the field

The dot field is the only thing in the app that is *Alfred* rather than a report about him. It is a height field over a 12 pt dot grid: at rest `z = 0` and it is perfectly still; while you hold the button it is driven by an eight-band audio envelope; while he is thinking a Gaussian pulse sweeps down it. All of that is `signal()` and `presence()` in `Alfred.dc.html` (lines ~597–637), and this task ports them constant for constant.

The split is deliberate. `PresenceSignal` is the physics — envelopes, attack and decay asymmetry, the thinking ramp — and has no DOM in it, so it can be tested. `PresenceField` is the drawing, and holds only the phase accumulators the canvas needs.

**One correction to the prototype, and only one.** `signal()`'s band loop computes `lo = 1 << k`, `hi = min(freq.length, 2 << k)`. With `fftSize = 256`, `frequencyBinCount` is 128, so at `k = 7` both are 128 and the band width is zero: `s / 0 / 255` is `NaN`, `Math.min(1, NaN)` is `NaN`, and the `NaN` propagates through `sum`, `target`, `level` and every dot coordinate — the field goes blank the moment a real microphone is attached. The port divides by `Math.max(1, hi - lo)`, which is identical for every band that has any width and yields `0` for the empty one. Nothing else in the maths is touched.

**Files:**
- Create: `web/src/lib/presence-signal.ts`, `web/src/lib/presence-signal.test.ts`
- Create: `web/src/room/PresenceField.tsx`, `web/src/room/PresenceField.test.tsx`

- [ ] **Step 1: Write the failing signal test**

Create `web/src/lib/presence-signal.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { PresenceSignal } from "./presence-signal";

/** An AnalyserNode stand-in that reports a fixed spectrum. */
function fakeAnalyser(fill: number, bins = 128): AnalyserNode {
  return {
    frequencyBinCount: bins,
    getByteFrequencyData: (array: Uint8Array) => array.fill(fill),
  } as unknown as AnalyserNode;
}

/** Run `count` ticks at 60 fps starting from `from`, returning the last frame. */
function run(signal: PresenceSignal, count: number, from = 0) {
  let frame = signal.tick(from);
  for (let i = 1; i <= count; i++) frame = signal.tick(from + i / 60);
  return frame;
}

describe("PresenceSignal at rest", () => {
  it("is perfectly still", () => {
    const signal = new PresenceSignal();
    const frame = run(signal, 120);
    expect(frame.level).toBe(0);
    expect(frame.think).toBe(0);
    expect(frame.bands).toHaveLength(8);
    expect(frame.bands.every((band) => band === 0)).toBe(true);
  });
});

describe("PresenceSignal while holding", () => {
  it("synthesizes a talking envelope with no analyser attached", () => {
    const signal = new PresenceSignal();
    signal.setHolding(true);
    const frame = run(signal, 60);
    expect(frame.level).toBeGreaterThan(0);
    expect(frame.level).toBeLessThanOrEqual(1);
  });

  it("keeps every band inside the unit range", () => {
    const signal = new PresenceSignal();
    signal.setHolding(true);
    for (let i = 0; i < 300; i++) {
      const frame = signal.tick(i / 60);
      for (const band of frame.bands) {
        expect(Number.isFinite(band)).toBe(true);
        expect(band).toBeGreaterThanOrEqual(0);
        expect(band).toBeLessThanOrEqual(1);
      }
    }
  });

  it("rises faster than it falls", () => {
    const rise = new PresenceSignal();
    rise.setHolding(true);
    const afterRise = run(rise, 30).level;

    rise.setHolding(false);
    const afterOneFall = rise.tick(31 / 60).level;

    // Attack 0.35 per tick, decay 0.06: one tick of silence must not undo
    // half a second of speech.
    expect(afterOneFall).toBeGreaterThan(afterRise * 0.9);
  });

  it("decays to a hard zero once released", () => {
    const signal = new PresenceSignal();
    signal.setHolding(true);
    run(signal, 60);
    signal.setHolding(false);
    const frame = run(signal, 400, 1);
    expect(frame.level).toBe(0);
  });
});

describe("PresenceSignal with an analyser", () => {
  it("reads the live spectrum instead of synthesizing one", () => {
    const signal = new PresenceSignal();
    signal.attach(fakeAnalyser(200));
    signal.setHolding(true);
    const frame = run(signal, 60);
    expect(frame.level).toBeGreaterThan(0.5);
  });

  it("survives the zero-width top band that fftSize 256 produces", () => {
    // frequencyBinCount 128 makes band 7 empty (lo 128, hi 128). The prototype
    // divides by that zero and turns the whole field into NaN.
    const signal = new PresenceSignal();
    signal.attach(fakeAnalyser(255, 128));
    signal.setHolding(true);
    const frame = run(signal, 60);
    expect(Number.isNaN(frame.level)).toBe(false);
    expect(frame.bands.every((band) => Number.isFinite(band))).toBe(true);
  });

  it("goes back to the synthesized envelope when the analyser is detached", () => {
    const signal = new PresenceSignal();
    signal.attach(fakeAnalyser(255));
    signal.setHolding(true);
    run(signal, 30);

    signal.attach(null);
    const frame = run(signal, 30, 1);

    expect(Number.isFinite(frame.level)).toBe(true);
    expect(frame.level).toBeGreaterThan(0);
  });

  it("ignores the analyser when not holding", () => {
    const signal = new PresenceSignal();
    signal.attach(fakeAnalyser(255));
    const frame = run(signal, 200);
    expect(frame.level).toBe(0);
  });
});

describe("PresenceSignal while thinking", () => {
  it("eases in and rises monotonically", () => {
    const signal = new PresenceSignal();
    signal.setThinking(true);
    let previous = -1;
    for (let i = 0; i < 90; i++) {
      const frame = signal.tick(i / 60);
      expect(frame.think).toBeGreaterThanOrEqual(previous);
      previous = frame.think;
    }
    expect(previous).toBeGreaterThan(0.4);
    expect(previous).toBeLessThan(1);
  });

  it("eases out more slowly than it eased in", () => {
    const signal = new PresenceSignal();
    signal.setThinking(true);
    const peak = run(signal, 90).think;

    signal.setThinking(false);
    const afterTen = run(signal, 10, 2).think;

    // In 0.08, out 0.035 — the field settles rather than snaps.
    expect(afterTen).toBeGreaterThan(peak * 0.6);
  });

  it("is independent of the audio level", () => {
    const signal = new PresenceSignal();
    signal.setThinking(true);
    const frame = run(signal, 60);
    expect(frame.level).toBe(0);
    expect(frame.think).toBeGreaterThan(0);
  });
});

describe("PresenceSignal offline", () => {
  it("hands out a flat field however loud it is", () => {
    const signal = new PresenceSignal();
    signal.setHolding(true);
    signal.setThinking(true);
    run(signal, 60);

    signal.setOffline(true);
    const frame = signal.tick(2);

    expect(frame.level).toBe(0);
    expect(frame.think).toBe(0);
    expect(frame.bands.every((band) => band === 0)).toBe(true);
  });

  it("comes back without a jump, because the envelopes kept running", () => {
    const signal = new PresenceSignal();
    signal.setHolding(true);
    signal.setOffline(true);
    run(signal, 60);

    signal.setOffline(false);
    const frame = signal.tick(2);

    expect(frame.level).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/lib/presence-signal.test.ts`
Expected: FAIL — `Failed to resolve import "./presence-signal"`. The module does not exist.

- [ ] **Step 3: Write `web/src/lib/presence-signal.ts`**

Complete file:

```ts
/**
 * The presence field's physics, ported from `Alfred.dc.html`'s `signal()`.
 *
 * Three envelopes over one shared clock:
 *  - `level`  — overall loudness, attack 0.35 / decay 0.06 per tick, snapped to
 *               a hard zero below 0.004 so the field is *perfectly* still at rest.
 *  - `bands`  — eight octave bands, either read from a live AnalyserNode or
 *               synthesized from three sines when there is no microphone.
 *  - `think`  — the thinking ramp, in 0.08 / out 0.035, so a reply settles the
 *               field instead of snapping it flat.
 *
 * No DOM, no rAF, no React: the caller ticks it once per frame with a seconds
 * clock and draws whatever comes back.
 */

const BAND_COUNT = 8;

export interface SignalFrame {
  /** 0–1 overall audio level. */
  level: number;
  /** Eight band levels, 0–1, low to high. The same array every tick — copy it if you keep it. */
  bands: number[];
  /** 0–1 thinking envelope. */
  think: number;
}

export class PresenceSignal {
  private analyser: AnalyserNode | null = null;
  private freq: Uint8Array<ArrayBuffer> | null = null;
  private bands = new Float32Array(BAND_COUNT);
  private out: number[] = new Array<number>(BAND_COUNT).fill(0);
  private level = 0;
  private think = 0;
  private holding = false;
  private thinking = false;
  private offline = false;

  /** Attach the recorder's analyser while holding; pass null on release. */
  attach(analyser: AnalyserNode | null): void {
    this.analyser = analyser;
    this.freq = analyser ? new Uint8Array(analyser.frequencyBinCount) : null;
  }

  setHolding(holding: boolean): void {
    this.holding = holding;
  }

  setThinking(thinking: boolean): void {
    this.thinking = thinking;
  }

  setOffline(offline: boolean): void {
    this.offline = offline;
  }

  /** Advance every envelope by one frame. `now` is a seconds clock, not milliseconds. */
  tick(now: number): SignalFrame {
    const b = this.bands;
    let target = 0;
    let live = false;

    if (this.analyser && this.freq && this.holding) {
      this.analyser.getByteFrequencyData(this.freq);
      let sum = 0;
      for (let k = 0; k < BAND_COUNT; k++) {
        const lo = 1 << k;
        const hi = Math.min(this.freq.length, 2 << k);
        let s = 0;
        for (let i = lo; i < hi; i++) s += this.freq[i] ?? 0;
        // max(1, …): with fftSize 256 the top band is empty (lo 128, hi 128) and
        // the prototype's division by zero turns the entire field into NaN.
        const width = Math.max(1, hi - lo);
        const v = Math.min(1, (s / width / 255) * 1.6);
        b[k] += (v - b[k]) * (v > b[k] ? 0.55 : 0.14);
        sum += v;
      }
      target = Math.min(1, sum / 5);
      live = true;
    }

    if (!live) {
      const n = (f: number, p: number) => 0.5 + 0.5 * Math.sin(now * f + p);
      if (this.holding) {
        // Speech-shaped: two fast sines beating against each other, gated by a
        // slow one so the envelope breathes instead of buzzing.
        const talk = n(5.1, 0) * n(7.7, 1.3) * 0.6 + n(1.9, 2) * 0.5;
        const pause = n(0.45, 0) > 0.28 ? 1 : 0.15;
        target = Math.min(1, 0.15 + talk * pause);
      } else {
        target = 0;
      }
      for (let k = 0; k < BAND_COUNT; k++) {
        const v = target * (0.5 + 0.5 * Math.sin(now * (1.3 + k * 0.7) + k * 1.9)) * (1 - k * 0.08);
        b[k] += (v - b[k]) * 0.2;
      }
    }

    this.level += (target - this.level) * (target > this.level ? 0.35 : 0.06);
    if (this.level < 0.004) this.level = 0;

    const wanted = this.thinking ? 1 : 0;
    this.think += (wanted - this.think) * (wanted > this.think ? 0.08 : 0.035);
    if (this.think < 0.004) this.think = 0;

    // Offline is a display decision, not a physics one. The envelopes keep
    // running — coming back should not snap — but nothing is handed out to draw.
    // The prototype does the same thing one layer up: `e = offline ? 0 : signal(now)`.
    if (this.offline) {
      this.out.fill(0);
      return { level: 0, bands: this.out, think: 0 };
    }

    for (let k = 0; k < BAND_COUNT; k++) this.out[k] = b[k];
    return { level: this.level, bands: this.out, think: this.think };
  }
}
```

- [ ] **Step 4: Run the signal test**

Run: `npm test -- src/lib/presence-signal.test.ts`
Expected: `Test Files  1 passed (1)`, 14 tests.

- [ ] **Step 5: Write the failing field test**

Create `web/src/room/PresenceField.test.tsx`:

```tsx
import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PresenceSignal } from "@/lib/presence-signal";
import { THEME_KEY } from "@/lib/theme";
import { ThemeProvider } from "@/shell/ThemeProvider";
import { PresenceField } from "./PresenceField";

interface Recorded {
  arcs: number;
  ellipses: number;
  fillStyles: string[];
  cleared: number;
}

let recorded: Recorded;

function fakeContext(): CanvasRenderingContext2D {
  const ctx = {
    setTransform: () => {},
    clearRect: () => void recorded.cleared++,
    beginPath: () => {},
    arc: () => void recorded.arcs++,
    ellipse: () => void recorded.ellipses++,
    fill: () => {},
    set fillStyle(value: string) {
      recorded.fillStyles.push(value);
    },
    get fillStyle() {
      return recorded.fillStyles.at(-1) ?? "";
    },
  };
  return ctx as unknown as CanvasRenderingContext2D;
}

function stubReducedMotion(matches: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, "hidden", { configurable: true, value: hidden });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: hidden ? "hidden" : "visible",
  });
}

function renderField(signal: PresenceSignal, offline = false) {
  return render(
    <ThemeProvider>
      <PresenceField signal={signal} offline={offline} />
    </ThemeProvider>,
  );
}

beforeEach(() => {
  recorded = { arcs: 0, ellipses: 0, fillStyles: [], cleared: 0 };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    fakeContext() as unknown as never,
  );
  setHidden(false);
  // Pin the theme: with no stored choice, resolveInitialTheme picks light
  // during the day, and the colour assertions below are the dark triples.
  localStorage.setItem(THEME_KEY, "dark");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("PresenceField", () => {
  it("is a 393x190 canvas that takes no pointer events", () => {
    const { container } = renderField(new PresenceSignal());
    const canvas = container.querySelector("canvas")!;
    expect(canvas).toBeInTheDocument();
    expect(canvas).toHaveAttribute("aria-hidden", "true");
    expect(canvas.style.width).toBe("393px");
    expect(canvas.style.height).toBe("190px");
    expect(canvas.style.pointerEvents).toBe("none");
  });

  it("scales the backing store by the device pixel ratio, capped at 2", () => {
    vi.stubGlobal("devicePixelRatio", 3);
    const { container } = renderField(new PresenceSignal());
    const canvas = container.querySelector("canvas")!;
    expect(canvas.width).toBe(786);
    expect(canvas.height).toBe(380);
  });

  it("draws the whole grid on its first frame", () => {
    renderField(new PresenceSignal());
    // 34 columns x 17 rows, one arc each.
    expect(recorded.cleared).toBeGreaterThanOrEqual(1);
    expect(recorded.arcs).toBeGreaterThanOrEqual(34 * 17);
  });

  it("draws in amber when connected and in grey when not (dark theme)", () => {
    renderField(new PresenceSignal(), false);
    expect(recorded.fillStyles.some((s) => s.startsWith("rgba(232,178,132"))).toBe(true);

    recorded = { arcs: 0, ellipses: 0, fillStyles: [], cleared: 0 };
    renderField(new PresenceSignal(), true);
    expect(recorded.fillStyles.some((s) => s.startsWith("rgba(154,145,134"))).toBe(true);
  });

  it("tells the signal it is offline", () => {
    const signal = new PresenceSignal();
    const setOffline = vi.spyOn(signal, "setOffline");
    renderField(signal, true);
    expect(setOffline).toHaveBeenCalledWith(true);
  });

  it("draws exactly one static frame under reduce-motion", () => {
    stubReducedMotion(true);
    const raf = vi.spyOn(window, "requestAnimationFrame");

    renderField(new PresenceSignal());

    expect(recorded.arcs).toBe(34 * 17);
    expect(raf).not.toHaveBeenCalled();
  });

  it("does not animate a hidden tab", () => {
    setHidden(true);
    const raf = vi.spyOn(window, "requestAnimationFrame");

    renderField(new PresenceSignal());

    expect(raf).not.toHaveBeenCalled();
    // Still painted once, so returning to the app never shows an empty canvas.
    expect(recorded.arcs).toBe(34 * 17);
  });

  it("stops the loop when it unmounts", () => {
    const cancel = vi.spyOn(window, "cancelAnimationFrame");
    const { unmount } = renderField(new PresenceSignal());
    unmount();
    expect(cancel).toHaveBeenCalled();
  });

  it("survives a canvas with no 2D context at all", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => {
      throw new Error("Not implemented: HTMLCanvasElement.prototype.getContext");
    });
    expect(() => renderField(new PresenceSignal())).not.toThrow();
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npm test -- src/room/PresenceField.test.tsx`
Expected: FAIL — `Failed to resolve import "./PresenceField"`.

- [ ] **Step 7: Write `web/src/room/PresenceField.tsx`**

Complete file:

```tsx
import { useEffect, useRef } from "react";
import type { PresenceSignal } from "@/lib/presence-signal";
import { useTheme } from "@/shell/ThemeProvider";

const W = 393;
const H = 190;
/** 12 pt dot grid — the handoff's "Presence field" paragraph. */
const STEP = 12;
/** The prototype's `dotResponse` prop. Fixed at its default; there is no control for it. */
const GAIN = 1;

function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export interface PresenceFieldProps {
  signal: PresenceSignal;
  offline: boolean;
}

/**
 * Alfred, as a surface. A dot grid displaced by a height field: still at rest,
 * tidal while you speak, pulsing while he thinks. Ported from `presence()` in
 * `Alfred.dc.html`.
 */
export function PresenceField({ signal, offline }: PresenceFieldProps) {
  const { theme } = useTheme();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const phaseRef = useRef(0);
  const thinkPhaseRef = useRef(0);
  const lastRef = useRef<number | null>(null);

  useEffect(() => {
    signal.setOffline(offline);
  }, [signal, offline]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const dark = theme === "dark";
    const reduced = prefersReducedMotion();

    const draw = (now: number) => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (canvas.width !== W * dpr) {
        canvas.width = W * dpr;
        canvas.height = H * dpr;
      }

      // jsdom has no 2D context without the optional `canvas` package, and a
      // canvas detached mid-frame returns null. Neither is worth a crash.
      let g: CanvasRenderingContext2D | null;
      try {
        g = canvas.getContext("2d");
      } catch {
        g = null;
      }
      if (!g) return;

      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, H);

      const frame = signal.tick(now);
      const e = frame.level;
      const b = frame.bands;
      const th = frame.think;

      // The wave phase only advances while something is flowing, so the field is
      // genuinely motionless at rest rather than slowly creeping.
      if (lastRef.current === null) lastRef.current = now;
      const dt = now - lastRef.current;
      phaseRef.current += dt * (0.4 + e * 2.2);
      thinkPhaseRef.current += dt * th;
      lastRef.current = now;
      const ph = phaseRef.current;
      const tph = thinkPhaseRef.current;

      const rgb = offline
        ? dark
          ? "154,145,134"
          : "138,129,119"
        : dark
          ? "232,178,132"
          : "205,132,80";
      const base = offline ? 0.42 : dark ? 0.55 : 0.7;

      const cols = Math.ceil(W / STEP) + 1;
      const rows = Math.ceil(H / STEP) + 1;

      // Tides: low bands are long swells travelling across, high bands are short
      // chop riding on the crests.
      const low = (b[0] + b[1] + b[2]) / 3;
      const mid = (b[3] + b[4]) / 2;
      const high = (b[5] + b[6] + b[7]) / 3;

      for (let j = 0; j < rows; j++) {
        const y = j * STEP;
        for (let i = 0; i < cols; i++) {
          const x = i * STEP;
          let z = 0;

          if (e > 0) {
            const swell =
              Math.sin(x * 0.022 + y * 0.012 - ph * 1.1) * 0.65 +
              Math.sin(x * 0.013 - y * 0.02 + ph * 0.7 + 1.7) * 0.5;
            const crest = Math.pow(Math.max(0, swell), 1.6) * (0.4 + low * 1.4);
            const trough = Math.min(0, swell) * 0.35 * (0.3 + low);
            const chop =
              Math.sin(x * 0.09 + ph * 3.1 + y * 0.04) *
              Math.sin(y * 0.07 - ph * 2.3) *
              high *
              0.5 *
              Math.max(0, swell + 0.3);
            const roll = Math.sin(x * 0.045 - ph * 1.8 + y * 0.03) * mid * 0.35;
            z = (crest + trough + chop + roll) * e * GAIN * 1.5;
            z = Math.max(-0.5, Math.min(1.9, z));
          }

          if (th > 0) {
            // A pulse sweeping down the field every ~2.4 s, with a fainter slow
            // counter-sweep going back up.
            const p1 = (y / H + tph * 0.42) % 1;
            const p2 = (y / H - tph * 0.17 + 0.5 + 1e4) % 1;
            const g1 = Math.abs(p1 - 0.5);
            const g2 = Math.abs(p2 - 0.5);
            const band = Math.exp(-Math.pow(g1 / 0.11, 2));
            const back = Math.exp(-Math.pow(g2 / 0.18, 2)) * 0.35;
            const lean = 1 - Math.abs(x - W / 2) / (W * 0.9);
            z += (band * 1.15 + back) * lean * th * GAIN;
          }

          const px = x + z * Math.sin(x * 0.022 - ph) * 2.5;
          const py = y - z * 12;
          const r = Math.max(0.6, 1 + 0.7 * z);
          const a = Math.max(0.05, Math.min(1, base * (0.65 + 0.45 * z)));

          if (z > 0.3) {
            g.fillStyle = `rgba(0,0,0,${(dark ? 0.24 : 0.11) * Math.min(1, z - 0.3)})`;
            g.beginPath();
            g.ellipse(px, y + 2, r * 1.3, r * 0.5, 0, 0, Math.PI * 2);
            g.fill();
          }

          g.fillStyle = `rgba(${rgb},${a})`;
          g.beginPath();
          g.arc(px, py, r, 0, Math.PI * 2);
          g.fill();
        }
      }
    };

    let raf = 0;
    const loop = () => {
      draw(performance.now() / 1000);
      raf = requestAnimationFrame(loop);
    };

    // Reduce-motion: one frame at t=0, and never again. Constraint from the
    // handoff's motion section, and the JS half of the CSS `prefers-reduced-motion`
    // block in index.css.
    if (reduced) {
      draw(0);
      return;
    }

    const start = () => {
      if (raf) return;
      // Drop the accumulated clock so a backgrounded hour does not arrive as one
      // enormous dt and fling the phase forward.
      lastRef.current = null;
      loop();
    };
    const stop = () => {
      if (!raf) return;
      cancelAnimationFrame(raf);
      raf = 0;
    };

    const onVisibility = () => (document.hidden ? stop() : start());
    document.addEventListener("visibilitychange", onVisibility);

    if (document.hidden) draw(0);
    else start();

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      cancelAnimationFrame(raf);
    };
  }, [signal, offline, theme]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="absolute top-0 left-1/2 -translate-x-1/2"
      style={{
        width: `${W}px`,
        height: `${H}px`,
        pointerEvents: "none",
        WebkitMaskImage: "linear-gradient(to bottom, rgba(0,0,0,.95) 30%, transparent 100%)",
        maskImage: "linear-gradient(to bottom, rgba(0,0,0,.95) 30%, transparent 100%)",
      }}
    />
  );
}
```

- [ ] **Step 8: Run the field test and the suite**

Run: `npm test -- src/room/PresenceField.test.tsx`
Expected: `Test Files  1 passed (1)`, 9 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  23 passed (23)`, no eslint output, `✓ built in …`.

- [ ] **Step 9: Commit**

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
git add web/src/lib/presence-signal.ts web/src/lib/presence-signal.test.ts web/src/room/PresenceField.tsx web/src/room/PresenceField.test.tsx
git commit -m "feat(web): Alfred's presence field — the ported signal and canvas"
```

---

### Task 16: The headline and the two rows under it

Three small components and one pure function that decides what Alfred says about himself. The function is worth isolating because the priority order is a product decision, not a rendering detail: an unreachable house says `Unreachable.` even on its first morning, and a do-not-disturb with no expiry has to be able to say so (spec §5.2.6 — "a silently growing deferred queue is otherwise invisible").

Priority, highest first:

| Condition | Headline |
|---|---|
| `!online && !reconnecting` | `Unreachable.` |
| `reconnecting` | `Reconnecting…` |
| `holding` | `Go on, sir.` |
| `busy` | `One moment, sir.` |
| `dnd.active` with an `until` | `Quiet until HH:MM.` |
| `dnd.active` with no `until` | `Quiet until further notice.` |
| `firstRun` | `Good morning, sir.` 05–11 · `Good afternoon, sir.` 12–17 · `Good evening, sir.` otherwise |
| otherwise | `Listening, sir.` |

This task also lifts `isFirstRun` out of `StatusLine` so the headline and the status line cannot disagree about what a first run is.

**Files:**
- Create: `web/src/lib/headline.ts`, `web/src/lib/headline.test.ts`
- Create: `web/src/room/Headline.tsx`, `web/src/room/OfflineNote.tsx`, `web/src/room/DndRow.tsx`, `web/src/room/Headline.test.tsx`
- Modify: `web/src/room/useOverview.ts`, `web/src/room/StatusLine.tsx`

- [ ] **Step 1: Write the failing headline test**

Create `web/src/lib/headline.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { greetingFor, pickHeadline, type HeadlineInput } from "./headline";

const idle: HeadlineInput = {
  online: true,
  reconnecting: false,
  firstRun: false,
  dnd: { active: false },
  holding: false,
  busy: false,
  hour: 21,
};

describe("pickHeadline", () => {
  it("listens when there is nothing else to say", () => {
    expect(pickHeadline(idle)).toBe("Listening, sir.");
  });

  it("says the house is unreachable before anything else", () => {
    expect(
      pickHeadline({
        ...idle,
        online: false,
        holding: true,
        busy: true,
        firstRun: true,
        dnd: { active: true, until: "2026-09-08T08:30:00" },
      }),
    ).toBe("Unreachable.");
  });

  it("distinguishes reconnecting from gone", () => {
    expect(pickHeadline({ ...idle, online: false, reconnecting: true })).toBe("Reconnecting…");
  });

  it("attends to the microphone over everything the house is doing", () => {
    expect(pickHeadline({ ...idle, holding: true, busy: true })).toBe("Go on, sir.");
  });

  it("says one moment while a turn is in flight", () => {
    expect(pickHeadline({ ...idle, busy: true })).toBe("One moment, sir.");
  });

  it("names the hour do-not-disturb ends", () => {
    expect(
      pickHeadline({ ...idle, dnd: { active: true, until: new Date(2026, 8, 8, 8, 30).toISOString() } }),
    ).toBe("Quiet until 08:30.");
  });

  it("says so when do-not-disturb has no end at all", () => {
    expect(pickHeadline({ ...idle, dnd: { active: true, until: null } })).toBe(
      "Quiet until further notice.",
    );
    expect(pickHeadline({ ...idle, dnd: { active: true } })).toBe("Quiet until further notice.");
  });

  it("refuses to invent an expiry from an unparseable one", () => {
    expect(pickHeadline({ ...idle, dnd: { active: true, until: "soon" } })).toBe(
      "Quiet until further notice.",
    );
  });

  it("greets by the hour on a first run", () => {
    const greet = (hour: number) => pickHeadline({ ...idle, firstRun: true, hour });
    expect(greet(5)).toBe("Good morning, sir.");
    expect(greet(11)).toBe("Good morning, sir.");
    expect(greet(12)).toBe("Good afternoon, sir.");
    expect(greet(17)).toBe("Good afternoon, sir.");
    expect(greet(18)).toBe("Good evening, sir.");
    expect(greet(23)).toBe("Good evening, sir.");
    expect(greet(4)).toBe("Good evening, sir.");
  });

  it("prefers quiet to a greeting", () => {
    expect(pickHeadline({ ...idle, firstRun: true, dnd: { active: true, until: null } })).toBe(
      "Quiet until further notice.",
    );
  });
});

describe("greetingFor", () => {
  it("is the same table the headline uses, exposed for the first-day row", () => {
    expect(greetingFor(9)).toBe("Good morning, sir.");
    expect(greetingFor(14)).toBe("Good afternoon, sir.");
    expect(greetingFor(21)).toBe("Good evening, sir.");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/lib/headline.test.ts`
Expected: FAIL — `Failed to resolve import "./headline"`.

- [ ] **Step 3: Write `web/src/lib/headline.ts`**

Complete file:

```ts
import { hhmm } from "./format";

export interface HeadlineInput {
  online: boolean;
  reconnecting: boolean;
  /** Every catalogued stream is empty — nothing has ever happened in this house. */
  firstRun: boolean;
  dnd: { active: boolean; until?: string | null };
  holding: boolean;
  /** A turn is in flight: sent, no reply yet. */
  busy: boolean;
  /** Local hour, 0–23. Passed in so the function stays pure and testable. */
  hour: number;
}

/** Exported because the empty Room's first-day row must greet the same way. */
export function greetingFor(hour: number): string {
  if (hour >= 5 && hour <= 11) return "Good morning, sir.";
  if (hour >= 12 && hour <= 17) return "Good afternoon, sir.";
  return "Good evening, sir.";
}

/**
 * What Alfred says about himself, in one line.
 *
 * Order matters and is a product decision: connection state outranks everything,
 * because a greeting over a dead socket is a lie; the microphone outranks the
 * conversation, because it is the thing the user is doing right now.
 */
export function pickHeadline(input: HeadlineInput): string {
  if (input.reconnecting) return "Reconnecting…";
  if (!input.online) return "Unreachable.";
  if (input.holding) return "Go on, sir.";
  if (input.busy) return "One moment, sir.";

  if (input.dnd.active) {
    const until = input.dnd.until;
    const stamp = until ? hhmm(until) : "--:--";
    // `hhmm` answers `--:--` for anything it cannot parse. "Quiet until --:--."
    // would be worse than admitting there is no end.
    return stamp === "--:--" ? "Quiet until further notice." : `Quiet until ${stamp}.`;
  }

  if (input.firstRun) return greetingFor(input.hour);
  return "Listening, sir.";
}
```

- [ ] **Step 4: Run the headline test**

Run: `npm test -- src/lib/headline.test.ts`
Expected: `Test Files  1 passed (1)`, 11 tests.

- [ ] **Step 5: Write the failing component test**

Create `web/src/room/Headline.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DndRow } from "./DndRow";
import { Headline } from "./Headline";
import { OfflineNote } from "./OfflineNote";

const at2114 = new Date(2026, 8, 7, 21, 14);

describe("Headline", () => {
  it("is the page's one heading", () => {
    render(<Headline text="Listening, sir." />);
    const heading = screen.getByRole("heading", { name: "Listening, sir." });
    expect(heading).toHaveClass("t-headline");
  });
});

describe("OfflineNote", () => {
  it("stamps when the house was last reachable", () => {
    render(<OfflineNote reconnecting={false} lastTrueAt={at2114} />);
    expect(
      screen.getByText(
        "No connection to the house since 21:14. Everything below is last-known. Sending is paused.",
      ),
    ).toBeInTheDocument();
  });

  it("says it is still trying while reconnecting", () => {
    render(<OfflineNote reconnecting lastTrueAt={at2114} />);
    expect(
      screen.getByText("Trying again. Everything below was last true at 21:14."),
    ).toBeInTheDocument();
  });

  it("prints an unknown clock rather than a made-up one", () => {
    render(<OfflineNote reconnecting={false} lastTrueAt={null} />);
    expect(screen.getByText(/since --:--\./)).toBeInTheDocument();
  });
});

describe("DndRow", () => {
  it("names the hour quiet ends and how much is waiting", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(
      <DndRow until={new Date(2026, 8, 8, 8, 30).toISOString()} heldCount={2} onOpen={onOpen} />,
    );

    const row = screen.getByRole("button", { name: /Do-not-disturb until 08:30/ });
    expect(row).toHaveTextContent("2 held ›");

    await user.click(row);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("says there is no expiry when there is none", () => {
    render(<DndRow until={null} heldCount={0} onOpen={() => {}} />);
    expect(
      screen.getByRole("button", { name: /Do-not-disturb · no expiry/ }),
    ).toHaveTextContent("0 held ›");
  });

  it("treats an unparseable expiry as no expiry", () => {
    render(<DndRow until="whenever" heldCount={1} onOpen={() => {}} />);
    expect(screen.getByRole("button", { name: /Do-not-disturb · no expiry/ })).toBeInTheDocument();
  });

  it("is a 44 px tap target", () => {
    render(<DndRow until={null} heldCount={0} onOpen={() => {}} />);
    expect(screen.getByRole("button")).toHaveClass("min-h-11");
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npm test -- src/room/Headline.test.tsx`
Expected: FAIL — `Failed to resolve import "./DndRow"`.

- [ ] **Step 7: Write the three components**

Complete `web/src/room/Headline.tsx`:

```tsx
/** 26/500/−0.02em, top-left of the header. `pickHeadline` decides the words. */
export function Headline({ text }: { text: string }) {
  return <h1 className="t-headline">{text}</h1>;
}
```

Complete `web/src/room/OfflineNote.tsx`:

```tsx
import { hhmm } from "@/lib/format";

export interface OfflineNoteProps {
  reconnecting: boolean;
  lastTrueAt: Date | null;
}

/**
 * Spec §5.2.2, "live is not last-known". Shown whenever the chat socket is not
 * online, and always carrying the time it last was.
 */
export function OfflineNote({ reconnecting, lastTrueAt }: OfflineNoteProps) {
  const stamp = lastTrueAt ? hhmm(lastTrueAt) : "--:--";
  const text = reconnecting
    ? `Trying again. Everything below was last true at ${stamp}.`
    : `No connection to the house since ${stamp}. Everything below is last-known. Sending is paused.`;

  return (
    <div
      className="mt-2 flex items-center gap-2 rounded-[10px] px-3 py-2 text-[13px] leading-[1.4]"
      style={{ background: "var(--surface)" }}
    >
      <span
        aria-hidden="true"
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ background: "var(--muted)" }}
      />
      <span>{text}</span>
    </div>
  );
}
```

Complete `web/src/room/DndRow.tsx`:

```tsx
import { hhmm } from "@/lib/format";

export interface DndRowProps {
  /** ISO 8601, or null for "no expiry" — the queue that never drains on its own. */
  until: string | null | undefined;
  heldCount: number;
  onOpen: () => void;
}

/**
 * The only place the Room admits it is being quiet. Opens the Held-back sheet,
 * because a deferred queue you cannot see is the failure spec §5.2.6 names.
 */
export function DndRow({ until, heldCount, onOpen }: DndRowProps) {
  const stamp = until ? hhmm(until) : "--:--";
  const label =
    stamp === "--:--" ? "Do-not-disturb · no expiry" : `Do-not-disturb until ${stamp}`;

  return (
    <button
      type="button"
      onClick={onOpen}
      className="mt-2 flex min-h-11 w-full items-center justify-between gap-2 rounded-[10px] border px-3 py-2.5 text-left text-[13px]"
      style={{ borderColor: "var(--line)", background: "transparent", color: "var(--fg)" }}
    >
      <span>{label}</span>
      <span className="t-meta">{heldCount} held ›</span>
    </button>
  );
}
```

- [ ] **Step 8: Lift `isFirstRun` into `useOverview.ts`**

Append to `web/src/room/useOverview.ts`:

```ts
/**
 * True when every catalogued stream is empty — nothing has ever happened here.
 *
 * An *absent* streams map is a different thing entirely (Redis is down), and must
 * not read as a first run. Shared by the status line and the headline so the two
 * can never disagree about which morning this is.
 */
export function isFirstRun(overview: Overview | undefined): boolean {
  const streams = overview?.streams ?? {};
  return (
    Object.keys(streams).length > 0 && Object.values(streams).every((stream) => stream.length === 0)
  );
}
```

In `web/src/room/StatusLine.tsx`, replace the inline computation

```tsx
  const streams = overview?.streams ?? {};
  // Every catalogued stream empty means nothing has ever happened here. An *absent*
  // streams map means Redis is down, which is a different thing entirely.
  const firstRun =
    Object.keys(streams).length > 0 && Object.values(streams).every((s) => s.length === 0);
```

with

```tsx
  const streams = overview?.streams ?? {};
  const firstRun = isFirstRun(overview);
```

and add the import beside the existing ones:

```tsx
import { isFirstRun } from "@/room/useOverview";
```

`StatusLine.test.tsx` is unchanged and must stay green — that is the point of doing it here rather than duplicating the rule.

- [ ] **Step 9: Run the tests and the suite**

Run: `npm test -- src/room/Headline.test.tsx src/room/StatusLine.test.tsx`
Expected: `Test Files  2 passed (2)` — 8 and 7 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  25 passed (25)`, no eslint output, `✓ built in …`.

- [ ] **Step 10: Commit**

```bash
git add web/src/lib/headline.ts web/src/lib/headline.test.ts web/src/room/Headline.tsx web/src/room/Headline.test.tsx web/src/room/OfflineNote.tsx web/src/room/DndRow.tsx web/src/room/useOverview.ts web/src/room/StatusLine.tsx
git commit -m "feat(web): the headline, the offline note and the do-not-disturb row"
```

---

### Task 17: History — four streams become one thread

The Room has no history endpoint. It has four stream pages, and the merge is the client's job (spec §10, "Composes from existing endpoints"). `Promise.allSettled` rather than `Promise.all`: a single stream that 404s or times out must cost you that stream, not the whole conversation.

What each stream contributes:

| Stream | Filter | Row |
|---|---|---|
| `user_requests` | none | `you` bubble, text = `content` |
| `user_responses` | none | `alfred` row, text = `text`, `mood`, `actions_taken` |
| `reflex_observations` | `action != null` | `act` row, RX hue 210 |
| `notifications` | `metadata.pending_action_id` absent | `act` row, NT hue 255 |

A passive observation (`action: null`) is deliberately dropped: spec §10 says "The Room renders only reflex observations that carry an `action`"; the ones Alfred merely watched belong to the Activity bench in Phase 2. A notification that *is* a confirmation request is dropped too — the Door owns it, and it would otherwise appear twice.

Dividers come last, over the merged list: a day divider whenever the date changes, and `new conversation · HH:MM` when two consecutive conversational turns are more than thirty minutes apart.

**Files:**
- Create: `web/src/lib/history.ts`, `web/src/lib/history.test.ts`
- Create: `web/src/room/useRoomHistory.ts`
- Modify: `web/src/test/fixtures.ts`

- [ ] **Step 1: Add the stream fixtures**

Append to `web/src/test/fixtures.ts` (extend the type import with `StreamPage`):

```ts
/**
 * Four stream pages as `GET /api/admin/streams/{name}?count=50` returns them:
 * newest first, each entry `{id, event}` with the event decoded from JSON.
 *
 * The clock in these is 2026-09-07: 17:58 a reflex act, 18:20 a notification,
 * 20:52 one conversational turn, and — an hour and a half later, so the
 * thirty-minute rule has something to fire on — 21:14 another.
 */
export const userRequestsPage: StreamPage = {
  entries: [
    {
      id: "1757279640000-0",
      event: {
        event_id: "ur-2",
        event_type: "user_request",
        timestamp: "2026-09-07T21:14:00",
        source: "web-pwa",
        channel: "web_pwa",
        session_id: "s_9f3",
        content_type: "text",
        content: "Is the back door locked?",
      },
    },
    {
      id: "1757275923000-0",
      event: {
        event_id: "ur-1",
        event_type: "user_request",
        timestamp: "2026-09-07T20:52:03",
        source: "web-pwa",
        channel: "web_pwa",
        session_id: "s_9f2",
        content_type: "text",
        content: "What have I got tomorrow morning?",
      },
    },
  ],
  next_before: null,
};

export const userResponsesPage: StreamPage = {
  entries: [
    {
      id: "1757279646000-0",
      event: {
        event_id: "al-2",
        event_type: "alfred_response",
        timestamp: "2026-09-07T21:14:06",
        source: "conscious-engine",
        session_id: "s_9f3",
        text: "It is, sir. Locked since 19:40.",
        actions_taken: ["home.get_state"],
        mood: "neutral",
      },
    },
    {
      id: "1757275926000-0",
      event: {
        event_id: "al-1",
        event_type: "alfred_response",
        timestamp: "2026-09-07T20:52:06",
        source: "conscious-engine",
        session_id: "s_9f2",
        text: "The dentist at nine, sir. I'd leave by twenty to; there's rain forecast from eight.",
        actions_taken: ["calendar.today", "weather.forecast"],
        mood: "pleased",
      },
    },
  ],
  next_before: null,
};

export const reflexObservationsPage: StreamPage = {
  entries: [
    {
      // Passive: seen, considered, nothing done. Never a Room row.
      id: "1757275000000-0",
      event: {
        observation_id: "obs-3",
        event_type: "reflex_observation",
        timestamp: "2026-09-07T20:36:40",
        source: "reflex-runner",
        origin: "state_change",
        trigger_event: { entity_id: "binary_sensor.motion_hall", new_state: "on" },
        action: null,
        result: null,
        decision_context: "user moving about, lights already on",
      },
    },
    {
      // Acted, and it failed. The meta says so.
      id: "1757271660000-0",
      event: {
        observation_id: "obs-2",
        event_type: "reflex_observation",
        timestamp: "2026-09-07T19:41:00",
        source: "reflex-runner",
        origin: "state_change",
        trigger_event: { entity_id: "fan.bathroom", new_state: "on" },
        action: { request_id: "8d2a", tool_name: "home.fan_set", target_service: "home-service" },
        result: { status: "error", error: "service unavailable" },
        decision_context: null,
      },
    },
    {
      id: "1757264280000-0",
      event: {
        observation_id: "obs-1",
        event_type: "reflex_observation",
        timestamp: "2026-09-07T17:58:00",
        source: "reflex-runner",
        origin: "state_change",
        trigger_event: { entity_id: "media_player.tv", new_state: "playing" },
        action: { request_id: "4b1d", tool_name: "home.light_set", target_service: "home-service" },
        result: { status: "success" },
        decision_context: "movie started, evening, user home",
      },
    },
  ],
  next_before: null,
};

export const notificationsPage: StreamPage = {
  entries: [
    {
      // A confirmation request: the Door's business, never a thread row.
      id: "1757265700000-0",
      event: {
        notification_id: "ntf-2",
        title: "Confirmation required",
        body: "Alfred wants to run 'home.lock_unlock' on home-service — confirm?",
        urgency: "urgent",
        source: "domain-router",
        timestamp: "2026-09-07T18:41:40",
        metadata: {
          pending_action_id: "a91f3c2e",
          tool_name: "home.lock_unlock",
          parameters: { entity_id: "lock.front_door", action: "unlock" },
          reason: null,
        },
      },
    },
    {
      id: "1757265600000-0",
      event: {
        notification_id: "ntf-1",
        title: "Your parcel arrived",
        body: "The door sensor saw it at 18:20.",
        urgency: "important",
        source: "trigger:trg_parcel",
        timestamp: "2026-09-07T18:20:00",
        metadata: {},
      },
    },
  ],
  next_before: null,
};

/** Yesterday, so the day-divider rule has two days to separate. */
export const yesterdayRequestPage: StreamPage = {
  entries: [
    {
      id: "1757185923000-0",
      event: {
        event_id: "ur-0",
        event_type: "user_request",
        timestamp: "2026-09-06T19:52:03",
        source: "web-pwa",
        channel: "web_pwa",
        session_id: "s_9f0",
        content_type: "text",
        content: "Lock up for the night.",
      },
    },
  ],
  next_before: null,
};
```

- [ ] **Step 2: Write the failing history test**

Create `web/src/lib/history.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchRoomHistory,
  pendingActionTitles,
  toTimelineItems,
  withDividers,
  type RoomHistory,
  type TimelineItem,
} from "./history";
import {
  notificationsPage,
  reflexObservationsPage,
  userRequestsPage,
  userResponsesPage,
  yesterdayRequestPage,
} from "@/test/fixtures";

const HISTORY: RoomHistory = {
  user_requests: userRequestsPage.entries,
  user_responses: userResponsesPage.entries,
  reflex_observations: reflexObservationsPage.entries,
  notifications: notificationsPage.entries,
};

afterEach(() => vi.unstubAllGlobals());

describe("fetchRoomHistory", () => {
  it("reads exactly the four Room streams, fifty each", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        urls.push(String(input));
        return new Response('{"entries":[],"next_before":null}', { status: 200 });
      }),
    );

    await fetchRoomHistory();

    expect(urls.sort()).toEqual([
      "/api/admin/streams/notifications?count=50",
      "/api/admin/streams/reflex_observations?count=50",
      "/api/admin/streams/user_requests?count=50",
      "/api/admin/streams/user_responses?count=50",
    ]);
  });

  it("loses one stream rather than the whole conversation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes("notifications"))
          return new Response('{"detail":"redis is down"}', { status: 503 });
        return new Response(JSON.stringify(userRequestsPage), { status: 200 });
      }),
    );

    const history = await fetchRoomHistory();

    expect(history.notifications).toEqual([]);
    expect(history.user_requests).toHaveLength(2);
  });
});

describe("toTimelineItems", () => {
  const items = toTimelineItems(HISTORY);

  it("puts the whole thread in chronological order", () => {
    expect(items.map((item) => item.kind)).toEqual([
      "act",
      "act",
      "act",
      "you",
      "alfred",
      "you",
      "alfred",
    ]);
    const stamps = items.map((item) => Date.parse(item.at));
    expect(stamps).toEqual([...stamps].sort((a, b) => a - b));
  });

  it("renders what you said as a sent bubble", () => {
    const you = items.find((item) => item.kind === "you")!;
    expect(you).toMatchObject({
      kind: "you",
      text: "What have I got tomorrow morning?",
      state: "sent",
    });
  });

  it("carries mood and tools onto Alfred's row", () => {
    const alfred = items.find((item) => item.kind === "alfred")!;
    expect(alfred).toMatchObject({
      kind: "alfred",
      mood: "pleased",
      actions: ["calendar.today", "weather.forecast"],
    });
  });

  it("reads a reflex act as its decision, in the RX hue", () => {
    const act = items[0];
    expect(act).toMatchObject({
      kind: "act",
      hue: 210,
      text: "movie started, evening, user home",
      meta: "17:58 · reflex · home.light_set",
    });
  });

  it("says when a reflex act failed", () => {
    const failed = items.find((item) => item.kind === "act" && item.meta.includes("home.fan_set"))!;
    expect(failed.kind === "act" && failed.meta).toBe("19:41 · reflex · home.fan_set · failed");
  });

  it("falls back to the tool name when there is no decision context", () => {
    const failed = items.find((item) => item.kind === "act" && item.meta.includes("home.fan_set"))!;
    expect(failed.kind === "act" && failed.text).toBe("home.fan_set");
  });

  it("drops the observations Alfred only watched", () => {
    expect(items.some((item) => item.kind === "act" && item.text.includes("moving about"))).toBe(
      false,
    );
  });

  it("renders a notification as its title, in the NT hue", () => {
    const nt = items.find((item) => item.kind === "act" && item.hue === 255)!;
    expect(nt).toMatchObject({
      text: "Your parcel arrived",
      meta: "18:20 · trigger:trg_parcel · important",
    });
  });

  it("leaves confirmation requests to the Door", () => {
    expect(
      items.some((item) => item.kind === "act" && item.text.includes("Confirmation required")),
    ).toBe(false);
  });

  it("gives every row a stable, unique id", () => {
    const ids = items.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(toTimelineItems(HISTORY).map((item) => item.id)).toEqual(ids);
  });

  it("skips an entry whose event is unreadable rather than dying", () => {
    const parsed = toTimelineItems({
      user_requests: [{ id: "1-0", event: {} }, ...userRequestsPage.entries],
      user_responses: [],
      reflex_observations: [],
      notifications: [],
    });
    expect(parsed).toHaveLength(2);
  });
});

describe("withDividers", () => {
  const now = new Date(2026, 8, 7, 21, 30);

  it("opens each day with its own label", () => {
    const twoDays = toTimelineItems({
      user_requests: [...userRequestsPage.entries, ...yesterdayRequestPage.entries],
      user_responses: [],
      reflex_observations: [],
      notifications: [],
    });

    const labels = withDividers(twoDays, now)
      .filter((item) => item.kind === "divider")
      .map((item) => (item.kind === "divider" ? item.label : ""));

    expect(labels).toEqual(["yesterday", "earlier today"]);
  });

  it("starts a new conversation after a thirty-minute silence", () => {
    const labels = withDividers(toTimelineItems(HISTORY), now)
      .filter((item) => item.kind === "divider")
      .map((item) => (item.kind === "divider" ? item.label : ""));

    // 20:52 then 21:14 is 22 minutes — the same conversation. The act rows before
    // them are not turns and never open one.
    expect(labels).toEqual(["earlier today"]);
  });

  it("splits two turns more than thirty minutes apart", () => {
    const first = toTimelineItems({
      user_requests: userRequestsPage.entries,
      user_responses: [],
      reflex_observations: [],
      notifications: [],
    })[0];
    const later: TimelineItem = {
      kind: "you",
      id: "you:later",
      at: "2026-09-07T21:44:00",
      text: "And the windows?",
      state: "sent",
    };

    const labels = withDividers([first, later], now)
      .filter((item) => item.kind === "divider")
      .map((item) => (item.kind === "divider" ? item.label : ""));

    expect(labels).toEqual(["earlier today", "new conversation · 21:44"]);
  });

  it("leaves an empty thread empty", () => {
    expect(withDividers([], now)).toEqual([]);
  });

  it("gives every divider a unique id", () => {
    const ids = withDividers(toTimelineItems(HISTORY), now).map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("pendingActionTitles", () => {
  it("names an approval by its tool, for the tombstone a deep link may need", () => {
    expect(pendingActionTitles(HISTORY)).toEqual({ a91f3c2e: "Lock unlock" });
  });

  it("is empty for an absent history", () => {
    expect(pendingActionTitles(undefined)).toEqual({});
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npm test -- src/lib/history.test.ts`
Expected: FAIL — `Failed to resolve import "./history"`.

- [ ] **Step 4: Write `web/src/lib/history.ts`**

Complete file:

```ts
import { api } from "./api";
import { dayLabel, hhmm, humaniseTool } from "./format";
import type { Mood, StreamEntry, StreamPage } from "./types";

/** One row of the Room. Every kind carries an ISO `at`; the list is sorted by it. */
export type TimelineItem =
  | { kind: "divider"; id: string; at: string; label: string }
  | { kind: "you"; id: string; at: string; text: string; state: "sent" | "unsent" }
  | {
      kind: "alfred";
      id: string;
      at: string;
      text: string;
      mood?: Mood;
      actions: string[];
      error?: boolean;
    }
  /** hue 120 trigger (EV) · 210 reflex (RX) · 255 notification (NT) — the handoff's stream hues. */
  | { kind: "act"; id: string; at: string; hue: 120 | 210 | 255; text: string; meta: string }
  | { kind: "tombstone"; id: string; at: string; title: string; meta: string }
  | { kind: "transcribing"; id: string; at: string; seconds: number }
  | { kind: "thinking"; id: string; at: string; detail: string };

/** The four streams the Room reads. Anything else belongs to the Workshop. */
export const ROOM_STREAMS = [
  "user_requests",
  "user_responses",
  "reflex_observations",
  "notifications",
] as const;

export type RoomStream = (typeof ROOM_STREAMS)[number];

export type RoomHistory = Record<RoomStream, StreamEntry[]>;

const HISTORY_COUNT = 50;
/** A silence this long between two turns starts a new conversation (handoff). */
const CONVERSATION_GAP_MS = 30 * 60 * 1000;

function emptyHistory(): RoomHistory {
  return {
    user_requests: [],
    user_responses: [],
    reflex_observations: [],
    notifications: [],
  };
}

/**
 * Read the Room's history: four pages of fifty, in parallel.
 *
 * `allSettled`, not `all`: one stream that 503s because Redis is briefly gone
 * should cost the user that stream, not the whole conversation. A missing stream
 * is an empty list, which renders as a thread with a hole in it rather than a
 * blank screen with an error.
 */
export async function fetchRoomHistory(): Promise<RoomHistory> {
  const settled = await Promise.allSettled(
    ROOM_STREAMS.map((name) =>
      api<StreamPage>(`/api/admin/streams/${name}?count=${HISTORY_COUNT}`),
    ),
  );

  const history = emptyHistory();
  settled.forEach((result, index) => {
    if (result.status === "fulfilled") history[ROOM_STREAMS[index]] = result.value.entries ?? [];
  });
  return history;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function youItem(entry: StreamEntry): TimelineItem | null {
  const at = str(entry.event.timestamp);
  const text = str(entry.event.content);
  if (!at || !text) return null;
  return { kind: "you", id: `you:${entry.id}`, at, text, state: "sent" };
}

function alfredItem(entry: StreamEntry): TimelineItem | null {
  const at = str(entry.event.timestamp);
  const text = str(entry.event.text);
  if (!at || !text) return null;
  const actions = Array.isArray(entry.event.actions_taken)
    ? entry.event.actions_taken.filter((a): a is string => typeof a === "string")
    : [];
  const mood = str(entry.event.mood);
  return {
    kind: "alfred",
    id: `alfred:${entry.id}`,
    at,
    text,
    mood: (mood ?? undefined) as Mood | undefined,
    actions,
  };
}

function reflexItem(entry: StreamEntry): TimelineItem | null {
  const at = str(entry.event.timestamp);
  const action = record(entry.event.action);
  // Passive observation: seen, considered, nothing done. Spec §10 — the Room
  // renders only observations that carry an action; the rest are the Workshop's.
  if (!at || !action) return null;
  const tool = str(action.tool_name) ?? "action";
  const result = record(entry.event.result);
  const failed = str(result?.status) === "error";
  return {
    kind: "act",
    id: `rx:${entry.id}`,
    at,
    hue: 210,
    text: str(entry.event.decision_context) ?? tool,
    meta: `${hhmm(at)} · reflex · ${tool}${failed ? " · failed" : ""}`,
  };
}

function notificationItem(entry: StreamEntry): TimelineItem | null {
  const at = str(entry.event.timestamp);
  const title = str(entry.event.title);
  if (!at || !title) return null;
  // A confirmation request is the Door's, and rendering it here as well would
  // show the same decision twice, one of them without a fuse.
  if (str(record(entry.event.metadata)?.pending_action_id)) return null;
  const source = str(entry.event.source) ?? "house";
  const urgency = str(entry.event.urgency) ?? "informational";
  return {
    kind: "act",
    id: `nt:${entry.id}`,
    at,
    hue: 255,
    text: title,
    meta: `${hhmm(at)} · ${source} · ${urgency}`,
  };
}

/** Four stream pages → one chronological thread. Unreadable entries are skipped. */
export function toTimelineItems(history: RoomHistory): TimelineItem[] {
  const items: TimelineItem[] = [];
  const add = (item: TimelineItem | null) => {
    if (item) items.push(item);
  };

  for (const entry of history.user_requests) add(youItem(entry));
  for (const entry of history.user_responses) add(alfredItem(entry));
  for (const entry of history.reflex_observations) add(reflexItem(entry));
  for (const entry of history.notifications) add(notificationItem(entry));

  return items.sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/**
 * Insert the two kinds of divider the handoff draws: one per day, and one after
 * a half-hour silence between conversational turns.
 *
 * Only `you` and `alfred` rows open a conversation. An autonomous act at 03:00
 * is Alfred talking to the house, not to you, and must not split the thread.
 */
export function withDividers(items: TimelineItem[], now: Date): TimelineItem[] {
  const out: TimelineItem[] = [];
  let lastDay: number | null = null;
  let lastTurnAt: number | null = null;

  for (const item of items) {
    const at = new Date(item.at);
    const day = startOfDay(at);

    if (day !== lastDay) {
      out.push({
        kind: "divider",
        id: `divider:day:${day}`,
        at: item.at,
        label: dayLabel(at, now),
      });
      lastDay = day;
      lastTurnAt = null;
    }

    if (item.kind === "you" || item.kind === "alfred") {
      const stamp = at.getTime();
      if (lastTurnAt !== null && stamp - lastTurnAt >= CONVERSATION_GAP_MS) {
        out.push({
          kind: "divider",
          id: `divider:gap:${item.id}`,
          at: item.at,
          label: `new conversation · ${hhmm(at)}`,
        });
      }
      lastTurnAt = stamp;
    }

    out.push(item);
  }

  return out;
}

/**
 * `pending_action_id` → a human title, harvested from the notifications page.
 *
 * A notification tap that lands on an action the house has already forgotten has
 * only the id in the URL. This is the one place the client can learn what that
 * id was *about*, so the tombstone can say "Lock unlock" instead of "Action a91f".
 */
export function pendingActionTitles(history: RoomHistory | undefined): Record<string, string> {
  const titles: Record<string, string> = {};
  for (const entry of history?.notifications ?? []) {
    const metadata = record(entry.event.metadata);
    const id = str(metadata?.pending_action_id);
    if (!id) continue;
    const tool = str(metadata?.tool_name);
    titles[id] = tool ? humaniseTool(tool) : (str(entry.event.title) ?? `Action ${id.slice(0, 4)}`);
  }
  return titles;
}
```

- [ ] **Step 5: Run the history test**

Run: `npm test -- src/lib/history.test.ts`
Expected: `Test Files  1 passed (1)`, 20 tests.

- [ ] **Step 6: Write `web/src/room/useRoomHistory.ts`**

Complete file:

```ts
import { useQuery } from "@tanstack/react-query";
import { fetchRoomHistory, type RoomHistory } from "@/lib/history";

/**
 * The thread as it stood when the app opened. Read once, then only again when
 * the app comes back to the foreground — `ConnectionProvider` invalidates
 * `["room-history"]` on `visibilitychange`, because the telemetry socket starts
 * at `$` and replays nothing (constraint §4.10).
 */
export function useRoomHistory() {
  return useQuery<RoomHistory>({
    queryKey: ["room-history"],
    queryFn: fetchRoomHistory,
    staleTime: 30_000,
  });
}
```

- [ ] **Step 7: Whole suite**

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  26 passed (26)`, no eslint output, `✓ built in …`.

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/history.ts web/src/lib/history.test.ts web/src/room/useRoomHistory.ts web/src/test/fixtures.ts
git commit -m "feat(web): merge four streams into one Room thread"
```

---

### Task 18: The eight rows, and the list that anchors them

Every row type in the handoff, at the handoff's measurements, plus the scroll behaviour that makes a live thread bearable: stick to the bottom while you are at the bottom, and stop the moment you scroll up to read something. 120 px of slack, so a rubber-band bounce does not count as scrolling away.

Nothing here fetches, subscribes or decides. Each component takes exactly the fields its `TimelineItem` variant carries, which is what makes the whole set testable in one file.

**Files:**
- Create: `web/src/room/rows/Divider.tsx`, `YouBubble.tsx`, `AlfredRow.tsx`, `ActRow.tsx`, `Tombstone.tsx`, `TranscribingBubble.tsx`, `ThinkingRow.tsx`, `FirstDay.tsx`
- Create: `web/src/room/Timeline.tsx`, `web/src/room/Timeline.test.tsx`

- [ ] **Step 1: Write the failing timeline test**

Create `web/src/room/Timeline.test.tsx` (it covers the eight row components as well — the same arrangement 1a uses for `Layer` and `Sheet`):

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { TimelineItem } from "@/lib/history";
import { Timeline } from "./Timeline";

let scrollHeight = 1000;
let clientHeight = 400;

beforeEach(() => {
  scrollHeight = 1000;
  clientHeight = 400;
  // jsdom does no layout: scrollHeight/clientHeight are 0 and scrollTop is a
  // no-op. Give the three of them real behaviour so the anchoring rule can be
  // tested at all.
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get: () => scrollHeight,
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get: () => clientHeight,
  });
  Object.defineProperty(HTMLElement.prototype, "scrollTop", {
    configurable: true,
    get(this: HTMLElement & { _top?: number }) {
      return this._top ?? 0;
    },
    set(this: HTMLElement & { _top?: number }, value: number) {
      this._top = value;
    },
  });
});

const you: TimelineItem = {
  kind: "you",
  id: "you:1",
  at: "2026-09-07T20:52:03",
  text: "What have I got tomorrow morning?",
  state: "sent",
};

const alfred: TimelineItem = {
  kind: "alfred",
  id: "alfred:1",
  at: "2026-09-07T20:52:06",
  text: "The dentist at nine, sir.\nI'd leave by twenty to.",
  mood: "pleased",
  actions: ["calendar.today", "weather.forecast"],
};

describe("Timeline rows", () => {
  it("draws a divider with its label", () => {
    render(
      <Timeline
        items={[{ kind: "divider", id: "d:1", at: you.at, label: "earlier today" }]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("earlier today")).toBeInTheDocument();
  });

  it("renders your bubble without a stamp when it was sent", () => {
    render(<Timeline items={[you]} firstDayGreeting={null} />);
    expect(screen.getByText("What have I got tomorrow morning?")).toBeInTheDocument();
    expect(screen.queryByText("not sent · will retry when connected")).toBeNull();
  });

  it("says an unsent bubble will be retried", () => {
    render(<Timeline items={[{ ...you, state: "unsent" }]} firstDayGreeting={null} />);
    expect(screen.getByText("not sent · will retry when connected")).toBeInTheDocument();
  });

  it("gives Alfred mood, tools and a time, and keeps his line breaks", () => {
    render(<Timeline items={[alfred]} firstDayGreeting={null} />);
    expect(
      screen.getByText("pleased · calendar.today, weather.forecast · 20:52"),
    ).toBeInTheDocument();
    const text = screen.getByText(/The dentist at nine, sir\./);
    expect(text).toHaveClass("whitespace-pre-line");
  });

  it("says no tools rather than an empty gap", () => {
    render(
      <Timeline items={[{ ...alfred, actions: [], mood: undefined }]} firstDayGreeting={null} />,
    );
    expect(screen.getByText("neutral · no tools · 20:52")).toBeInTheDocument();
  });

  it("marks an error reply as one instead of inventing a mood", () => {
    render(
      <Timeline
        items={[{ ...alfred, text: "No reply in 60 s.", actions: [], error: true }]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("error · 20:52")).toBeInTheDocument();
  });

  it("colours an act row by its stream hue", () => {
    render(
      <Timeline
        items={[
          {
            kind: "act",
            id: "rx:1",
            at: you.at,
            hue: 210,
            text: "movie started, evening",
            meta: "17:58 · reflex · home.light_set",
          },
        ]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("movie started, evening")).toBeInTheDocument();
    expect(screen.getByTestId("act-mark")).toHaveAttribute("data-hue", "210");
  });

  it("strikes a tombstone through", () => {
    render(
      <Timeline
        items={[
          {
            kind: "tombstone",
            id: "tomb:a91f",
            at: you.at,
            title: "Lock unlock",
            meta: "expired 07:46 · not done · asked 07:41",
          },
        ]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("Lock unlock")).toHaveClass("line-through");
    expect(screen.getByText("expired 07:46 · not done · asked 07:41")).toBeInTheDocument();
  });

  it("says how much audio is with the server while transcribing", () => {
    render(
      <Timeline
        items={[{ kind: "transcribing", id: "tr:1", at: you.at, seconds: 2.4 }]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("Transcribing…")).toBeInTheDocument();
    expect(screen.getByText("audio sent · 2.4 s · waiting on server")).toBeInTheDocument();
  });

  it("names which mind is working", () => {
    render(
      <Timeline
        items={[{ kind: "thinking", id: "think", at: you.at, detail: "working" }]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("conscious mind · working")).toBeInTheDocument();
  });

  it("greets an empty house and says why it is empty", () => {
    render(<Timeline items={[]} firstDayGreeting="Good evening, sir." />);
    expect(
      screen.getByText(
        "Good evening, sir. Nothing has happened yet; I'm watching the house and listening for you.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("first run · no memories · 0 routines · hold the button to speak"),
    ).toBeInTheDocument();
  });

  it("shows nothing at all when there is history but no first-day greeting", () => {
    render(<Timeline items={[]} firstDayGreeting={null} />);
    expect(screen.queryByText(/Nothing has happened yet/)).toBeNull();
  });
});

describe("Timeline anchoring", () => {
  it("sits at the bottom as rows arrive", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);
    const list = screen.getByTestId("timeline");
    expect(list.scrollTop).toBe(1000);

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);
    expect(list.scrollTop).toBe(1400);
  });

  it("stops following once the user scrolls up to read", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);
    const list = screen.getByTestId("timeline");

    list.scrollTop = 200; // 1000 - 200 - 400 = 400 px from the bottom
    fireEvent.scroll(list);

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);

    expect(list.scrollTop).toBe(200);
  });

  it("treats a 120 px bounce as still being at the bottom", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);
    const list = screen.getByTestId("timeline");

    list.scrollTop = 480; // 1000 - 480 - 400 = 120, exactly the slack
    fireEvent.scroll(list);

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);

    expect(list.scrollTop).toBe(1400);
  });

  it("follows again once the user returns to the bottom", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);
    const list = screen.getByTestId("timeline");

    list.scrollTop = 100;
    fireEvent.scroll(list);
    list.scrollTop = 600; // back within the slack
    fireEvent.scroll(list);

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);

    expect(list.scrollTop).toBe(1400);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/room/Timeline.test.tsx`
Expected: FAIL — `Failed to resolve import "./Timeline"`.

- [ ] **Step 3: Write the eight row components**

Complete `web/src/room/rows/Divider.tsx`:

```tsx
/** `earlier today` · `yesterday` · `4 Sep` · `new conversation · 20:52`. */
export function Divider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2.5 py-1.5">
      <div className="h-px flex-1" style={{ background: "var(--line)" }} />
      <div className="t-meta">{label}</div>
      <div className="h-px flex-1" style={{ background: "var(--line)" }} />
    </div>
  );
}
```

Complete `web/src/room/rows/YouBubble.tsx`:

```tsx
export interface YouBubbleProps {
  text: string;
  state: "sent" | "unsent";
}

/** Right-aligned, max 280, radius `16 16 4 16`. Unsent fades and says so. */
export function YouBubble({ text, state }: YouBubbleProps) {
  const unsent = state === "unsent";
  return (
    <div
      className="t-you max-w-[280px] self-end rounded-[16px_16px_4px_16px] border px-3.5 py-2.5"
      style={{ borderColor: "var(--line)", opacity: unsent ? 0.6 : 1 }}
    >
      {text}
      {unsent ? (
        <div className="mt-1 font-mono text-[10.5px]" style={{ color: "var(--muted)" }}>
          not sent · will retry when connected
        </div>
      ) : null}
    </div>
  );
}
```

Complete `web/src/room/rows/AlfredRow.tsx`:

```tsx
import { hhmm } from "@/lib/format";
import type { Mood } from "@/lib/types";

export interface AlfredRowProps {
  text: string;
  at: string;
  mood?: Mood;
  actions: string[];
  error?: boolean;
}

/**
 * No bubble — Alfred's voice is the page, one size above yours. Plain text with
 * `pre-line`, never markdown (decision 3), and a meta line naming the mood, the
 * tools he ran and when.
 */
export function AlfredRow({ text, at, mood, actions, error }: AlfredRowProps) {
  // An error frame has no mood and ran no tools; `neutral · no tools` would be
  // a small lie about a failure.
  const meta = error
    ? `error · ${hhmm(at)}`
    : [mood ?? "neutral", actions.length > 0 ? actions.join(", ") : "no tools", hhmm(at)].join(
        " · ",
      );

  return (
    <div className="flex max-w-[330px] flex-col gap-[7px]">
      <div
        className="t-alfred whitespace-pre-line"
        style={{ color: error ? "var(--fg2)" : "var(--fg)" }}
      >
        {text}
      </div>
      <div className="t-meta">{meta}</div>
    </div>
  );
}
```

Complete `web/src/room/rows/ActRow.tsx`:

```tsx
export interface ActRowProps {
  /** 120 trigger (EV) · 210 reflex (RX) · 255 notification (NT). */
  hue: 120 | 210 | 255;
  text: string;
  meta: string;
}

/**
 * Something Alfred did while you were not asking. Same thread, quieter voice:
 * `fg2` at 14.5, hairlines above and below, and an 8 px mark in the stream's own
 * hue so which mind acted is legible at a glance (spec §5.2.4).
 */
export function ActRow({ hue, text, meta }: ActRowProps) {
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
        style={{ background: `oklch(0.62 0.11 ${hue})` }}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="t-row" style={{ color: "var(--fg2)" }}>
          {text}
        </div>
        <div className="t-meta">{meta}</div>
      </div>
    </div>
  );
}
```

Complete `web/src/room/rows/Tombstone.tsx`:

```tsx
export interface TombstoneProps {
  title: string;
  meta: string;
}

/**
 * What is left of an approval nobody answered. Struck through, on `surface`, at
 * 80% — present, finished, and unmistakably not done.
 */
export function Tombstone({ title, meta }: TombstoneProps) {
  return (
    <div
      className="flex items-start gap-3 rounded-xl px-3 py-2.5 opacity-80"
      style={{ background: "var(--surface)" }}
    >
      <span
        aria-hidden="true"
        className="mt-[5px] box-border h-2 w-2 shrink-0 rounded-full border-[1.5px]"
        style={{ borderColor: "var(--muted)" }}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="t-row line-through" style={{ color: "var(--fg2)" }}>
          {title}
        </div>
        <div className="t-meta">{meta}</div>
      </div>
    </div>
  );
}
```

Complete `web/src/room/rows/TranscribingBubble.tsx`:

```tsx
/**
 * The gap between releasing the button and the server telling you what it heard.
 * Dashed, because it is not yet a message: nothing has been transcribed, and the
 * count of seconds is the only true thing the client knows about it.
 */
export function TranscribingBubble({ seconds }: { seconds: number }) {
  return (
    <div
      className="t-you max-w-[280px] self-end rounded-[16px_16px_4px_16px] border border-dashed px-3.5 py-2.5 italic"
      style={{ borderColor: "var(--line)", color: "var(--muted)" }}
    >
      Transcribing…
      <div className="mt-1 font-mono text-[10.5px] not-italic">
        audio sent · {seconds.toFixed(1)} s · waiting on server
      </div>
    </div>
  );
}
```

Complete `web/src/room/rows/ThinkingRow.tsx`:

```tsx
const DELAYS = [0, 0.2, 0.4];

/** Three breathing dots, staggered, and the name of the mind that is busy. */
export function ThinkingRow({ detail }: { detail: string }) {
  return (
    <div className="flex flex-col gap-[7px]">
      <div aria-hidden="true" className="flex h-[22px] items-center gap-1">
        {DELAYS.map((delay) => (
          <span
            key={delay}
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: "var(--accent)",
              animation: `breathe 1.2s ${delay}s ease-in-out infinite`,
            }}
          />
        ))}
      </div>
      <div className="t-meta">conscious mind · {detail}</div>
    </div>
  );
}
```

Complete `web/src/room/rows/FirstDay.tsx`:

```tsx
/**
 * The empty Room, on the morning it was installed. The greeting comes from the
 * same hour table the headline uses, so the two agree.
 */
export function FirstDay({ greeting }: { greeting: string }) {
  return (
    <div className="flex flex-1 flex-col justify-center gap-2.5 pb-[60px]">
      <div className="t-alfred">
        {greeting} Nothing has happened yet; I&apos;m watching the house and listening for you.
      </div>
      <div className="t-meta">first run · no memories · 0 routines · hold the button to speak</div>
    </div>
  );
}
```

- [ ] **Step 4: Write `web/src/room/Timeline.tsx`**

Complete file:

```tsx
import { useEffect, useRef } from "react";
import type { TimelineItem } from "@/lib/history";
import { ActRow } from "@/room/rows/ActRow";
import { AlfredRow } from "@/room/rows/AlfredRow";
import { Divider } from "@/room/rows/Divider";
import { FirstDay } from "@/room/rows/FirstDay";
import { ThinkingRow } from "@/room/rows/ThinkingRow";
import { Tombstone } from "@/room/rows/Tombstone";
import { TranscribingBubble } from "@/room/rows/TranscribingBubble";
import { YouBubble } from "@/room/rows/YouBubble";

/**
 * How far from the bottom still counts as "at the bottom". Wide enough that an
 * iOS rubber-band bounce does not read as the user scrolling away, narrow enough
 * that one row of deliberate scrolling does.
 */
const SCROLL_ANCHOR_PX = 120;

export interface TimelineProps {
  items: TimelineItem[];
  /** Non-null only on a first run with nothing in the thread at all. */
  firstDayGreeting: string | null;
}

function Row({ item }: { item: TimelineItem }) {
  switch (item.kind) {
    case "divider":
      return <Divider label={item.label} />;
    case "you":
      return <YouBubble text={item.text} state={item.state} />;
    case "alfred":
      return (
        <AlfredRow
          text={item.text}
          at={item.at}
          mood={item.mood}
          actions={item.actions}
          error={item.error}
        />
      );
    case "act":
      return <ActRow hue={item.hue} text={item.text} meta={item.meta} />;
    case "tombstone":
      return <Tombstone title={item.title} meta={item.meta} />;
    case "transcribing":
      return <TranscribingBubble seconds={item.seconds} />;
    case "thinking":
      return <ThinkingRow detail={item.detail} />;
  }
}

export function Timeline({ items, firstDayGreeting }: TimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // Ref, not state: whether we are following the bottom must not re-render the
  // list, and the scroll handler fires on every frame of a flick.
  const followRef = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !followRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [items]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_ANCHOR_PX;
  };

  return (
    <div
      ref={scrollRef}
      data-testid="timeline"
      onScroll={onScroll}
      className="relative z-[1] flex flex-1 flex-col gap-4 overflow-y-auto px-6 pt-[22px] pb-3"
    >
      {items.length === 0 && firstDayGreeting ? <FirstDay greeting={firstDayGreeting} /> : null}
      {items.map((item) => (
        <Row key={item.id} item={item} />
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Run the timeline test and the suite**

Run: `npm test -- src/room/Timeline.test.tsx`
Expected: `Test Files  1 passed (1)`, 16 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  27 passed (27)`, no eslint output, `✓ built in …`.

- [ ] **Step 6: Commit**

```bash
git add web/src/room/rows web/src/room/Timeline.tsx web/src/room/Timeline.test.tsx
git commit -m "feat(web): the eight timeline rows and the list that anchors them"
```

---

### Task 19: `useRoom` — the live half of the thread

History is a read; this is everything that happens after it. `useRoom` owns the rows created in this session, the queue of things you said while the house could not hear you, and the merge that puts both halves in one chronological list.

Four rules it exists to enforce:

1. **An unsent message says so and comes back.** Offline, a send appends an `unsent` bubble, persists it to `alfred.unsent`, and retries in order the moment the socket reports online. If the socket refuses mid-queue, the retry stops there rather than reordering the conversation.
2. **A turn in flight is visible.** Sending adds a thinking row; the reply, the error, or sixty seconds of silence removes it. Sixty seconds is a client-side truth — the server's own `publish_and_wait` timeout is 60 s (`core/channels/web_server.py`), so past that there is nothing still coming.
3. **A transcription lands in place.** The dashed bubble is replaced by what the server heard, not appended below it. An STT failure arrives as a `response`, not a `transcription`, so the response path clears the dashed bubble too — otherwise it hangs there for ever.
4. **A confirmation notification is not a message.** Frames carrying `metadata.pending_action_id` belong to the Door and are dropped here.

**Files:**
- Create: `web/src/room/useRoom.ts`, `web/src/room/useRoom.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `web/src/room/useRoom.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TimelineItem } from "@/lib/history";
import type { ChatServerMessage } from "@/lib/types";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { UNSENT_KEY, useRoom, type UseRoomOptions } from "./useRoom";

const { chats, playWavBase64Mock } = vi.hoisted(() => ({
  chats: [] as unknown[],
  playWavBase64Mock: vi.fn(),
}));

let sendSucceeds = true;

vi.mock("@/lib/audio", () => ({
  playWavBase64: playWavBase64Mock,
  installAudioUnlock: () => () => {},
  getAudioContext: () => null,
}));

vi.mock("@/lib/chat-socket", () => {
  class ChatSocket {
    onstatus: (status: string) => void = () => {};
    listeners = new Set<(msg: ChatServerMessage) => void>();
    connect = vi.fn();
    close = vi.fn();
    sendText = vi.fn(() => sendSucceeds);
    sendAudio = vi.fn(() => sendSucceeds);
    constructor() {
      chats.push(this);
    }
    listen(fn: (msg: ChatServerMessage) => void): () => void {
      this.listeners.add(fn);
      return () => void this.listeners.delete(fn);
    }
    deliver(msg: ChatServerMessage): void {
      for (const fn of [...this.listeners]) fn(msg);
    }
  }
  return { ChatSocket };
});

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

interface FakeChat {
  onstatus: (status: string) => void;
  sendText: ReturnType<typeof vi.fn>;
  sendAudio: ReturnType<typeof vi.fn>;
  deliver: (msg: ChatServerMessage) => void;
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <ConnectionProvider>{children}</ConnectionProvider>
    </QueryClientProvider>
  );
}

// `online` is the socket's word, not a prop: the provider hears it from the
// ChatSocket, so the harness says it the same way.
function renderRoom({ online, ...props }: UseRoomOptions & { online: boolean }) {
  const view = renderHook((options: UseRoomOptions) => useRoom(options), {
    wrapper: Wrapper,
    initialProps: props,
  });
  const chat = chats.at(-1) as FakeChat;
  if (online) act(() => chat.onstatus("online"));
  return { ...view, chat };
}

// Dated well in the past: useRoom sorts by timestamp, and the live rows are
// stamped with the real clock, so history must not be "later than now".
const historyRow: TimelineItem = {
  kind: "alfred",
  id: "alfred:history",
  at: "2026-09-01T20:52:06",
  text: "The dentist at nine, sir.",
  mood: "pleased",
  actions: ["calendar.today"],
};

function kinds(items: TimelineItem[]): string[] {
  return items.filter((item) => item.kind !== "divider").map((item) => item.kind);
}

beforeEach(() => {
  sendSucceeds = true;
  playWavBase64Mock.mockClear();
  localStorage.clear();
  (chats.at(-1) as FakeChat | undefined)?.sendText.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
  localStorage.clear();
});

describe("useRoom — the merged thread", () => {
  it("keeps history and live rows in one chronological list", () => {
    const { result, chat } = renderRoom({ history: [historyRow], online: true });

    act(() => result.current.sendText("Is the back door locked?"));

    expect(kinds(result.current.items)).toEqual(["alfred", "you", "thinking"]);
    expect(chat.sendText).toHaveBeenCalledWith("Is the back door locked?");
  });

  it("puts a day divider in front of the thread", () => {
    const { result } = renderRoom({ history: [historyRow], online: true });
    expect(result.current.items[0].kind).toBe("divider");
  });

  it("ignores an empty or whitespace-only draft", () => {
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() => result.current.sendText("   "));

    expect(chat.sendText).not.toHaveBeenCalled();
    expect(result.current.items).toHaveLength(0);
  });
});

describe("useRoom — sending", () => {
  it("marks a message unsent and remembers it when the house cannot hear", () => {
    sendSucceeds = false;
    const { result } = renderRoom({ history: [], online: false });

    act(() => result.current.sendText("Turn the hall light off"));

    const you = result.current.items.find((item) => item.kind === "you")!;
    expect(you.kind === "you" && you.state).toBe("unsent");
    // No thinking row: nothing is in flight.
    expect(kinds(result.current.items)).toEqual(["you"]);
    expect(JSON.parse(localStorage.getItem(UNSENT_KEY) ?? "[]")).toHaveLength(1);
  });

  it("retries the queue in order when the connection returns", () => {
    sendSucceeds = false;
    const { result, chat } = renderRoom({ history: [], online: false });

    act(() => result.current.sendText("first"));
    act(() => result.current.sendText("second"));
    chat.sendText.mockClear();

    sendSucceeds = true;
    act(() => chat.onstatus("online"));

    expect(chat.sendText.mock.calls.map((call) => call[0])).toEqual(["first", "second"]);
    expect(
      result.current.items.every((item) => item.kind !== "you" || item.state === "sent"),
    ).toBe(true);
    expect(JSON.parse(localStorage.getItem(UNSENT_KEY) ?? "[]")).toHaveLength(0);
  });

  it("stops the retry at the first refusal rather than reordering", () => {
    sendSucceeds = false;
    const { result, chat } = renderRoom({ history: [], online: false });

    act(() => result.current.sendText("first"));
    act(() => result.current.sendText("second"));
    chat.sendText.mockClear();
    chat.sendText.mockImplementationOnce(() => true).mockImplementationOnce(() => false);

    act(() => chat.onstatus("online"));

    const states = result.current.items
      .filter((item) => item.kind === "you")
      .map((item) => (item.kind === "you" ? item.state : ""));
    expect(states).toEqual(["sent", "unsent"]);
  });

  it("restores the queue after a cold launch", () => {
    localStorage.setItem(
      UNSENT_KEY,
      JSON.stringify([
        { kind: "you", id: "you:cold", at: "2026-09-07T21:00:00", text: "held over", state: "unsent" },
      ]),
    );

    const { result } = renderRoom({ history: [], online: false });

    expect(result.current.items.some((item) => item.kind === "you" && item.text === "held over")).toBe(
      true,
    );
  });

  it("ignores a corrupt queue rather than refusing to start", () => {
    localStorage.setItem(UNSENT_KEY, "{not json");
    const { result } = renderRoom({ history: [], online: false });
    expect(result.current.items).toHaveLength(0);
  });
});

describe("useRoom — what comes back", () => {
  it("replaces the thinking row with Alfred's reply and speaks it", () => {
    const { result, chat } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("Anything tomorrow?"));

    act(() =>
      chat.deliver({
        type: "response",
        text: "The dentist at nine, sir.",
        session_id: "s_9f2",
        actions_taken: ["calendar.today"],
        mood: "pleased",
        audio: "UklGRg==",
      }),
    );

    expect(kinds(result.current.items)).toEqual(["you", "alfred"]);
    const alfred = result.current.items.find((item) => item.kind === "alfred")!;
    expect(alfred.kind === "alfred" && alfred.mood).toBe("pleased");
    expect(playWavBase64Mock).toHaveBeenCalledWith("UklGRg==");
    expect(result.current.thinking).toBe(false);
  });

  it("renders an error frame as an error row", () => {
    const { result, chat } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("Anything tomorrow?"));

    act(() => chat.deliver({ type: "error", text: "Expected a JSON object" }));

    const alfred = result.current.items.find((item) => item.kind === "alfred")!;
    expect(alfred.kind === "alfred" && alfred.error).toBe(true);
    expect(alfred.kind === "alfred" && alfred.text).toBe("Expected a JSON object");
  });

  it("replaces the dashed bubble with what the server heard", () => {
    const { result, chat } = renderRoom({ history: [], online: true });
    act(() => result.current.sendAudio("data:audio/mp4;base64,AAAA", 2.4));
    expect(kinds(result.current.items)).toEqual(["transcribing"]);

    act(() =>
      chat.deliver({ type: "transcription", text: "Is the back door locked?", session_id: "s_9f2" }),
    );

    expect(kinds(result.current.items)).toEqual(["you", "thinking"]);
    const you = result.current.items.find((item) => item.kind === "you")!;
    expect(you.kind === "you" && you.text).toBe("Is the back door locked?");
  });

  it("clears the dashed bubble when transcription failed outright", () => {
    const { result, chat } = renderRoom({ history: [], online: true });
    act(() => result.current.sendAudio("data:audio/mp4;base64,AAAA", 1.2));

    // The STT failure path answers with a `response`, never a `transcription`.
    act(() =>
      chat.deliver({
        type: "response",
        text: "I'm afraid I couldn't make out what was said.",
        session_id: "s_9f2",
      }),
    );

    expect(kinds(result.current.items)).toEqual(["alfred"]);
  });

  it("sends nothing and shows nothing when the socket refuses the audio", () => {
    sendSucceeds = false;
    const { result } = renderRoom({ history: [], online: false });

    act(() => result.current.sendAudio("data:audio/mp4;base64,AAAA", 2.4));

    expect(result.current.items).toHaveLength(0);
  });

  it("turns a notification into a quiet act row", () => {
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Bins go out tonight",
        body: "Collection moved to Friday.",
        urgency: "important",
        notification_id: "ntf-9",
        metadata: {},
      }),
    );

    const act_ = result.current.items.find((item) => item.kind === "act")!;
    expect(act_.kind === "act" && act_.hue).toBe(255);
    expect(act_.kind === "act" && act_.text).toBe("Bins go out tonight");
    expect(act_.kind === "act" && act_.meta).toMatch(/^\d{2}:\d{2} · live · important$/);
  });

  it("leaves a confirmation request to the Door", () => {
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Confirmation required",
        body: "Alfred wants to run 'home.lock_unlock' on home-service — confirm?",
        urgency: "urgent",
        notification_id: "ntf-8",
        metadata: { pending_action_id: "a91f3c2e" },
      }),
    );

    expect(result.current.items).toHaveLength(0);
  });

  it("speaks an urgent notification and stays quiet for the rest", () => {
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Water where it should not be",
        body: "The utility-room sensor is wet.",
        urgency: "urgent",
        notification_id: "ntf-7",
        audio: "UklGRg==",
        metadata: {},
      }),
    );
    expect(playWavBase64Mock).toHaveBeenCalledWith("UklGRg==");

    playWavBase64Mock.mockClear();
    act(() =>
      chat.deliver({
        type: "notification",
        title: "Bins go out tonight",
        body: "Collection moved to Friday.",
        urgency: "important",
        notification_id: "ntf-6",
        audio: "UklGRg==",
        metadata: {},
      }),
    );
    expect(playWavBase64Mock).not.toHaveBeenCalled();
    expect(result.current.items.filter((item) => item.kind === "act")).toHaveLength(2);
  });
});

describe("useRoom — silence", () => {
  it("gives up after sixty seconds and says so", () => {
    vi.useFakeTimers();
    const { result } = renderRoom({ history: [], online: true });

    act(() => result.current.sendText("Anything tomorrow?"));
    expect(result.current.thinking).toBe(true);

    act(() => void vi.advanceTimersByTime(59_999));
    expect(result.current.thinking).toBe(true);

    act(() => void vi.advanceTimersByTime(1));

    expect(result.current.thinking).toBe(false);
    const alfred = result.current.items.find((item) => item.kind === "alfred")!;
    expect(alfred.kind === "alfred" && alfred.text).toBe("No reply in 60 s.");
    expect(alfred.kind === "alfred" && alfred.error).toBe(true);
  });

  it("cancels the timeout when the reply arrives in time", () => {
    vi.useFakeTimers();
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() => result.current.sendText("Anything tomorrow?"));
    act(() =>
      chat.deliver({ type: "response", text: "Nothing, sir.", session_id: "s_9f2" }),
    );
    act(() => void vi.advanceTimersByTime(120_000));

    expect(
      result.current.items.filter((item) => item.kind === "alfred"),
    ).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/room/useRoom.test.tsx`
Expected: FAIL — `Failed to resolve import "./useRoom"`.

- [ ] **Step 3: Write `web/src/room/useRoom.ts`**

Complete file:

```ts
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { playWavBase64 } from "@/lib/audio";
import { hhmm } from "@/lib/format";
import { withDividers, type TimelineItem } from "@/lib/history";
import { onVisible } from "@/lib/lifecycle";
import type { ChatServerMessage } from "@/lib/types";
import { useConnection } from "@/shell/ConnectionProvider";

/** Messages typed while the house was unreachable, kept across a cold launch. */
export const UNSENT_KEY = "alfred.unsent";
/** The server's own `publish_and_wait` timeout. Past this, nothing is still coming. */
export const NO_REPLY_MS = 60_000;

const THINKING_ID = "thinking";
const TRANSCRIBING_ID = "transcribing";

// A monotonic counter rather than a random id: stable React keys, and a
// deterministic order in tests. It only has to be unique within one page load.
let seq = 0;
function uid(prefix: string): string {
  seq += 1;
  return `${prefix}:${seq}`;
}

type YouItem = Extract<TimelineItem, { kind: "you" }>;

function isUnsent(item: TimelineItem): item is YouItem {
  return item.kind === "you" && item.state === "unsent";
}

function readUnsent(): TimelineItem[] {
  try {
    const raw = localStorage.getItem(UNSENT_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is YouItem =>
        !!item &&
        typeof item === "object" &&
        (item as YouItem).kind === "you" &&
        typeof (item as YouItem).text === "string",
    );
  } catch {
    // Corrupt or unavailable storage loses the queue, never the app.
    return [];
  }
}

function writeUnsent(items: TimelineItem[]): void {
  try {
    localStorage.setItem(UNSENT_KEY, JSON.stringify(items.filter(isUnsent)));
  } catch {
    // Private mode. The queue survives in memory for this session only.
  }
}

export interface UseRoomOptions {
  /** `toTimelineItems(useRoomHistory().data)` — the thread as it stood on open. */
  history: TimelineItem[];
  /** Expired and already-answered approvals, from `tombstoneItems` (Task 25). */
  tombstones?: TimelineItem[];
}

export interface RoomValue {
  items: TimelineItem[];
  /** A turn is in flight: something was sent and nothing has come back. */
  thinking: boolean;
  sendText: (text: string) => void;
  sendAudio: (dataUrl: string, seconds: number) => void;
}

export function useRoom({ history, tombstones }: UseRoomOptions): RoomValue {
  const { chat, online, subscribeOnline } = useConnection();
  const [live, setLive] = useState<TimelineItem[]>(readUnsent);

  // Read once at mount and again whenever the app comes back to the foreground.
  // Day dividers are relative to it, and a PWA left open across midnight would
  // otherwise still be calling yesterday "earlier today".
  const [now, setNow] = useState(() => new Date());
  useEffect(() => onVisible(() => setNow(new Date())), []);

  useEffect(() => {
    writeUnsent(live);
  }, [live]);

  const sendText = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (!text) return;
      const at = new Date().toISOString();
      const sent = online && chat.sendText(text);
      setLive((current) => {
        // Annotated: TS infers a type predicate from the filter and would
        // otherwise refuse to push a thinking row back in.
        const next: TimelineItem[] = current.filter((item) => item.kind !== "thinking");
        next.push({ kind: "you", id: uid("you"), at, text, state: sent ? "sent" : "unsent" });
        if (sent) next.push({ kind: "thinking", id: THINKING_ID, at, detail: "working" });
        return next;
      });
    },
    [chat, online],
  );

  const sendAudio = useCallback(
    (dataUrl: string, seconds: number) => {
      // Nothing is shown unless the frame actually left: a dashed bubble waiting
      // on a server that never heard the audio would never resolve.
      if (!chat.sendAudio(dataUrl)) return;
      const at = new Date().toISOString();
      setLive((current) => [
        ...current.filter((item) => item.kind !== "transcribing"),
        { kind: "transcribing", id: TRANSCRIBING_ID, at, seconds },
      ]);
    },
    [chat],
  );

  // Retry the queue, in order, the moment the socket says it is online. Stopping
  // at the first refusal keeps the conversation in the order it was written.
  // Driven by the socket's own notification rather than the `online` flag: the
  // sends are an external side effect and the rows they settle are recorded in
  // the same callback, so the effect itself only subscribes. The queue is read
  // through a ref so that subscription is made once, not per timeline change.
  const liveRef = useRef(live);
  useEffect(() => {
    liveRef.current = live;
  }, [live]);

  useEffect(
    () =>
      subscribeOnline(() => {
        const delivered = new Set<string>();
        for (const item of liveRef.current.filter(isUnsent)) {
          if (!chat.sendText(item.text)) break;
          delivered.add(item.id);
        }
        if (delivered.size === 0) return;

        setLive((current) =>
          current.map((item) =>
            item.kind === "you" && delivered.has(item.id)
              ? { kind: "you", id: item.id, at: item.at, text: item.text, state: "sent" }
              : item,
          ),
        );
      }),
    [subscribeOnline, chat],
  );

  useEffect(() => {
    return chat.listen((msg: ChatServerMessage) => {
      const at = new Date();
      const iso = at.toISOString();

      if (msg.type === "response") {
        setLive((current) => [
          // Both transients go: a failed transcription answers with a `response`
          // and never a `transcription`, so this is the only thing that clears it.
          ...current.filter((item) => item.kind !== "thinking" && item.kind !== "transcribing"),
          {
            kind: "alfred",
            id: uid("alfred"),
            at: iso,
            text: msg.text,
            mood: msg.mood,
            actions: msg.actions_taken ?? [],
          },
        ]);
        if (msg.audio) playWavBase64(msg.audio);
        return;
      }

      if (msg.type === "error") {
        setLive((current) => [
          ...current.filter((item) => item.kind !== "thinking" && item.kind !== "transcribing"),
          { kind: "alfred", id: uid("alfred"), at: iso, text: msg.text, actions: [], error: true },
        ]);
        return;
      }

      if (msg.type === "transcription") {
        setLive((current) => [
          ...current.filter((item) => item.kind !== "transcribing"),
          { kind: "you", id: uid("you"), at: iso, text: msg.text, state: "sent" },
          { kind: "thinking", id: THINKING_ID, at: iso, detail: "working" },
        ]);
        return;
      }

      if (msg.type === "notification") {
        // A confirmation request has a fuse and a Door; it is not a thread row.
        if (typeof msg.metadata?.pending_action_id === "string") return;
        setLive((current) => [
          ...current,
          {
            kind: "act",
            id: uid("nt"),
            at: iso,
            hue: 255,
            text: msg.title,
            // `live`, not a source: the /ws notification frame carries none
            // (core/notifications/adapters/websocket.py). The same notification
            // re-read from the stream later shows its real one.
            meta: `${hhmm(at)} · live · ${msg.urgency}`,
          },
        ]);
        if (msg.audio && msg.urgency === "urgent") playWavBase64(msg.audio);
      }
    });
  }, [chat]);

  // Keyed on the thinking row's own timestamp, so an unrelated notification
  // arriving at 59 s does not quietly restart the countdown.
  const thinkingAt = live.find((item) => item.kind === "thinking")?.at ?? null;

  useEffect(() => {
    if (!thinkingAt) return;
    const timer = setTimeout(() => {
      setLive((current) => [
        ...current.filter((item) => item.kind !== "thinking"),
        {
          kind: "alfred",
          id: uid("alfred"),
          at: new Date().toISOString(),
          text: "No reply in 60 s.",
          actions: [],
          error: true,
        },
      ]);
    }, NO_REPLY_MS);
    return () => clearTimeout(timer);
  }, [thinkingAt]);

  const items = useMemo(() => {
    const merged = [...history, ...(tombstones ?? []), ...live].sort(
      (a, b) => Date.parse(a.at) - Date.parse(b.at),
    );
    return withDividers(merged, now);
  }, [history, tombstones, live, now]);

  return { items, thinking: thinkingAt !== null, sendText, sendAudio };
}
```

- [ ] **Step 4: Run the test and the suite**

Run: `npm test -- src/room/useRoom.test.tsx`
Expected: `Test Files  1 passed (1)`, 18 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  28 passed (28)`, no eslint output, `✓ built in …`.

- [ ] **Step 5: Commit**

```bash
git add web/src/room/useRoom.ts web/src/room/useRoom.test.tsx
git commit -m "feat(web): live Room state — sending, the unsent queue and the reply timeout"
```

---

### Task 20: The composer

50 px field, and to its right either the send button or the hold-to-talk slot — never both. The keyboard is the interesting part: `interactive-widget=resizes-content` (1a, Task 2) makes iOS shrink the layout rather than cover it, `--keyboard-inset` (1a, Task 4) says by how much, and `.pb-keyboard` turns that into padding. The one thing missing is that the home-indicator inset must *stop* being added once the keyboard is up — the keyboard is already covering that strip, and adding both leaves a visible band of background under the field.

The hold-to-talk button itself is Task 22; this task takes it as a slot so the composer can be tested on its own.

**Files:**
- Create: `web/src/room/Composer.tsx`, `web/src/room/Composer.test.tsx`
- Modify: `web/src/index.css`

- [ ] **Step 1: Write the failing test**

Create `web/src/room/Composer.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";

function setViewport(height: number, innerHeight = 852): void {
  Object.defineProperty(window, "innerHeight", { configurable: true, value: innerHeight });
  Object.defineProperty(window, "visualViewport", {
    configurable: true,
    value: Object.assign(new EventTarget(), { height, offsetTop: 0 }),
  });
}

const originalViewport = window.visualViewport;

afterEach(() => {
  Object.defineProperty(window, "visualViewport", {
    configurable: true,
    value: originalViewport,
  });
});

describe("Composer", () => {
  it("invites a message and offers the hold slot while empty", () => {
    render(
      <Composer online onSend={() => {}} hold={<button type="button">hold to talk</button>} />,
    );

    expect(screen.getByPlaceholderText("Ask or tell Alfred")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "hold to talk" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  });

  it("swaps the hold slot for send as soon as there is a draft", async () => {
    const user = userEvent.setup();
    render(
      <Composer online onSend={() => {}} hold={<button type="button">hold to talk</button>} />,
    );

    await user.type(screen.getByPlaceholderText("Ask or tell Alfred"), "hello");

    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "hold to talk" })).toBeNull();
  });

  it("sends on the button and clears the field", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online onSend={onSend} hold={null} />);
    const field = screen.getByPlaceholderText("Ask or tell Alfred");

    await user.type(field, "Turn the hall light off");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(onSend).toHaveBeenCalledWith("Turn the hall light off");
    expect(field).toHaveValue("");
  });

  it("sends on Enter", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online onSend={onSend} hold={null} />);

    await user.type(screen.getByPlaceholderText("Ask or tell Alfred"), "Anything tomorrow?{Enter}");

    expect(onSend).toHaveBeenCalledWith("Anything tomorrow?");
  });

  it("refuses to send an empty draft", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online onSend={onSend} hold={null} />);

    await user.type(screen.getByPlaceholderText("Ask or tell Alfred"), "   {Enter}");

    expect(onSend).not.toHaveBeenCalled();
  });

  it("says what will happen to a message typed offline, and still takes it", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online={false} onSend={onSend} hold={null} />);
    const field = screen.getByPlaceholderText("Offline · will send when connected");

    await user.type(field, "Turn the hall light off{Enter}");

    // Not disabled: the queue is the point. `useRoom` marks it unsent and retries.
    expect(field).toBeEnabled();
    expect(onSend).toHaveBeenCalledWith("Turn the hall light off");
  });

  it("drops the home-indicator inset once the keyboard is up", () => {
    setViewport(500); // 852 - 500 = 352 px of keyboard
    const { container } = render(<Composer online onSend={() => {}} hold={null} />);
    const row = container.querySelector(".pb-keyboard")!;
    expect(row).toHaveClass("keyboard-up");
  });

  it("keeps the inset while the keyboard is down", () => {
    setViewport(852);
    const { container } = render(<Composer online onSend={() => {}} hold={null} />);
    const row = container.querySelector(".pb-keyboard")!;
    expect(row).not.toHaveClass("keyboard-up");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/room/Composer.test.tsx`
Expected: FAIL — `Failed to resolve import "./Composer"`.

- [ ] **Step 3: Write `web/src/room/Composer.tsx`**

Complete file:

```tsx
import { useState, type KeyboardEvent, type ReactNode } from "react";
import { useKeyboardOpen } from "@/lib/viewport";

export interface ComposerProps {
  online: boolean;
  onSend: (text: string) => void;
  /** The hold-to-talk button, shown whenever there is no draft. */
  hold: ReactNode;
}

/**
 * 50 px field, 56 px action. Never disabled offline: a message typed while the
 * house is unreachable is queued and retried, and a dead input would hide that.
 */
export function Composer({ online, onSend, hold }: ComposerProps) {
  const [draft, setDraft] = useState("");
  const keyboardOpen = useKeyboardOpen();
  const hasDraft = draft.trim().length > 0;

  function send(): void {
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "Enter") {
      event.preventDefault();
      send();
    }
  }

  return (
    // Two elements on purpose: the outer one owns the keyboard and safe-area
    // padding (a Tailwind padding utility on the same element would out-rank the
    // `@layer components` rule and silently drop the inset), the inner one the
    // handoff's own `0 20 8`.
    <div className={`pb-keyboard relative z-[1] ${keyboardOpen ? "keyboard-up" : ""}`}>
      <div className="flex items-center gap-2.5 px-5 pb-2">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={online ? "Ask or tell Alfred" : "Offline · will send when connected"}
          aria-label="Message Alfred"
          autoComplete="off"
          autoCapitalize="sentences"
          autoCorrect="on"
          enterKeyHint="send"
          className="h-[50px] min-w-0 flex-1 rounded-[25px] border px-[18px] text-[15px] outline-none"
          style={{ background: "var(--field)", borderColor: "var(--line)", color: "var(--fg)" }}
        />

        {hasDraft ? (
          <button
            type="button"
            onClick={send}
            aria-label="Send"
            className="flex h-14 w-14 shrink-0 items-center justify-center rounded-[28px] border-0"
            style={{ background: "var(--ink)", color: "var(--paper)" }}
          >
            <span
              aria-hidden="true"
              className="block h-2.5 w-2.5 border-t-2 border-r-2 border-current"
              style={{ transform: "rotate(-45deg) translate(-1px, 1px)" }}
            />
          </button>
        ) : (
          hold
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Add the keyboard-up rule to `web/src/index.css`**

Inside the `@layer components` block, directly after the existing `.pb-keyboard` rule, insert:

```css
  /* With the keyboard up, iOS has already covered the home-indicator strip.
     Adding `env(safe-area-inset-bottom)` on top of `--keyboard-inset` then leaves
     a visible band of background between the field and the keys. */
  .pb-keyboard.keyboard-up {
    padding-bottom: var(--keyboard-inset, 0px);
  }
```

- [ ] **Step 5: Run the tests and the suite**

Run: `npm test -- src/room/Composer.test.tsx`
Expected: `Test Files  1 passed (1)`, 8 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  29 passed (29)`, no eslint output, `✓ built in …`.

```bash
grep -o "keyboard-up" dist/assets/*.css | wc -l
```

Expected: `1` or more — the rule survived Tailwind's build (it is plain CSS in a layer, never purged).

- [ ] **Step 6: Commit**

```bash
git add web/src/room/Composer.tsx web/src/room/Composer.test.tsx web/src/index.css
git commit -m "feat(web): the composer, and the keyboard inset it sits above"
```

---

### Task 21: One unlocked AudioContext, and a recorder Safari will speak to

Two iOS constraints, both of which break the app rather than degrade it.

**§4.7 — audio needs a gesture.** iOS refuses `Audio.play()` and leaves an `AudioContext` suspended until the user has touched the page. The outgoing client made a fresh `new Audio()` per reply, so the *first* spoken reply after launch was silently dropped — the exact failure `docs/qa-backlog/audio-context-unlock-before-first-urgent-notification.md` records. One context, unlocked on the first `pointerdown` or `keydown` anywhere in the app, fixes it: everything after that plays through a context iOS has already blessed.

**§4.8 — Safari has no WebM.** The outgoing `VoiceButton` hard-coded `audio/webm;codecs=opus`, which throws in the `MediaRecorder` constructor on iOS. Feature-detect `audio/mp4`, then `audio/aac`, then let the browser choose; the backend's `_decode_audio` already accepts aac/m4a/wav.

The recorder also owns the `AnalyserNode` (fftSize 256, smoothing 0.6) that Task 22 hands to `PresenceSignal`, because it is the same microphone stream and must be torn down with it.

**Files:**
- Rewrite: `web/src/lib/audio.ts`
- Create: `web/src/lib/audio.test.ts`, `web/src/lib/recorder.ts`, `web/src/lib/recorder.test.ts`

- [ ] **Step 1: Write the failing audio test**

Create `web/src/lib/audio.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

interface Started {
  starts: number;
  decoded: number;
  resumed: number;
}

let started: Started;
let decodeFails = false;

class FakeAudioContext {
  state: AudioContextState = "suspended";
  destination = {} as AudioDestinationNode;

  resume(): Promise<void> {
    started.resumed += 1;
    this.state = "running";
    return Promise.resolve();
  }

  createBuffer(): AudioBuffer {
    return {} as AudioBuffer;
  }

  createBufferSource(): AudioBufferSourceNode {
    return {
      buffer: null,
      connect: () => {},
      start: () => void (started.starts += 1),
    } as unknown as AudioBufferSourceNode;
  }

  decodeAudioData(): Promise<AudioBuffer> {
    started.decoded += 1;
    return decodeFails
      ? Promise.reject(new Error("EncodingError"))
      : Promise.resolve({} as AudioBuffer);
  }
}

/** A fresh module per test, so the cached context never leaks between them. */
async function loadAudio() {
  vi.resetModules();
  return await import("./audio");
}

beforeEach(() => {
  started = { starts: 0, decoded: 0, resumed: 0 };
  decodeFails = false;
  vi.stubGlobal("AudioContext", FakeAudioContext);
});

afterEach(() => vi.unstubAllGlobals());

describe("getAudioContext", () => {
  it("builds one context and hands out the same one for ever", async () => {
    const { getAudioContext } = await loadAudio();
    const first = getAudioContext();
    expect(first).toBeInstanceOf(FakeAudioContext);
    expect(getAudioContext()).toBe(first);
  });

  it("answers null where the browser has no Web Audio at all", async () => {
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("webkitAudioContext", undefined);
    const { getAudioContext } = await loadAudio();
    expect(getAudioContext()).toBeNull();
  });

  it("accepts the webkit-prefixed constructor", async () => {
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("webkitAudioContext", FakeAudioContext);
    const { getAudioContext } = await loadAudio();
    expect(getAudioContext()).toBeInstanceOf(FakeAudioContext);
  });

  it("answers null rather than throwing when construction fails", async () => {
    vi.stubGlobal(
      "AudioContext",
      class {
        constructor() {
          throw new Error("not allowed");
        }
      },
    );
    const { getAudioContext } = await loadAudio();
    expect(getAudioContext()).toBeNull();
  });
});

describe("installAudioUnlock", () => {
  it("resumes and primes the context on the first tap, then stops listening", async () => {
    const { installAudioUnlock, getAudioContext } = await loadAudio();
    installAudioUnlock();

    document.dispatchEvent(new Event("pointerdown"));

    expect(started.resumed).toBe(1);
    expect(started.starts).toBe(1);
    expect(getAudioContext()!.state).toBe("running");

    document.dispatchEvent(new Event("pointerdown"));
    expect(started.resumed).toBe(1);
  });

  it("takes a keypress as the gesture too", async () => {
    const { installAudioUnlock } = await loadAudio();
    installAudioUnlock();

    document.dispatchEvent(new Event("keydown"));

    expect(started.resumed).toBe(1);
  });

  it("can be uninstalled before any gesture arrives", async () => {
    const { installAudioUnlock } = await loadAudio();
    const uninstall = installAudioUnlock();
    uninstall();

    document.dispatchEvent(new Event("pointerdown"));

    expect(started.resumed).toBe(0);
  });

  it("installs once however many times it is called", async () => {
    const { installAudioUnlock } = await loadAudio();
    installAudioUnlock();
    installAudioUnlock();

    document.dispatchEvent(new Event("pointerdown"));

    expect(started.starts).toBe(1);
  });
});

describe("playWavBase64", () => {
  it("decodes the reply and plays it through the shared context", async () => {
    const { playWavBase64 } = await loadAudio();
    playWavBase64(btoa("RIFF....WAVE"));
    await Promise.resolve();
    await Promise.resolve();

    expect(started.decoded).toBe(1);
    expect(started.starts).toBe(1);
  });

  it("resumes a context iOS suspended while the app was in the background", async () => {
    const { playWavBase64 } = await loadAudio();
    playWavBase64(btoa("RIFF"));
    expect(started.resumed).toBe(1);
  });

  it("stays silent rather than throwing when there is no Web Audio", async () => {
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("webkitAudioContext", undefined);
    const { playWavBase64 } = await loadAudio();
    expect(() => playWavBase64(btoa("RIFF"))).not.toThrow();
  });

  it("swallows a decode failure", async () => {
    decodeFails = true;
    const { playWavBase64 } = await loadAudio();
    playWavBase64(btoa("RIFF"));
    await Promise.resolve();
    await Promise.resolve();
    expect(started.starts).toBe(0);
  });

  it("swallows a payload that is not base64 at all", async () => {
    const { playWavBase64 } = await loadAudio();
    expect(() => playWavBase64("not base64 !!!")).not.toThrow();
    expect(started.decoded).toBe(0);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/lib/audio.test.ts`
Expected: FAIL — `The requested module './audio' does not provide an export named 'getAudioContext'`. The carried-over module has only `playWavBase64`, built on `new Audio()`.

- [ ] **Step 3: Rewrite `web/src/lib/audio.ts`**

Complete file:

```ts
/**
 * One AudioContext for the whole app, unlocked by the first gesture.
 *
 * Constraint §4.7: iOS keeps a context suspended and refuses `Audio.play()`
 * until the user has touched the page, and it will not retroactively allow a
 * sound that was requested before then. The outgoing client made a fresh
 * `new Audio()` per reply, so the first spoken reply after every launch was
 * dropped in silence. Everything now plays through a context that a real tap
 * has already resumed.
 */

type AudioContextCtor = new () => AudioContext;

interface AudioWindow {
  AudioContext?: AudioContextCtor;
  webkitAudioContext?: AudioContextCtor;
}

let context: AudioContext | null = null;
let installed = false;

function constructor(): AudioContextCtor | null {
  const w = window as unknown as AudioWindow;
  return w.AudioContext ?? w.webkitAudioContext ?? null;
}

/** The shared context, built on first use. Null where Web Audio does not exist. */
export function getAudioContext(): AudioContext | null {
  if (context) return context;
  const Ctor = constructor();
  if (!Ctor) return null;
  try {
    context = new Ctor();
  } catch {
    // Some embedded browsers refuse construction outright. Silence beats a crash.
    context = null;
  }
  return context;
}

/**
 * Resume and prime the shared context on the first `pointerdown` or `keydown`.
 * Returns an uninstaller; `main.tsx` calls this once and never uninstalls.
 *
 * The zero-length buffer is not ceremony: on iOS, resuming is not enough — a
 * source has to actually start from inside the gesture for the context to count
 * as unlocked.
 */
export function installAudioUnlock(): () => void {
  if (installed) return () => {};
  installed = true;

  const remove = () => {
    document.removeEventListener("pointerdown", unlock);
    document.removeEventListener("keydown", unlock);
    installed = false;
  };

  function unlock(): void {
    const ctx = getAudioContext();
    if (ctx) {
      if (ctx.state === "suspended") void ctx.resume().catch(() => {});
      try {
        const source = ctx.createBufferSource();
        source.buffer = ctx.createBuffer(1, 1, 22050);
        source.connect(ctx.destination);
        source.start(0);
      } catch {
        // Nothing to prime — the context will still work for later playback.
      }
    }
    remove();
  }

  document.addEventListener("pointerdown", unlock);
  document.addEventListener("keydown", unlock);
  return remove;
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** Play a base64 WAV — Alfred's spoken reply, or an urgent notification. */
export function playWavBase64(base64: string): void {
  const ctx = getAudioContext();
  if (!ctx) return;

  let bytes: Uint8Array;
  try {
    bytes = base64ToBytes(base64);
  } catch {
    // A truncated or non-base64 payload. Nothing to play, nothing to report.
    return;
  }

  // iOS suspends the context whenever the app is backgrounded; a reply arriving
  // on the way back would otherwise decode fine and play into nothing.
  if (ctx.state === "suspended") void ctx.resume().catch(() => {});

  void ctx
    .decodeAudioData(bytes.buffer as ArrayBuffer)
    .then((buffer) => {
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      source.start(0);
    })
    .catch(() => {
      // Undecodable audio is not worth interrupting the conversation over — the
      // text of the reply is already on screen.
    });
}
```

- [ ] **Step 4: Run the audio test**

Run: `npm test -- src/lib/audio.test.ts`
Expected: `Test Files  1 passed (1)`, 13 tests.

- [ ] **Step 5: Write the failing recorder test**

Create `web/src/lib/recorder.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { blobToDataUrl, pickMimeType, Recorder } from "./recorder";

class FakeMediaRecorder {
  static supported: string[] = ["audio/mp4"];
  static instances: FakeMediaRecorder[] = [];

  static isTypeSupported(type: string): boolean {
    return FakeMediaRecorder.supported.includes(type);
  }

  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  options: { mimeType?: string } | undefined;
  started = false;

  constructor(_stream: MediaStream, options?: { mimeType?: string }) {
    this.options = options;
    FakeMediaRecorder.instances.push(this);
  }

  start(): void {
    this.started = true;
  }

  stop(): void {
    this.ondataavailable?.({ data: new Blob(["audio-bytes"], { type: "audio/mp4" }) });
    this.onstop?.();
  }
}

const analyser = { fftSize: 0, smoothingTimeConstant: 0, frequencyBinCount: 128 };
const source = { connect: vi.fn(), disconnect: vi.fn() };

class FakeAudioContext {
  state = "running";
  destination = {};
  createAnalyser() {
    return analyser;
  }
  createMediaStreamSource() {
    return source;
  }
  resume() {
    return Promise.resolve();
  }
}

let tracks: { stop: ReturnType<typeof vi.fn> }[] = [];
let getUserMedia: ReturnType<typeof vi.fn>;

beforeEach(() => {
  FakeMediaRecorder.supported = ["audio/mp4"];
  FakeMediaRecorder.instances = [];
  analyser.fftSize = 0;
  analyser.smoothingTimeConstant = 0;
  source.connect.mockClear();
  source.disconnect.mockClear();
  tracks = [{ stop: vi.fn() }, { stop: vi.fn() }];

  getUserMedia = vi.fn(async () => ({ getTracks: () => tracks }) as unknown as MediaStream);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  vi.stubGlobal("AudioContext", FakeAudioContext);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("pickMimeType", () => {
  it("prefers mp4, which is what Safari has", () => {
    expect(pickMimeType((type) => ["audio/mp4", "audio/aac"].includes(type))).toBe("audio/mp4");
  });

  it("falls back to aac", () => {
    expect(pickMimeType((type) => type === "audio/aac")).toBe("audio/aac");
  });

  it("lets the browser choose when it supports neither", () => {
    expect(pickMimeType(() => false)).toBe("");
  });

  it("never asks for webm, which Safari cannot record", () => {
    const asked: string[] = [];
    pickMimeType((type) => {
      asked.push(type);
      return false;
    });
    expect(asked).toEqual(["audio/mp4", "audio/aac"]);
  });

  it("uses MediaRecorder.isTypeSupported by default", () => {
    expect(pickMimeType()).toBe("audio/mp4");
  });
});

describe("Recorder", () => {
  it("opens the microphone and wires an analyser at the design's settings", async () => {
    const recorder = new Recorder();
    await recorder.start();

    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(FakeMediaRecorder.instances[0].options).toEqual({ mimeType: "audio/mp4" });
    expect(FakeMediaRecorder.instances[0].started).toBe(true);
    expect(recorder.analyser).toBe(analyser);
    expect(analyser.fftSize).toBe(256);
    expect(analyser.smoothingTimeConstant).toBe(0.6);
    expect(source.connect).toHaveBeenCalledWith(analyser);
  });

  it("constructs with no options when nothing is supported", async () => {
    FakeMediaRecorder.supported = [];
    const recorder = new Recorder();
    await recorder.start();
    expect(FakeMediaRecorder.instances[0].options).toBeUndefined();
  });

  it("ignores a second start while already recording", async () => {
    const recorder = new Recorder();
    await recorder.start();
    await recorder.start();
    expect(FakeMediaRecorder.instances).toHaveLength(1);
  });

  it("returns the blob, the type and how long it ran", async () => {
    vi.useFakeTimers();
    const recorder = new Recorder();
    await recorder.start();

    vi.advanceTimersByTime(2400);
    const recording = await recorder.stop();

    expect(recording).not.toBeNull();
    expect(recording!.mimeType).toBe("audio/mp4");
    expect(recording!.durationMs).toBe(2400);
    expect(recording!.blob.size).toBeGreaterThan(0);
  });

  it("releases the microphone and the analyser", async () => {
    const recorder = new Recorder();
    await recorder.start();

    await recorder.stop();

    expect(tracks[0].stop).toHaveBeenCalled();
    expect(tracks[1].stop).toHaveBeenCalled();
    expect(source.disconnect).toHaveBeenCalled();
    expect(recorder.analyser).toBeNull();
  });

  it("answers null when it was never started", async () => {
    expect(await new Recorder().stop()).toBeNull();
  });

  it("still releases the microphone when the recorder refuses to stop", async () => {
    const recorder = new Recorder();
    await recorder.start();
    FakeMediaRecorder.instances[0].stop = () => {
      throw new Error("InvalidStateError");
    };

    const recording = await recorder.stop();

    expect(recording).not.toBeNull();
    expect(tracks[0].stop).toHaveBeenCalled();
  });

  it("records without an analyser where there is no Web Audio", async () => {
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("webkitAudioContext", undefined);
    // getAudioContext() memoises the context the tests above built, so a fresh
    // copy of the module is the only recorder that has never seen one.
    vi.resetModules();
    const { Recorder: FreshRecorder } = await import("./recorder");
    const recorder = new FreshRecorder();
    await recorder.start();
    expect(FakeMediaRecorder.instances[0].started).toBe(true);
    expect(recorder.analyser).toBeNull();
  });
});

describe("blobToDataUrl", () => {
  it("reads a blob as a data URL the socket can carry", async () => {
    const url = await blobToDataUrl(new Blob(["hello"], { type: "audio/mp4" }));
    expect(url.startsWith("data:audio/mp4;base64,")).toBe(true);
  });
});
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npm test -- src/lib/recorder.test.ts`
Expected: FAIL — `Failed to resolve import "./recorder"`.

- [ ] **Step 7: Write `web/src/lib/recorder.ts`**

Complete file:

```ts
import { getAudioContext } from "./audio";

/**
 * In preference order. Constraint §4.8: Safari's MediaRecorder has no WebM at
 * all and *throws* from the constructor when asked for it, which is how the
 * outgoing VoiceButton died on every iPhone. The backend accepts aac/m4a/wav.
 */
export const RECORDER_MIME_TYPES = ["audio/mp4", "audio/aac"] as const;

function supportedByBrowser(type: string): boolean {
  return (
    typeof MediaRecorder !== "undefined" &&
    typeof MediaRecorder.isTypeSupported === "function" &&
    MediaRecorder.isTypeSupported(type)
  );
}

/** The first type this browser can record, or `""` to let it choose for itself. */
export function pickMimeType(isSupported: (type: string) => boolean = supportedByBrowser): string {
  for (const type of RECORDER_MIME_TYPES) {
    if (isSupported(type)) return type;
  }
  return "";
}

export interface Recording {
  blob: Blob;
  mimeType: string;
  durationMs: number;
}

/**
 * One hold-to-talk recording: the microphone stream, the MediaRecorder, and the
 * AnalyserNode the presence field is driven from — all opened together and, more
 * importantly, all released together. A leaked stream leaves the iOS microphone
 * indicator on after the button is let go.
 */
export class Recorder {
  analyser: AnalyserNode | null = null;

  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private chunks: Blob[] = [];
  private mimeType = "";
  private startedAt = 0;

  async start(): Promise<void> {
    if (this.recorder) return;

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.stream = stream;
    this.chunks = [];
    this.mimeType = pickMimeType();

    const recorder = this.mimeType
      ? new MediaRecorder(stream, { mimeType: this.mimeType })
      : new MediaRecorder(stream);
    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data && event.data.size > 0) this.chunks.push(event.data);
    };
    recorder.start();
    this.recorder = recorder;
    this.startedAt = Date.now();

    // The analyser is optional: no Web Audio means no live presence field, but
    // the recording itself must still work.
    const ctx = getAudioContext();
    if (!ctx) return;
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.6;
    const source = ctx.createMediaStreamSource(stream);
    source.connect(analyser);
    this.analyser = analyser;
    this.source = source;
  }

  async stop(): Promise<Recording | null> {
    const recorder = this.recorder;
    const stream = this.stream;
    const mimeType = this.mimeType;
    const durationMs = this.startedAt === 0 ? 0 : Date.now() - this.startedAt;

    this.recorder = null;
    this.stream = null;
    this.startedAt = 0;

    const release = () => {
      this.source?.disconnect();
      this.source = null;
      this.analyser = null;
      for (const track of stream?.getTracks() ?? []) track.stop();
    };

    if (!recorder) {
      release();
      return null;
    }

    const blob = await new Promise<Blob>((resolve) => {
      const finish = () => resolve(new Blob(this.chunks, { type: mimeType || "audio/mp4" }));
      recorder.onstop = finish;
      try {
        recorder.stop();
      } catch {
        // Already inactive. Whatever was collected is still the recording.
        finish();
      }
    });

    release();
    return { blob, mimeType, durationMs };
  }
}

/** Blob → `data:audio/mp4;base64,…`, which is what `/ws` expects as `content`. */
export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the recording"));
    reader.readAsDataURL(blob);
  });
}
```

- [ ] **Step 8: Run the recorder test and the suite**

Run: `npm test -- src/lib/recorder.test.ts`
Expected: `Test Files  1 passed (1)`, 14 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  31 passed (31)`, no eslint output, `✓ built in …`.

```bash
grep -rn "audio/webm" src/
```

Expected: no output — the codec that killed voice on iOS is gone from the client.

- [ ] **Step 9: Teach the server the `mp4` format hint (failing test first)**

iOS Safari's `MediaRecorder` produces `audio/mp4`. `_decode_audio` in
`core/channels/web_server.py` only passes a MIME subtype through as the ffmpeg
extension hint when it is in `_ALLOWED_AUDIO_FORMATS = {"wav", "webm", "aac", "m4a", "ogg", "mp3"}`
— anything else is written to a `.wav` temp file and left to ffmpeg's content probe.
The probe usually wins for an `ftyp`-led MP4, but "usually" is not a contract. One
line on the server makes the hint honest. Append to `tests/core/channels/test_audio_format.py`:

```python


def test_decode_audio_mp4_returns_format() -> None:
    """iOS Safari records audio/mp4; the hint must survive to the temp-file suffix."""
    raw = b"fake-mp4-data"
    data_url = f"data:audio/mp4;base64,{base64.b64encode(raw).decode()}"
    audio_bytes, fmt = _decode_audio(data_url)
    assert audio_bytes == raw
    assert fmt == "mp4"
```

Run: `uv run pytest tests/core/channels/test_audio_format.py -q`
Expected: `1 failed, 8 passed` — `assert 'wav' == 'mp4'`.

Edit `core/channels/web_server.py` line 98:

```python
_ALLOWED_AUDIO_FORMATS = {"wav", "webm", "aac", "m4a", "mp4", "ogg", "mp3"}
```

Run: `uv run pytest tests/core/channels/test_audio_format.py -q`
Expected: `9 passed`.

- [ ] **Step 10: Commit**

```bash
git add web/src/lib/audio.ts web/src/lib/audio.test.ts web/src/lib/recorder.ts web/src/lib/recorder.test.ts core/channels/web_server.py tests/core/channels/test_audio_format.py
git commit -m "feat(web): one unlocked AudioContext and a Safari-safe recorder"
```

---

### Task 22: Hold to talk

Press and hold; the button fills, four bars wave, the presence field starts moving to your voice, and a caption counts the seconds. Release and it is sent. Release inside a second and nothing happened at all — the handoff's rule, and the right one: a mis-tap on the microphone should not put a fragment of a sentence into the conversation.

Pointer capture is what makes it feel physical. Without it, a finger that drifts off the 56 px button while speaking never delivers `pointerup` to the button, and the recording runs until the tab is closed.

jsdom implements neither `PointerEvent` nor pointer capture, so the test environment gains both here — Task 26's slider needs the same two.

**Files:**
- Create: `web/src/room/HoldToTalk.tsx`, `web/src/room/HoldToTalk.test.tsx`
- Modify: `web/src/test/setup.ts`

- [ ] **Step 1: Teach jsdom about pointers**

Append to `web/src/test/setup.ts`:

```ts
// jsdom implements neither PointerEvent nor pointer capture, and both are
// load-bearing: hold-to-talk and slide-to-confirm are pointer-driven, and both
// capture the pointer so a finger that drifts off the control still reports up.
if (typeof globalThis.PointerEvent !== "function") {
  class TestPointerEvent extends MouseEvent {
    pointerId: number;
    pointerType: string;
    isPrimary: boolean;

    constructor(type: string, params: PointerEventInit = {}) {
      super(type, params);
      this.pointerId = params.pointerId ?? 1;
      this.pointerType = params.pointerType ?? "touch";
      this.isPrimary = params.isPrimary ?? true;
    }
  }
  Object.defineProperty(globalThis, "PointerEvent", {
    configurable: true,
    writable: true,
    value: TestPointerEvent,
  });
}

if (typeof Element.prototype.setPointerCapture !== "function") {
  Element.prototype.setPointerCapture = function setPointerCapture() {};
  Element.prototype.releasePointerCapture = function releasePointerCapture() {};
  Element.prototype.hasPointerCapture = function hasPointerCapture() {
    return false;
  };
}
```

Append to `web/src/test/setup.test.ts`, inside the existing `describe("test environment", …)` block:

```ts
  it("constructs pointer events with coordinates", () => {
    const event = new PointerEvent("pointerdown", { pointerId: 7, clientX: 42 });
    expect(event.pointerId).toBe(7);
    expect(event.clientX).toBe(42);
  });

  it("lets an element capture the pointer without throwing", () => {
    const el = document.createElement("div");
    expect(() => el.setPointerCapture(1)).not.toThrow();
    expect(() => el.releasePointerCapture(1)).not.toThrow();
  });
```

Run: `npm test -- src/test/setup.test.ts`
Expected: `Test Files  1 passed (1)`, 7 tests.

- [ ] **Step 2: Write the failing hold test**

Create `web/src/room/HoldToTalk.test.tsx`:

```tsx
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PresenceSignal } from "@/lib/presence-signal";
import { HoldToTalk } from "./HoldToTalk";

const { recorders, state } = vi.hoisted(() => ({
  recorders: [] as { start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }[],
  state: { durationMs: 2400, startRejects: false },
}));

const FAKE_ANALYSER = { fftSize: 256 } as unknown as AnalyserNode;

vi.mock("@/lib/recorder", () => {
  class Recorder {
    analyser: AnalyserNode | null = FAKE_ANALYSER;
    start = vi.fn(async () => {
      if (state.startRejects) throw new Error("NotAllowedError");
    });
    stop = vi.fn(async () => ({
      blob: new Blob(["audio"], { type: "audio/mp4" }),
      mimeType: "audio/mp4",
      durationMs: state.durationMs,
    }));
    constructor() {
      recorders.push(this);
    }
  }
  return {
    Recorder,
    blobToDataUrl: async () => "data:audio/mp4;base64,AAAA",
    pickMimeType: () => "audio/mp4",
  };
});

function renderHold(online = true) {
  const signal = new PresenceSignal();
  const setHolding = vi.spyOn(signal, "setHolding");
  const attach = vi.spyOn(signal, "attach");
  const onAudio = vi.fn();
  const onHoldingChange = vi.fn();
  render(
    <div style={{ position: "relative" }}>
      <HoldToTalk
        signal={signal}
        online={online}
        onAudio={onAudio}
        onHoldingChange={onHoldingChange}
      />
    </div>,
  );
  return {
    button: screen.getByRole("button", { name: "Hold to talk" }),
    setHolding,
    attach,
    onAudio,
    onHoldingChange,
  };
}

async function press(button: HTMLElement): Promise<void> {
  await act(async () => {
    fireEvent.pointerDown(button, { pointerId: 1 });
  });
}

async function release(button: HTMLElement): Promise<void> {
  await act(async () => {
    fireEvent.pointerUp(button, { pointerId: 1 });
  });
}

beforeEach(() => {
  recorders.length = 0;
  state.durationMs = 2400;
  state.startRejects = false;
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("HoldToTalk", () => {
  it("is a 56 px button, and refuses to record while offline", () => {
    const { button } = renderHold(false);
    expect(button).toHaveClass("h-14", "w-14");
    expect(button).toBeDisabled();
  });

  it("starts recording, drives the presence field and shows the caption", async () => {
    const { button, setHolding, attach, onHoldingChange } = renderHold();

    await press(button);

    expect(recorders).toHaveLength(1);
    expect(recorders[0].start).toHaveBeenCalled();
    expect(setHolding).toHaveBeenCalledWith(true);
    expect(attach).toHaveBeenCalledWith(FAKE_ANALYSER);
    expect(onHoldingChange).toHaveBeenCalledWith(true);
    expect(screen.getByText("recording 0:00 · release to send")).toBeInTheDocument();
  });

  it("counts the seconds while it is held", async () => {
    vi.useFakeTimers();
    const { button } = renderHold();
    await press(button);

    act(() => void vi.advanceTimersByTime(4000));

    expect(screen.getByText("recording 0:04 · release to send")).toBeInTheDocument();
  });

  it("sends the recording and its length on release", async () => {
    const { button, onAudio, setHolding, attach, onHoldingChange } = renderHold();
    await press(button);

    await release(button);

    await waitFor(() =>
      expect(onAudio).toHaveBeenCalledWith("data:audio/mp4;base64,AAAA", 2.4),
    );
    expect(recorders[0].stop).toHaveBeenCalled();
    expect(setHolding).toHaveBeenLastCalledWith(false);
    expect(attach).toHaveBeenLastCalledWith(null);
    expect(onHoldingChange).toHaveBeenLastCalledWith(false);
    expect(screen.queryByText(/release to send/)).toBeNull();
  });

  it("throws away anything under a second", async () => {
    state.durationMs = 640;
    const { button, onAudio } = renderHold();

    await press(button);
    await release(button);

    expect(onAudio).not.toHaveBeenCalled();
    // The microphone is still released — a discarded take must not leak the stream.
    expect(recorders[0].stop).toHaveBeenCalled();
  });

  it("discards on pointercancel", async () => {
    const { button, onAudio, setHolding } = renderHold();
    await press(button);

    await act(async () => {
      fireEvent.pointerCancel(button, { pointerId: 1 });
    });

    expect(onAudio).not.toHaveBeenCalled();
    expect(setHolding).toHaveBeenLastCalledWith(false);
  });

  it("captures the pointer so a finger sliding off still ends the take", async () => {
    const capture = vi.spyOn(Element.prototype, "setPointerCapture");
    const { button } = renderHold();

    await press(button);

    expect(capture).toHaveBeenCalledWith(1);
  });

  it("ignores a second press while already recording", async () => {
    const { button } = renderHold();
    await press(button);
    await press(button);
    expect(recorders).toHaveLength(1);
  });

  it("leaves the room as it was when the microphone is refused", async () => {
    state.startRejects = true;
    const { button, onAudio, setHolding, onHoldingChange } = renderHold();

    await press(button);

    expect(onAudio).not.toHaveBeenCalled();
    expect(setHolding).toHaveBeenLastCalledWith(false);
    expect(onHoldingChange).toHaveBeenLastCalledWith(false);
    expect(screen.queryByText(/release to send/)).toBeNull();
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npm test -- src/room/HoldToTalk.test.tsx`
Expected: FAIL — `Failed to resolve import "./HoldToTalk"`.

- [ ] **Step 4: Write `web/src/room/HoldToTalk.tsx`**

Complete file:

```tsx
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { mmss } from "@/lib/format";
import type { PresenceSignal } from "@/lib/presence-signal";
import { blobToDataUrl, Recorder } from "@/lib/recorder";

/** Under this, it was a mis-tap and not a sentence. Handoff: "<1 s releases do nothing". */
const MIN_HOLD_MS = 1000;
/** 3 px bars, staggered exactly as the handoff's `wave` keyframe describes. */
const BAR_DELAYS = [0, 0.15, 0.3, 0.1];

export interface HoldToTalkProps {
  signal: PresenceSignal;
  online: boolean;
  /** Lifts `holding` to the Room, which is where the headline reads it. */
  onHoldingChange: (holding: boolean) => void;
  onAudio: (dataUrl: string, seconds: number) => void;
}

export function HoldToTalk({ signal, online, onHoldingChange, onAudio }: HoldToTalkProps) {
  const [holding, setHolding] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const recorderRef = useRef<Recorder | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopTicking(): void {
    if (!tickRef.current) return;
    clearInterval(tickRef.current);
    tickRef.current = null;
  }

  async function finish(send: boolean): Promise<void> {
    const recorder = recorderRef.current;
    recorderRef.current = null;

    stopTicking();
    setHolding(false);
    setSeconds(0);
    onHoldingChange(false);
    signal.setHolding(false);
    signal.attach(null);

    if (!recorder) return;

    // Always stop, whether or not we are sending: the microphone indicator stays
    // on until the tracks are released.
    const recording = await recorder.stop();
    if (!send || !recording) return;
    if (recording.durationMs < MIN_HOLD_MS) return;

    const dataUrl = await blobToDataUrl(recording.blob);
    onAudio(dataUrl, recording.durationMs / 1000);
  }

  async function begin(event: ReactPointerEvent<HTMLButtonElement>): Promise<void> {
    if (!online || recorderRef.current) return;
    // Capture first: a finger that slides off a 56 px button mid-sentence would
    // otherwise never deliver pointerup here, and the take would run for ever.
    event.currentTarget.setPointerCapture(event.pointerId);

    const recorder = new Recorder();
    recorderRef.current = recorder;
    setHolding(true);
    setSeconds(0);
    onHoldingChange(true);
    signal.setHolding(true);
    tickRef.current = setInterval(() => setSeconds((current) => current + 1), 1000);

    try {
      await recorder.start();
    } catch {
      // Permission refused, or no microphone. Unwind quietly — the composer is
      // still there and the user can type.
      await finish(false);
      return;
    }
    signal.attach(recorder.analyser);
  }

  useEffect(() => {
    return () => {
      stopTicking();
      void recorderRef.current?.stop();
      recorderRef.current = null;
    };
  }, []);

  return (
    <>
      <button
        type="button"
        aria-label="Hold to talk"
        disabled={!online}
        onPointerDown={(event) => void begin(event)}
        onPointerUp={() => void finish(true)}
        onPointerCancel={() => void finish(false)}
        className="flex h-14 w-14 shrink-0 items-center justify-center rounded-[28px] border-[1.5px] transition-transform duration-150"
        style={{
          borderColor: "var(--accent)",
          background: holding ? "var(--accent)" : "transparent",
          transform: holding ? "scale(1.12)" : "scale(1)",
          touchAction: "none",
          userSelect: "none",
          WebkitUserSelect: "none",
        }}
      >
        {holding ? (
          <span aria-hidden="true" className="flex h-[22px] items-center gap-[3px]">
            {BAR_DELAYS.map((delay) => (
              <span
                key={delay}
                className="h-[22px] w-[3px] rounded-[2px]"
                style={{
                  background: "var(--ink)",
                  animation: `wave .7s ${delay}s ease-in-out infinite`,
                }}
              />
            ))}
          </span>
        ) : (
          <span
            aria-hidden="true"
            className="block h-[22px] w-3.5 rounded-[7px]"
            style={{ background: online ? "var(--accent)" : "var(--muted)" }}
          />
        )}
      </button>

      {holding ? (
        <div className="t-meta pointer-events-none absolute inset-x-0 bottom-[84px] z-[1] text-center">
          recording {mmss(seconds)} · release to send
        </div>
      ) : null}
    </>
  );
}
```

- [ ] **Step 5: Run the hold test and the suite**

Run: `npm test -- src/room/HoldToTalk.test.tsx`
Expected: `Test Files  1 passed (1)`, 9 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  32 passed (32)`, no eslint output, `✓ built in …`.

- [ ] **Step 6: Commit**

```bash
git add web/src/room/HoldToTalk.tsx web/src/room/HoldToTalk.test.tsx web/src/test/setup.ts web/src/test/setup.test.ts
git commit -m "feat(web): hold-to-talk, with pointer capture and a one-second floor"
```

---

### Task 23: The Held-back sheet

The one place a do-not-disturb queue becomes visible. Spec §5.2.6: with no expiry it never drains on its own, and a queue nobody can see is the failure. Spec §5.2.1 governs the button: `POST /api/admin/notifications/drain` writes an internal action to `alfred:actions` and returns `{"status": "queued"}` — the API cannot know whether anything was delivered, so the button says `Queued` and the note says exactly what that does and does not mean.

All copy here is the handoff's, verbatim.

**Files:**
- Create: `web/src/sheets/HeldBackSheet.tsx`, `web/src/sheets/HeldBackSheet.test.tsx`
- Modify: `web/src/test/fixtures.ts`

- [ ] **Step 1: Add the deferred fixture**

Append to `web/src/test/fixtures.ts` (extend the type import with `NotificationEvent`):

```ts
/** `GET /api/admin/notifications/deferred` — full Notification JSON, oldest first. */
export const deferredFixture: { notifications: NotificationEvent[] } = {
  notifications: [
    {
      notification_id: "ntf-d1",
      title: "Bins go out tonight",
      body: "The council moved collection to Friday.",
      urgency: "important",
      source: "trigger:trg_bins",
      timestamp: "2026-09-07T07:02:00",
      metadata: {},
    },
    {
      notification_id: "ntf-d2",
      title: "Bathroom humidity stayed high",
      body: "Above 70% for an hour.",
      urgency: "informational",
      source: "trigger:trg_bath",
      timestamp: "2026-09-07T07:19:00",
      metadata: {},
    },
  ],
};
```

- [ ] **Step 2: Write the failing test**

Create `web/src/sheets/HeldBackSheet.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { deferredFixture } from "@/test/fixtures";
import { HeldBackSheet } from "./HeldBackSheet";

interface Call {
  url: string;
  method: string;
}

let calls: Call[] = [];
let drainStatus = 200;

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET" });
      if (url === "/api/admin/notifications/deferred")
        return new Response(JSON.stringify(deferredFixture), { status: 200 });
      if (url === "/api/admin/notifications/drain")
        return new Response(
          drainStatus === 200 ? '{"status":"queued"}' : '{"detail":"redis is down"}',
          { status: drainStatus },
        );
      return new Response("{}", { status: 404 });
    }),
  );
}

function renderSheet(open = true) {
  const onClose = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <HeldBackSheet open={open} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

beforeEach(() => {
  calls = [];
  drainStatus = 200;
  stubFetch();
});

afterEach(() => vi.unstubAllGlobals());

describe("HeldBackSheet", () => {
  it("explains why the queue exists and what never drains it", async () => {
    renderSheet();
    expect(
      await screen.findByText(
        "Non-urgent notifications wait here while do-not-disturb is on. Urgent ones still speak. With no expiry set this queue never drains on its own.",
      ),
    ).toBeInTheDocument();
  });

  it("lists what is waiting, with its urgency and its hour", async () => {
    renderSheet();

    expect(await screen.findByText("Bins go out tonight")).toBeInTheDocument();
    expect(screen.getByText("important · 07:02 · deferred by DND")).toBeInTheDocument();
    expect(screen.getByText("Bathroom humidity stayed high")).toBeInTheDocument();
    expect(screen.getByText("informational · 07:19 · deferred by DND")).toBeInTheDocument();
  });

  it("says queued, not delivered", async () => {
    const user = userEvent.setup();
    renderSheet();
    const drain = await screen.findByRole("button", { name: "Drain queue now" });

    expect(
      screen.getByText(
        "Queued only; the server does not report delivery. Items stay listed until a fresh read confirms.",
      ),
    ).toBeInTheDocument();

    await user.click(drain);

    expect(await screen.findByRole("button", { name: "Queued" })).toBeInTheDocument();
    expect(
      screen.getByText(
        /^Accepted at \d{2}:\d{2}\. Queue will empty on the next refresh if delivery succeeded\.$/,
      ),
    ).toBeInTheDocument();
    expect(calls.some((call) => call.method === "POST" && call.url.endsWith("/drain"))).toBe(true);
  });

  it("keeps the rows on screen after draining, because nothing is confirmed", async () => {
    const user = userEvent.setup();
    renderSheet();
    await user.click(await screen.findByRole("button", { name: "Drain queue now" }));

    await screen.findByRole("button", { name: "Queued" });
    expect(screen.getByText("Bins go out tonight")).toBeInTheDocument();
  });

  it("reports a refused drain and stays offerable", async () => {
    const user = userEvent.setup();
    drainStatus = 503;
    renderSheet();

    await user.click(await screen.findByRole("button", { name: "Drain queue now" }));

    expect(await screen.findByText("redis is down")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Drain queue now" })).toBeEnabled();
  });

  it("says so when nothing is waiting", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"notifications":[]}', { status: 200 })),
    );
    renderSheet();

    expect(await screen.findByText("Nothing is being held back.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Drain queue now" })).toBeNull();
  });

  it("closes on Done", async () => {
    const user = userEvent.setup();
    const { onClose } = renderSheet();

    await user.click(await screen.findByRole("button", { name: "Done" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("reads nothing while it is closed", () => {
    renderSheet(false);
    expect(calls).toHaveLength(0);
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npm test -- src/sheets/HeldBackSheet.test.tsx`
Expected: FAIL — `Failed to resolve import "./HeldBackSheet"`.

- [ ] **Step 4: Write `web/src/sheets/HeldBackSheet.tsx`**

Complete file:

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, post } from "@/lib/api";
import { hhmm } from "@/lib/format";
import type { NotificationEvent } from "@/lib/types";
import { Sheet } from "@/shell/Sheet";

const INTRO =
  "Non-urgent notifications wait here while do-not-disturb is on. Urgent ones still speak. With no expiry set this queue never drains on its own.";
const IDLE_NOTE =
  "Queued only; the server does not report delivery. Items stay listed until a fresh read confirms.";

export interface HeldBackSheetProps {
  open: boolean;
  onClose: () => void;
}

export function HeldBackSheet({ open, onClose }: HeldBackSheetProps) {
  const [queuedAt, setQueuedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const deferred = useQuery<{ notifications: NotificationEvent[] }>({
    queryKey: ["deferred"],
    queryFn: () => api<{ notifications: NotificationEvent[] }>("/api/admin/notifications/deferred"),
    // Nothing is read until the sheet is actually opened.
    enabled: open,
  });

  const notifications = deferred.data?.notifications ?? [];

  async function drain(): Promise<void> {
    setError(null);
    try {
      await post("/api/admin/notifications/drain");
      setQueuedAt(hhmm(new Date()));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : (caught as Error).message);
    }
  }

  return (
    <Sheet open={open} title="Held back" onClose={onClose}>
      <p className="text-[13.5px] leading-[1.5]" style={{ color: "var(--fg2)" }}>
        {INTRO}
      </p>

      {notifications.map((notification) => (
        <div
          key={notification.notification_id}
          className="flex gap-3 py-3"
          style={{ borderTop: "1px solid var(--line)" }}
        >
          <span
            aria-hidden="true"
            className="mt-1.5 h-2 w-2 shrink-0 rounded-[2px]"
            style={{ background: "oklch(0.62 0.11 255)" }}
          />
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <div className="t-row">{notification.title}</div>
            <div className="t-meta">
              {notification.urgency} · {hhmm(notification.timestamp)} · deferred by DND
            </div>
          </div>
        </div>
      ))}

      {deferred.isSuccess && notifications.length === 0 ? (
        <div className="t-row" style={{ color: "var(--muted)" }}>
          Nothing is being held back.
        </div>
      ) : null}

      {notifications.length > 0 ? (
        <>
          <button
            type="button"
            onClick={() => void drain()}
            disabled={queuedAt !== null}
            className="mt-1.5 h-[50px] rounded-[25px] border bg-transparent text-[15px] font-medium disabled:opacity-60"
            style={{ borderColor: "var(--line)", color: "var(--fg)" }}
          >
            {queuedAt ? "Queued" : "Drain queue now"}
          </button>
          <div className="t-meta text-center">
            {error ??
              (queuedAt
                ? `Accepted at ${queuedAt}. Queue will empty on the next refresh if delivery succeeded.`
                : IDLE_NOTE)}
          </div>
        </>
      ) : null}
    </Sheet>
  );
}
```

- [ ] **Step 5: Run the test and the suite**

Run: `npm test -- src/sheets/HeldBackSheet.test.tsx`
Expected: `Test Files  1 passed (1)`, 8 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  33 passed (33)`, no eslint output, `✓ built in …`.

- [ ] **Step 6: Commit**

```bash
git add web/src/sheets web/src/test/fixtures.ts
git commit -m "feat(web): the held-back sheet, and a drain that says queued"
```

---

### Task 24: The actions store, and the provider that feeds it

A pending approval is the highest-stakes object in the app and it arrives from three unrelated directions:

| Source | When it matters |
|---|---|
| `GET /api/actions/pending` | Cold launch, and every return from the background |
| A `/ws` `notification` carrying `metadata.pending_action_id` | The app was open when the conscious engine asked |
| The telemetry stream `home_action_results` | The lock actually reported back |

They can arrive in any order, twice, or not at all, so the state is a reducer over an explicit event type rather than a pile of `setState` calls. The reducer is pure and takes its clock from the event — `tick` carries `now` — which is what makes the expiry rule testable.

Two rules the reducer exists to hold:

- **Only a `pending` action expires.** A `queued` one has been confirmed and the result may still be on its way; turning it into a tombstone because the fuse ran out would say "not done" about something that very likely was.
- **A list read never demotes what it does not mention.** On a cold launch from a notification tap, `GET /api/actions/pending` and `GET /api/actions/{id}` race, and an empty list landing second would turn a live approval into a tombstone. An action consumed elsewhere resolves the honest way instead: `tick` expires it, a 404 on confirm answers it, or a `home_action_results` frame applies it.

**Files:**
- Create: `web/src/lib/actions.ts`, `web/src/lib/actions.test.ts`
- Create: `web/src/door/DoorProvider.tsx`, `web/src/door/DoorProvider.test.tsx`
- Modify: `web/src/test/fixtures.ts`

- [ ] **Step 1: Add the action fixtures**

Append to `web/src/test/fixtures.ts` (extend the type import with `ActionResultEvent, PendingAction`):

```ts
/**
 * `GET /api/actions/pending`, as `pending_action_payload` builds it. The clock in
 * these is 2026-09-07: asked at 07:41, lapses at 07:46.
 */
export const pendingActionFixture: PendingAction = {
  request_id: "a91f3c2e",
  tool_name: "home.lock_unlock",
  target_service: "home-service",
  parameters: { entity_id: "lock.front_door", action: "unlock" },
  reason: "You asked me to let the cleaner in when she rings. She rang at 07:41.",
  source: "conscious-engine",
  timestamp: "2026-09-07T07:41:00Z",
  ttl_seconds: 300,
  expires_at: "2026-09-07T07:46:00Z",
};

/** A second one, three minutes younger, with no reason supplied. */
export const secondPendingActionFixture: PendingAction = {
  request_id: "7c2e0b1d",
  tool_name: "home.alarm_disarm",
  target_service: "home-service",
  parameters: { entity_id: "alarm_control_panel.house" },
  reason: null,
  source: "conscious-engine",
  timestamp: "2026-09-07T07:44:00Z",
  ttl_seconds: 300,
  expires_at: "2026-09-07T07:49:00Z",
};

/** One entry of the `home_action_results` stream — the only proof of "applied". */
export const actionResultFixture: ActionResultEvent = {
  request_id: "a91f3c2e",
  tool_name: "home.lock_unlock",
  status: "success",
  result: { state: "unlocked" },
  timestamp: "2026-09-07T07:42:10Z",
};
```

- [ ] **Step 2: Write the failing reducer test**

Create `web/src/lib/actions.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  actionReducer,
  confirmAction,
  fetchAction,
  fetchPending,
  fuseRemaining,
  type TrackedAction,
} from "./actions";
import {
  actionResultFixture,
  pendingActionFixture,
  secondPendingActionFixture,
} from "@/test/fixtures";

const T0741 = Date.parse("2026-09-07T07:41:00Z");
const T0742 = Date.parse("2026-09-07T07:42:00Z");
const T0747 = Date.parse("2026-09-07T07:47:00Z");

const tracked = (phase: TrackedAction["phase"] = "pending"): TrackedAction => ({
  action: pendingActionFixture,
  phase,
});

afterEach(() => vi.unstubAllGlobals());

describe("fuseRemaining", () => {
  it("counts the seconds left on the server's own clock", () => {
    expect(fuseRemaining(pendingActionFixture, T0742)).toBe(240);
    expect(fuseRemaining(pendingActionFixture, T0741)).toBe(300);
  });

  it("never counts below zero", () => {
    expect(fuseRemaining(pendingActionFixture, T0747)).toBe(0);
  });

  it("treats an unreadable expiry as lapsed rather than infinite", () => {
    expect(fuseRemaining({ ...pendingActionFixture, expires_at: "soon" }, T0742)).toBe(0);
  });
});

describe("actionReducer — loaded", () => {
  it("adopts unknown actions as pending, oldest first", () => {
    const state = actionReducer([], {
      type: "loaded",
      actions: [secondPendingActionFixture, pendingActionFixture],
    });
    expect(state.map((item) => item.action.request_id)).toEqual(["a91f3c2e", "7c2e0b1d"]);
    expect(state.every((item) => item.phase === "pending")).toBe(true);
  });

  it("keeps the phase of an id it already knows", () => {
    const state = actionReducer([tracked("queued")], {
      type: "loaded",
      actions: [pendingActionFixture],
    });
    expect(state[0].phase).toBe("queued");
  });

  it("leaves an action the list did not mention alone", () => {
    // A deep-link read and this list read race on a cold launch. Demoting here
    // would turn a live approval into a tombstone because two requests landed
    // out of order; a genuinely consumed one resolves through `tick`, a 404
    // confirm, or a result frame.
    const state = actionReducer([tracked()], { type: "loaded", actions: [] });
    expect(state).toHaveLength(1);
    expect(state[0].phase).toBe("pending");
  });

  it("leaves a tombstone alone", () => {
    const state = actionReducer([tracked("expired")], { type: "loaded", actions: [] });
    expect(state[0].phase).toBe("expired");
  });

  it("refreshes the payload of a known id", () => {
    const state = actionReducer([tracked()], {
      type: "loaded",
      actions: [{ ...pendingActionFixture, ttl_seconds: 120 }],
    });
    expect(state[0].action.ttl_seconds).toBe(120);
  });
});

describe("actionReducer — arrived", () => {
  it("adds a new action in age order", () => {
    const state = actionReducer([{ action: secondPendingActionFixture, phase: "pending" }], {
      type: "arrived",
      action: pendingActionFixture,
    });
    expect(state.map((item) => item.action.request_id)).toEqual(["a91f3c2e", "7c2e0b1d"]);
  });

  it("refreshes an action it already tracks without resetting its phase", () => {
    const state = actionReducer([tracked("queued")], {
      type: "arrived",
      action: { ...pendingActionFixture, ttl_seconds: 60 },
    });
    expect(state).toHaveLength(1);
    expect(state[0].phase).toBe("queued");
    expect(state[0].action.ttl_seconds).toBe(60);
  });
});

describe("actionReducer — answering", () => {
  it("queues on a confirm, and stamps when", () => {
    const state = actionReducer([tracked()], {
      type: "confirm-sent",
      id: "a91f3c2e",
      at: "2026-09-07T07:42:00Z",
    });
    expect(state[0].phase).toBe("queued");
    expect(state[0].confirmedAt).toBe("2026-09-07T07:42:00Z");
  });

  it("marks a 404 confirm as already answered", () => {
    const state = actionReducer([tracked()], { type: "confirm-404", id: "a91f3c2e" });
    expect(state[0].phase).toBe("answered");
  });

  it("only applies on the matching request id", () => {
    const state = actionReducer([tracked("queued")], {
      type: "result",
      result: actionResultFixture,
      at: "2026-09-07T07:42:10Z",
    });
    expect(state[0].phase).toBe("applied");
    expect(state[0].appliedAt).toBe("2026-09-07T07:42:10Z");
    expect(state[0].result).toEqual(actionResultFixture);
  });

  it("ignores a result for something it is not tracking", () => {
    const before = [tracked("queued")];
    const state = actionReducer(before, {
      type: "result",
      result: { ...actionResultFixture, request_id: "0000" },
      at: "2026-09-07T07:42:10Z",
    });
    expect(state[0].phase).toBe("queued");
  });

  it("believes a result even after the fuse lapsed — it really did happen", () => {
    const state = actionReducer([tracked("expired")], {
      type: "result",
      result: actionResultFixture,
      at: "2026-09-07T07:47:10Z",
    });
    expect(state[0].phase).toBe("applied");
  });
});

describe("actionReducer — tick", () => {
  it("expires a pending action whose fuse has run out", () => {
    const state = actionReducer([tracked()], { type: "tick", now: T0747 });
    expect(state[0].phase).toBe("expired");
  });

  it("leaves a queued action alone — its result may still be coming", () => {
    const state = actionReducer([tracked("queued")], { type: "tick", now: T0747 });
    expect(state[0].phase).toBe("queued");
  });

  it("does nothing at all while the fuse is running", () => {
    const before = [tracked()];
    expect(actionReducer(before, { type: "tick", now: T0742 })).toBe(before);
  });
});

describe("the three reads", () => {
  it("lists the pending actions", async () => {
    const mock = vi.fn<typeof fetch>(
      async () =>
        new Response(JSON.stringify({ actions: [pendingActionFixture] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", mock);

    await expect(fetchPending()).resolves.toEqual([pendingActionFixture]);
    expect(mock.mock.calls[0][0]).toBe("/api/actions/pending");
  });

  it("survives a body with no actions key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 })),
    );
    await expect(fetchPending()).resolves.toEqual([]);
  });

  it("reads one action by id", async () => {
    const mock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify(pendingActionFixture), { status: 200 }),
    );
    vi.stubGlobal("fetch", mock);

    await expect(fetchAction("a91f3c2e")).resolves.toEqual(pendingActionFixture);
    expect(mock.mock.calls[0][0]).toBe("/api/actions/a91f3c2e");
  });

  it("posts a confirmation", async () => {
    const mock = vi.fn<typeof fetch>(async () => new Response('{"status":"confirmed"}', { status: 200 }));
    vi.stubGlobal("fetch", mock);

    await confirmAction("a91f3c2e");

    expect(mock.mock.calls[0][0]).toBe("/api/actions/a91f3c2e/confirm");
    expect((mock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("throws the 404 through so the caller can tombstone it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"detail":"Pending action not found or expired"}', { status: 404 })),
    );
    await expect(confirmAction("a91f3c2e")).rejects.toMatchObject({ status: 404 });
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npm test -- src/lib/actions.test.ts`
Expected: FAIL — `Failed to resolve import "./actions"`.

- [ ] **Step 4: Write `web/src/lib/actions.ts`**

Complete file:

```ts
import { api, post } from "./api";
import type { ActionResultEvent, PendingAction } from "./types";

/**
 * Where one approval stands.
 *
 * `queued` is deliberately not `applied`: `POST /api/actions/{id}/confirm`
 * republishes the action and returns immediately, so a 200 proves only that the
 * house accepted the answer (spec §5.2.1). `applied` comes from the
 * `home_action_results` stream and nowhere else.
 */
export type ActionPhase = "pending" | "queued" | "applied" | "expired" | "answered";

export interface TrackedAction {
  action: PendingAction;
  phase: ActionPhase;
  /** ISO, when the confirm POST succeeded. */
  confirmedAt?: string;
  /** ISO, when the result arrived on the telemetry stream. */
  appliedAt?: string;
  result?: ActionResultEvent;
}

export type ActionEvent =
  | { type: "loaded"; actions: PendingAction[] }
  | { type: "arrived"; action: PendingAction }
  | { type: "confirm-sent"; id: string; at: string }
  | { type: "confirm-404"; id: string }
  | { type: "result"; result: ActionResultEvent; at: string }
  | { type: "tick"; now: number };

/** Seconds left on the server's `expires_at`. Never negative; 0 if unreadable. */
export function fuseRemaining(action: PendingAction, now: number): number {
  const expires = Date.parse(action.expires_at);
  if (Number.isNaN(expires)) return 0;
  return Math.max(0, (expires - now) / 1000);
}

function byAge(a: TrackedAction, b: TrackedAction): number {
  return Date.parse(a.action.timestamp) - Date.parse(b.action.timestamp);
}

export function actionReducer(state: TrackedAction[], ev: ActionEvent): TrackedAction[] {
  switch (ev.type) {
    case "loaded": {
      const known = new Map(state.map((item) => [item.action.request_id, item]));
      const listed = new Set(ev.actions.map((action) => action.request_id));

      const loaded: TrackedAction[] = ev.actions.map((action) => {
        const previous = known.get(action.request_id);
        return previous ? { ...previous, action } : { action, phase: "pending" };
      });

      // Everything we track that the list did not mention, untouched. This read
      // races the deep link's own `GET /api/actions/{id}` on a cold launch, and
      // demoting a `pending` action because an empty list landed second would
      // tombstone a live approval. One consumed elsewhere still resolves: `tick`
      // expires it, a 404 confirm answers it, a result frame applies it.
      const kept: TrackedAction[] = state.filter(
        (item) => !listed.has(item.action.request_id),
      );

      return [...kept, ...loaded].sort(byAge);
    }

    case "arrived": {
      const id = ev.action.request_id;
      if (state.some((item) => item.action.request_id === id)) {
        return state.map((item) =>
          item.action.request_id === id ? { ...item, action: ev.action } : item,
        );
      }
      const arrived: TrackedAction = { action: ev.action, phase: "pending" };
      return [...state, arrived].sort(byAge);
    }

    case "confirm-sent":
      return state.map((item) =>
        item.action.request_id === ev.id
          ? { ...item, phase: "queued", confirmedAt: ev.at }
          : item,
      );

    case "confirm-404":
      return state.map((item) =>
        item.action.request_id === ev.id ? { ...item, phase: "answered" } : item,
      );

    case "result":
      // Applied even from `expired`: if the lock reported, it happened, and the
      // tombstone was wrong.
      return state.map((item) =>
        item.action.request_id === ev.result.request_id
          ? { ...item, phase: "applied", appliedAt: ev.at, result: ev.result }
          : item,
      );

    case "tick": {
      // Identity when nothing changed — this runs once a second and must not
      // re-render the Room for a fuse that merely moved.
      const lapsed = state.some(
        (item) => item.phase === "pending" && fuseRemaining(item.action, ev.now) <= 0,
      );
      if (!lapsed) return state;
      return state.map((item) =>
        item.phase === "pending" && fuseRemaining(item.action, ev.now) <= 0
          ? { ...item, phase: "expired" }
          : item,
      );
    }
  }

  return state;
}

/** `GET /api/actions/pending` — oldest first, per the route's own contract. */
export async function fetchPending(): Promise<PendingAction[]> {
  const body = await api<{ actions?: PendingAction[] }>("/api/actions/pending");
  return body.actions ?? [];
}

/** `GET /api/actions/{id}` — 404 means consumed or expired. Throws `ApiError`. */
export function fetchAction(id: string): Promise<PendingAction> {
  return api<PendingAction>(`/api/actions/${encodeURIComponent(id)}`);
}

/** `POST /api/actions/{id}/confirm` — 200 is `queued`, 404 is already answered. */
export async function confirmAction(id: string): Promise<void> {
  await post<{ status: string }>(`/api/actions/${encodeURIComponent(id)}/confirm`);
}
```

- [ ] **Step 5: Run the reducer test**

Run: `npm test -- src/lib/actions.test.ts`
Expected: `Test Files  1 passed (1)`, 23 tests.

- [ ] **Step 6: Write the failing provider test**

Create `web/src/door/DoorProvider.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatServerMessage, TelemetryMessage } from "@/lib/types";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import {
  actionResultFixture,
  pendingActionFixture,
  secondPendingActionFixture,
} from "@/test/fixtures";
import { DoorProvider, useDoor } from "./DoorProvider";

const { chats, telemetries } = vi.hoisted(() => ({
  chats: [] as unknown[],
  telemetries: [] as unknown[],
}));

vi.mock("@/lib/chat-socket", () => {
  class ChatSocket {
    onstatus: (status: string) => void = () => {};
    listeners = new Set<(msg: ChatServerMessage) => void>();
    connect = vi.fn();
    close = vi.fn();
    sendText = vi.fn(() => true);
    sendAudio = vi.fn(() => true);
    constructor() {
      chats.push(this);
    }
    listen(fn: (msg: ChatServerMessage) => void): () => void {
      this.listeners.add(fn);
      return () => void this.listeners.delete(fn);
    }
    deliver(msg: ChatServerMessage): void {
      for (const fn of [...this.listeners]) fn(msg);
    }
  }
  return { ChatSocket };
});

vi.mock("@/lib/telemetry-socket", () => {
  class TelemetrySocket {
    onstatus: (status: string) => void = () => {};
    listeners = new Set<(msg: TelemetryMessage) => void>();
    connect = vi.fn();
    close = vi.fn();
    subscribe = vi.fn();
    constructor() {
      telemetries.push(this);
    }
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

interface FakeChat {
  deliver: (msg: ChatServerMessage) => void;
}
interface FakeTelemetry {
  subscribe: ReturnType<typeof vi.fn>;
  deliver: (msg: TelemetryMessage) => void;
}

let pendingBody: unknown = { actions: [] };
let getStatus = 200;
let confirmStatus = 200;
const calls: string[] = [];

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/api/actions/pending")
        return new Response(JSON.stringify(pendingBody), { status: 200 });
      if (url.endsWith("/confirm"))
        return new Response(
          confirmStatus === 200
            ? '{"status":"confirmed"}'
            : '{"detail":"Pending action not found or expired"}',
          { status: confirmStatus },
        );
      if (url.startsWith("/api/actions/"))
        return new Response(
          getStatus === 200
            ? JSON.stringify(pendingActionFixture)
            : '{"detail":"Pending action not found or expired"}',
          { status: getStatus },
        );
      return new Response("{}", { status: 404 });
    }),
  );
}

function Probe() {
  const door = useDoor();
  return (
    <div>
      <span data-testid="phases">
        {door.actions.map((item) => `${item.action.request_id}:${item.phase}`).join(",")}
      </span>
      <span data-testid="pending">{door.pending.length}</span>
      <span data-testid="open">{door.open ? door.current?.action.request_id : "closed"}</span>
      <button type="button" onClick={() => door.openAction("a91f3c2e")}>
        open
      </button>
      <button type="button" onClick={door.close}>
        close
      </button>
      <button type="button" onClick={() => door.confirm("a91f3c2e")}>
        confirm
      </button>
    </div>
  );
}

function renderDoor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <DoorProvider>
          <Probe />
        </DoorProvider>
      </ConnectionProvider>
    </QueryClientProvider>,
  );
  return {
    chat: chats.at(-1) as FakeChat,
    telemetry: telemetries.at(-1) as FakeTelemetry,
  };
}

const phases = () => screen.getByTestId("phases").textContent;

beforeEach(() => {
  calls.length = 0;
  pendingBody = { actions: [] };
  getStatus = 200;
  confirmStatus = 200;
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-09-07T07:42:00Z"));
  stubFetch();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("DoorProvider", () => {
  it("loads whatever is already waiting, oldest first", async () => {
    pendingBody = { actions: [secondPendingActionFixture, pendingActionFixture] };
    renderDoor();

    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending,7c2e0b1d:pending"));
    expect(screen.getByTestId("pending")).toHaveTextContent("2");
  });

  it("subscribes to home_action_results and nothing else", () => {
    const { telemetry } = renderDoor();
    expect(telemetry.subscribe).toHaveBeenCalledWith(["home_action_results"]);
  });

  it("fetches the action a confirmation notification names", async () => {
    const { chat } = renderDoor();

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Confirmation required",
        body: "Alfred wants to run 'home.lock_unlock' on home-service — confirm?",
        urgency: "urgent",
        notification_id: "ntf-1",
        metadata: { pending_action_id: "a91f3c2e" },
      }),
    );

    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));
    expect(calls).toContain("GET /api/actions/a91f3c2e");
  });

  it("ignores a notification with no action on it", async () => {
    const { chat } = renderDoor();

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Bins go out tonight",
        body: "Collection moved to Friday.",
        urgency: "important",
        notification_id: "ntf-2",
        metadata: {},
      }),
    );

    await waitFor(() => expect(calls.some((call) => call.includes("/api/actions/a"))).toBe(false));
    expect(phases()).toBe("");
  });

  it("ignores an action that has already gone by the time we ask", async () => {
    getStatus = 404;
    const { chat } = renderDoor();

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Confirmation required",
        body: "…",
        urgency: "urgent",
        notification_id: "ntf-3",
        metadata: { pending_action_id: "a91f3c2e" },
      }),
    );

    await waitFor(() => expect(calls).toContain("GET /api/actions/a91f3c2e"));
    expect(phases()).toBe("");
  });

  it("applies only on a result carrying the same request id", async () => {
    pendingBody = { actions: [pendingActionFixture] };
    const { telemetry } = renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    act(() =>
      telemetry.deliver({
        type: "entry",
        stream: "home_action_results",
        id: "1757000000000-0",
        event: { ...actionResultFixture, request_id: "0000" } as unknown as Record<string, unknown>,
      }),
    );
    expect(phases()).toBe("a91f3c2e:pending");

    act(() =>
      telemetry.deliver({
        type: "entry",
        stream: "home_action_results",
        id: "1757000000001-0",
        event: actionResultFixture as unknown as Record<string, unknown>,
      }),
    );
    expect(phases()).toBe("a91f3c2e:applied");
  });

  it("confirms, and calls it queued rather than applied", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    pendingBody = { actions: [pendingActionFixture] };
    renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    await user.click(screen.getByRole("button", { name: "confirm" }));

    await waitFor(() => expect(phases()).toBe("a91f3c2e:queued"));
    expect(calls).toContain("POST /api/actions/a91f3c2e/confirm");
  });

  it("marks a 404 confirm as already answered", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    confirmStatus = 404;
    pendingBody = { actions: [pendingActionFixture] };
    renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    await user.click(screen.getByRole("button", { name: "confirm" }));

    await waitFor(() => expect(phases()).toBe("a91f3c2e:answered"));
  });

  it("expires a pending action when its fuse runs out", async () => {
    pendingBody = { actions: [pendingActionFixture] };
    renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    // 07:42 → 07:47: past the 07:46 expiry.
    await act(async () => {
      vi.advanceTimersByTime(5 * 60_000);
    });

    expect(phases()).toBe("a91f3c2e:expired");
  });

  it("opens and closes one action at a time", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    pendingBody = { actions: [pendingActionFixture] };
    renderDoor();
    await waitFor(() => expect(phases()).toBe("a91f3c2e:pending"));

    expect(screen.getByTestId("open")).toHaveTextContent("closed");

    await user.click(screen.getByRole("button", { name: "open" }));
    expect(screen.getByTestId("open")).toHaveTextContent("a91f3c2e");

    await user.click(screen.getByRole("button", { name: "close" }));
    expect(screen.getByTestId("open")).toHaveTextContent("closed");
  });

  it("refuses to be used outside the provider", () => {
    expect(() => render(<Probe />)).toThrow("useDoor outside DoorProvider");
  });
});
```

- [ ] **Step 7: Run it to verify it fails**

Run: `npm test -- src/door/DoorProvider.test.tsx`
Expected: FAIL — `Failed to resolve import "./DoorProvider"`.

- [ ] **Step 8: Write `web/src/door/DoorProvider.tsx`**

Complete file:

```tsx
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useState,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";
import {
  actionReducer,
  confirmAction,
  fetchAction,
  fetchPending,
  type TrackedAction,
} from "@/lib/actions";
import { ApiError } from "@/lib/api";
import type { ActionResultEvent, PendingAction } from "@/lib/types";
import { useConnection } from "@/shell/ConnectionProvider";

/** The fuse steps once a second, exactly as the handoff draws it. */
const TICK_MS = 1000;
/** The only stream the Door needs; "applied" is what it carries. */
const RESULT_STREAM = "home_action_results";

export interface DoorValue {
  /** Everything tracked, oldest first — including tombstones. */
  actions: TrackedAction[];
  /** Just the ones still waiting on you. */
  pending: TrackedAction[];
  current: TrackedAction | null;
  open: boolean;
  openAction: (id: string) => void;
  close: () => void;
  confirm: (id: string) => void;
  /** Push an action in from outside — the `/actions/:id` deep link uses this. */
  arrived: (action: PendingAction) => void;
  /** Epoch ms, stepped once a second while anything is counting. */
  now: number;
}

const DoorContext = createContext<DoorValue | null>(null);

export function DoorProvider({ children }: { children: ReactNode }) {
  const { chat, telemetry } = useConnection();
  const [actions, dispatch] = useReducer(actionReducer, []);
  const [openId, setOpenId] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Cold launch and every return from the background: ConnectionProvider
  // invalidates this key on `visibilitychange` (1a, Task 12).
  const { data } = useQuery<PendingAction[]>({
    queryKey: ["pending-actions"],
    queryFn: fetchPending,
    staleTime: 10_000,
  });

  useEffect(() => {
    if (data) dispatch({ type: "loaded", actions: data });
  }, [data]);

  const arrived = useCallback((action: PendingAction) => {
    dispatch({ type: "arrived", action });
  }, []);

  // The app was open when the conscious engine asked. The notification carries
  // the id but not the action, so read it — and say nothing if it has already gone.
  useEffect(() => {
    return chat.listen((msg) => {
      if (msg.type !== "notification") return;
      const id = msg.metadata?.pending_action_id;
      if (typeof id !== "string") return;
      void fetchAction(id)
        .then((action) => dispatch({ type: "arrived", action }))
        .catch(() => {
          // 404: consumed or expired before we asked. Nothing to show.
        });
    });
  }, [chat]);

  // The one true source of "applied".
  useEffect(() => {
    telemetry.subscribe([RESULT_STREAM]);
    return telemetry.listen((msg) => {
      if (msg.type !== "entry" || msg.stream !== RESULT_STREAM) return;
      const result = msg.event as unknown as ActionResultEvent;
      if (typeof result?.request_id !== "string") return;
      dispatch({ type: "result", result, at: new Date().toISOString() });
    });
  }, [telemetry]);

  // Only run a clock while something is actually counting: a Room with no
  // pending approval must not re-render once a second for ever.
  const counting = actions.some((item) => item.phase === "pending" || item.phase === "queued");

  useEffect(() => {
    if (!counting) return;
    const timer = setInterval(() => {
      const at = Date.now();
      setNow(at);
      dispatch({ type: "tick", now: at });
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [counting]);

  const confirm = useCallback((id: string) => {
    void confirmAction(id)
      .then(() => dispatch({ type: "confirm-sent", id, at: new Date().toISOString() }))
      .catch((error: unknown) => {
        // 404 is the handoff's "already consumed" case: the same tombstone as an
        // expiry, with a different sentence. Anything else leaves it pending so
        // the user can try again while the fuse still runs.
        if (error instanceof ApiError && error.status === 404) {
          dispatch({ type: "confirm-404", id });
        }
      });
  }, []);

  const value = useMemo<DoorValue>(() => {
    const current = actions.find((item) => item.action.request_id === openId) ?? null;
    return {
      actions,
      pending: actions.filter((item) => item.phase === "pending"),
      current,
      open: current !== null,
      openAction: setOpenId,
      close: () => setOpenId(null),
      confirm,
      arrived,
      now,
    };
  }, [actions, openId, now, confirm, arrived]);

  return <DoorContext.Provider value={value}>{children}</DoorContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useDoor(): DoorValue {
  const ctx = useContext(DoorContext);
  if (!ctx) throw new Error("useDoor outside DoorProvider");
  return ctx;
}
```

- [ ] **Step 9: Run the provider test and the suite**

Run: `npm test -- src/door/DoorProvider.test.tsx`
Expected: `Test Files  1 passed (1)`, 11 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  35 passed (35)`, no eslint output, `✓ built in …`.

- [ ] **Step 10: Commit**

```bash
git add web/src/lib/actions.ts web/src/lib/actions.test.ts web/src/door web/src/test/fixtures.ts
git commit -m "feat(web): track pending approvals from three sources at once"
```

---

### Task 25: The banner, the ring, and what an unanswered approval leaves behind

Two visible consequences of the store built in Task 24.

**The banner.** While anything is pending, an ink-on-paper bar sits above the composer with a 34 px fuse ring, the humanised tool name and the time left. It is the whole reason the Door does not need to interrupt: the approval is announced, and opening it is the user's decision.

**The tombstone.** An approval that lapsed or was answered elsewhere does not vanish — it becomes a struck-through row in the thread saying what it was and that nothing was done. `expired · not done` is in the closed status vocabulary, and it is the one thing the design insists a fuse must leave behind.

`FuseRing` is built here because the banner needs it; Task 26 uses the same component at 168 px. The two mask radii are the prototype's own numbers, not a formula — a 34 px ring masked with the 168 px proportions is a solid disc.

**Files:**
- Create: `web/src/door/FuseRing.tsx`, `web/src/door/DoorBanner.tsx`, `web/src/door/DoorBanner.test.tsx`
- Modify: `web/src/lib/actions.ts`, `web/src/lib/actions.test.ts`, `web/src/index.css`

- [ ] **Step 1: Write the failing tombstone test**

Append to `web/src/lib/actions.test.ts` (extend the import from `./actions` with `tombstoneItems`, and add `import { hhmm } from "./format";` after it):

```ts
describe("tombstoneItems", () => {
  it("says an expired approval was not done, and when it was asked", () => {
    const [item] = tombstoneItems([tracked("expired")]);
    expect(item).toMatchObject({
      kind: "tombstone",
      id: "tomb:a91f3c2e",
      at: "2026-09-07T07:46:00Z",
      title: "Lock unlock",
    });
    expect(item.kind === "tombstone" && item.meta).toBe(
      `expired ${hhmm("2026-09-07T07:46:00Z")} · not done · asked ${hhmm("2026-09-07T07:41:00Z")}`,
    );
  });

  it("says an already-answered one differently", () => {
    const [item] = tombstoneItems([tracked("answered")]);
    expect(item.kind === "tombstone" && item.meta).toBe(
      `already answered · asked ${hhmm("2026-09-07T07:41:00Z")}`,
    );
  });

  it("leaves live, queued and applied approvals out of the thread", () => {
    expect(tombstoneItems([tracked("pending"), tracked("queued"), tracked("applied")])).toEqual([]);
  });

  it("returns one row per action", () => {
    const items = tombstoneItems([
      tracked("expired"),
      { action: secondPendingActionFixture, phase: "answered" },
    ]);
    expect(items.map((item) => item.id)).toEqual(["tomb:a91f3c2e", "tomb:7c2e0b1d"]);
  });

  it("is empty for an empty store", () => {
    expect(tombstoneItems([])).toEqual([]);
  });
});
```

Add `import { hhmm } from "./format";` at the top of the test file — the meta is asserted against the same local-clock formatter the component uses, so the test does not depend on the runner's timezone.

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/lib/actions.test.ts`
Expected: FAIL — `The requested module './actions' does not provide an export named 'tombstoneItems'`.

- [ ] **Step 3: Add `tombstoneItems` to `web/src/lib/actions.ts`**

Add to the imports at the top of the file:

```ts
import { hhmm, humaniseTool } from "./format";
import type { TimelineItem } from "./history";
```

and append at the end of the file:

```ts
/**
 * What an unanswered approval leaves in the thread.
 *
 * `expired · not done` is the closed status vocabulary's own phrase, and the
 * design's whole argument for the fuse: a decision that lapsed must leave a mark,
 * not disappear. `already answered` is the 404 case — someone, or something else,
 * got there first.
 */
export function tombstoneItems(actions: TrackedAction[]): TimelineItem[] {
  const items: TimelineItem[] = [];
  for (const item of actions) {
    if (item.phase !== "expired" && item.phase !== "answered") continue;
    const asked = hhmm(item.action.timestamp);
    items.push({
      kind: "tombstone",
      id: `tomb:${item.action.request_id}`,
      at: item.action.expires_at,
      title: humaniseTool(item.action.tool_name),
      meta:
        item.phase === "expired"
          ? `expired ${hhmm(item.action.expires_at)} · not done · asked ${asked}`
          : `already answered · asked ${asked}`,
    });
  }
  return items;
}
```

Run: `npm test -- src/lib/actions.test.ts`
Expected: `Test Files  1 passed (1)`, 28 tests.

- [ ] **Step 4: Write the failing banner test**

Create `web/src/door/DoorBanner.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { TrackedAction } from "@/lib/actions";
import { pendingActionFixture } from "@/test/fixtures";
import { DoorBanner } from "./DoorBanner";
import { FuseRing } from "./FuseRing";

const T0742 = Date.parse("2026-09-07T07:42:00Z");
const T0745_40 = Date.parse("2026-09-07T07:45:40Z");
const T0747 = Date.parse("2026-09-07T07:47:00Z");

const tracked: TrackedAction = { action: pendingActionFixture, phase: "pending" };

describe("FuseRing", () => {
  it("hands the arc its fraction and its colour as custom properties", () => {
    render(<FuseRing percent={80} size={168} danger={false} />);
    const arc = screen.getByTestId("fuse-arc");
    // Custom properties, not an inline `background`: jsdom's CSS parser drops a
    // conic-gradient shorthand it cannot parse, and the assertion would be
    // meaningless. The gradient itself lives in index.css.
    expect(arc.style.getPropertyValue("--fuse-pct")).toBe("80.0%");
    expect(arc.style.getPropertyValue("--fuse-color")).toBe("var(--accent)");
    expect(arc).toHaveClass("fuse-arc");
  });

  it("turns to paper in the last thirty seconds", () => {
    render(<FuseRing percent={9} size={168} danger />);
    expect(screen.getByTestId("fuse-arc").style.getPropertyValue("--fuse-color")).toBe(
      "var(--paper)",
    );
  });

  it("masks each size with that size's own class", () => {
    const { rerender } = render(<FuseRing percent={50} size={168} danger={false} />);
    expect(screen.getByTestId("fuse-arc")).toHaveClass("fuse-168");

    rerender(<FuseRing percent={50} size={34} danger={false} />);
    expect(screen.getByTestId("fuse-arc")).toHaveClass("fuse-34");
  });

  it("is drawn at the size it was asked for", () => {
    render(<FuseRing percent={50} size={34} danger={false} />);
    const ring = screen.getByTestId("fuse-ring");
    expect(ring.style.width).toBe("34px");
    expect(ring.style.height).toBe("34px");
  });

  it("puts its children in the middle of the ring", () => {
    render(
      <FuseRing percent={50} size={168} danger={false}>
        <span>4:12</span>
      </FuseRing>,
    );
    expect(screen.getByText("4:12")).toBeInTheDocument();
  });
});

describe("DoorBanner", () => {
  it("names the action, counts it down, and says who it is for", () => {
    render(<DoorBanner tracked={tracked} now={T0742} onOpen={() => {}} />);

    expect(screen.getByText("Lock unlock")).toBeInTheDocument();
    expect(screen.getByText("expires in 4:00 · asked by Alfred, for you")).toBeInTheDocument();
    expect(screen.getByText("Open")).toBeInTheDocument();
  });

  it("draws the arc as the fraction of the server's own TTL", () => {
    render(<DoorBanner tracked={tracked} now={T0742} onOpen={() => {}} />);
    // 240 s left of 300.
    expect(screen.getByTestId("fuse-arc")).toHaveAttribute("data-percent", "80.0");
  });

  it("goes to paper under thirty seconds", () => {
    render(<DoorBanner tracked={tracked} now={T0745_40} onOpen={() => {}} />);
    expect(screen.getByTestId("fuse-arc").style.getPropertyValue("--fuse-color")).toBe(
      "var(--paper)",
    );
  });

  it("reads 0:00 rather than a negative fuse", () => {
    render(<DoorBanner tracked={tracked} now={T0747} onOpen={() => {}} />);
    expect(screen.getByText("expires in 0:00 · asked by Alfred, for you")).toBeInTheDocument();
    expect(screen.getByTestId("fuse-arc")).toHaveAttribute("data-percent", "0.0");
  });

  it("is one tap target, and opens the Door", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<DoorBanner tracked={tracked} now={T0742} onOpen={onOpen} />);

    await user.click(screen.getByRole("button", { name: /Lock unlock/ }));

    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("survives a zero TTL without dividing by it", () => {
    render(
      <DoorBanner
        tracked={{ action: { ...pendingActionFixture, ttl_seconds: 0 }, phase: "pending" }}
        now={T0742}
        onOpen={() => {}}
      />,
    );
    expect(screen.getByTestId("fuse-arc")).toHaveAttribute("data-percent", "0.0");
  });
});
```

- [ ] **Step 5: Run it to verify it fails**

Run: `npm test -- src/door/DoorBanner.test.tsx`
Expected: FAIL — `Failed to resolve import "./DoorBanner"`.

- [ ] **Step 6: Add the ring's CSS to `web/src/index.css`**

Inside the `@layer components` block, after the `.pb-keyboard.keyboard-up` rule, insert:

```css
  /* ------------------------------------------------------------------- fuse
     The arc is a conic gradient driven by two custom properties the component
     sets, so the gradient itself never has to be built in JS. 900 ms linear
     turns each one-second step into a sweep; nothing pulses. The two masks are
     the prototype's own radii per size — not a formula: applying either one's
     proportions to the other size gives a solid disc instead of a hairline. */
  .fuse-arc {
    background: conic-gradient(var(--fuse-color) var(--fuse-pct), var(--ring) 0);
    transition: background 0.9s linear;
  }
  .fuse-168 {
    -webkit-mask-image: radial-gradient(circle, transparent 79px, #000 80px);
    mask-image: radial-gradient(circle, transparent 79px, #000 80px);
  }
  .fuse-34 {
    -webkit-mask-image: radial-gradient(circle, transparent 14px, #000 15px);
    mask-image: radial-gradient(circle, transparent 14px, #000 15px);
  }
```

- [ ] **Step 7: Write `web/src/door/FuseRing.tsx`**

Complete file:

```tsx
import type { CSSProperties, ReactNode } from "react";

export interface FuseRingProps {
  /** 0–100, the fraction of the TTL still to run. */
  percent: number;
  size: 34 | 168;
  /** Under thirty seconds the arc goes from accent to paper (handoff). */
  danger: boolean;
  children?: ReactNode;
}

/**
 * The fuse, at both the sizes the design uses: 34 px in the banner, 168 px in
 * the Door. The arc and its mask are CSS (`.fuse-arc`, `.fuse-34`, `.fuse-168`);
 * this only decides how far round it has gone and what colour that is.
 */
export function FuseRing({ percent, size, danger, children }: FuseRingProps) {
  const pct = percent.toFixed(1);
  const arcStyle = {
    "--fuse-pct": `${pct}%`,
    "--fuse-color": danger ? "var(--paper)" : "var(--accent)",
  } as CSSProperties;

  return (
    <div
      data-testid="fuse-ring"
      className="relative flex shrink-0 items-center justify-center"
      style={{ width: `${size}px`, height: `${size}px` }}
    >
      <div
        aria-hidden="true"
        data-testid="fuse-arc"
        data-percent={pct}
        className={`fuse-arc fuse-${size} absolute inset-0 rounded-full`}
        style={arcStyle}
      />
      {children}
    </div>
  );
}
```

- [ ] **Step 8: Write `web/src/door/DoorBanner.tsx`**

Complete file:

```tsx
import { fuseRemaining, type TrackedAction } from "@/lib/actions";
import { humaniseTool, mmss } from "@/lib/format";
import { FuseRing } from "@/door/FuseRing";

/** Under this the ring changes colour — the handoff's only "hurry" signal. */
const DANGER_SECONDS = 30;

export interface DoorBannerProps {
  tracked: TrackedAction;
  /** Epoch ms from `useDoor().now`, so every fuse on screen agrees. */
  now: number;
  onOpen: () => void;
}

/**
 * Above the composer while an approval waits. Ink on paper, so it reads as the
 * Door's own colour arriving early — and the whole bar is the tap target, which
 * is how a 34 px ring and a 13 px word can sit in a 44 pt control.
 */
export function DoorBanner({ tracked, now, onOpen }: DoorBannerProps) {
  const remaining = fuseRemaining(tracked.action, now);
  const ttl = tracked.action.ttl_seconds;
  const percent = ttl > 0 ? Math.max(0, Math.min(100, (remaining / ttl) * 100)) : 0;

  return (
    <button
      type="button"
      onClick={onOpen}
      className="relative z-[1] mx-4 mb-2.5 flex items-center gap-3.5 rounded-2xl border-0 px-4 py-3.5 text-left"
      style={{ background: "var(--ink)", color: "var(--paper)" }}
    >
      <FuseRing percent={percent} size={34} danger={remaining <= DANGER_SECONDS} />

      <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
        <div className="text-[16px] font-medium">{humaniseTool(tracked.action.tool_name)}</div>
        {/* Not `.t-meta`: that class pins the colour to --muted, which is a Room
            token and disappears against ink. */}
        <div className="font-mono text-[11px] leading-[1.5]" style={{ color: "var(--paper-muted)" }}>
          expires in {mmss(remaining)} · asked by Alfred, for you
        </div>
      </div>

      <div className="text-[13px] font-medium" style={{ color: "var(--accent)" }}>
        Open
      </div>
    </button>
  );
}
```

- [ ] **Step 9: Run the banner test and the suite**

Run: `npm test -- src/door/DoorBanner.test.tsx`
Expected: `Test Files  1 passed (1)`, 11 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  36 passed (36)`, no eslint output, `✓ built in …`.

- [ ] **Step 10: Commit**

```bash
git add web/src/door/FuseRing.tsx web/src/door/DoorBanner.tsx web/src/door/DoorBanner.test.tsx web/src/lib/actions.ts web/src/lib/actions.test.ts web/src/index.css
git commit -m "feat(web): the door banner, the fuse ring and the tombstone it leaves"
```

---

### Task 26: The Door

The one screen in the app that inverts. Ink and paper swap, a 168 px fuse counts down in the middle, and the only way to say yes is to drag a knob most of the way across a 64 px track. Spec §5.3: "a high-stakes confirmation that cannot misfire on a touchscreen, with no haptics available to add weight" — the weight is the travel.

The easing in `slideKnob` is the interesting part. `r < 0.25 ? r * 0.6 : 0.15 + (r - 0.25) * 1.1333` means the knob moves at 60 % of your finger for the first quarter and then catches up: it *resists* at the start, so a brush against the track goes almost nowhere, and a deliberate drag ends where your finger is. Ported exactly from the prototype's `slideMove`.

Five phases, five sentences. `queued` is not `applied`; `expired` says what did not happen and how to ask again; `answered` is the 404 the handoff calls "already consumed".

**Files:**
- Create: `web/src/lib/slide.ts`, `web/src/lib/slide.test.ts`
- Create: `web/src/door/SlideToConfirm.tsx`, `web/src/door/DoorLayer.tsx`, `web/src/door/DoorLayer.test.tsx`

- [ ] **Step 1: Write the failing slide-maths test**

Create `web/src/lib/slide.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { CONFIRM_RATIO, hintOpacity, slideKnob } from "./slide";

/** 393 pt phone, 20 px gutters, 64 px of track taken by the knob and its inset. */
const MAX = 289;

describe("CONFIRM_RATIO", () => {
  it("is 85% of the travel", () => {
    expect(CONFIRM_RATIO).toBe(0.85);
  });
});

describe("slideKnob", () => {
  it("starts at home", () => {
    expect(slideKnob(0, MAX)).toBe(0);
  });

  it("resists for the first quarter, at 60% of the finger", () => {
    expect(slideKnob(MAX * 0.1, MAX)).toBeCloseTo(MAX * 0.06, 5);
    expect(slideKnob(MAX * 0.25, MAX)).toBeCloseTo(MAX * 0.15, 5);
  });

  it("catches up after the resistance", () => {
    // 0.15 + (0.5 - 0.25) * 1.1333
    expect(slideKnob(MAX * 0.5, MAX)).toBeCloseTo(MAX * 0.433325, 4);
  });

  it("lands under the finger at the far end", () => {
    expect(slideKnob(MAX, MAX)).toBeCloseTo(MAX, 1);
  });

  it("never goes backwards or past the end", () => {
    expect(slideKnob(-200, MAX)).toBe(0);
    expect(slideKnob(MAX * 3, MAX)).toBeLessThanOrEqual(MAX);
  });

  it("crosses the confirm threshold only after a deliberate drag", () => {
    expect(slideKnob(MAX * 0.86, MAX)).toBeLessThan(MAX * CONFIRM_RATIO);
    expect(slideKnob(MAX * 0.88, MAX)).toBeGreaterThan(MAX * CONFIRM_RATIO);
  });

  it("is zero on a track with no travel", () => {
    expect(slideKnob(120, 0)).toBe(0);
  });
});

describe("hintOpacity", () => {
  it("fades the hint out over the first half of the travel", () => {
    expect(hintOpacity(0, MAX)).toBe(1);
    expect(hintOpacity(MAX * 0.25, MAX)).toBeCloseTo(0.5, 5);
    expect(hintOpacity(MAX * 0.5, MAX)).toBe(0);
  });

  it("never goes negative", () => {
    expect(hintOpacity(MAX, MAX)).toBe(0);
  });

  it("is fully visible before the track has been measured", () => {
    expect(hintOpacity(0, 0)).toBe(1);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/lib/slide.test.ts`
Expected: FAIL — `Failed to resolve import "./slide"`.

- [ ] **Step 3: Write `web/src/lib/slide.ts`**

Complete file:

```ts
/** Release at or past this fraction of the travel and it counts as a yes. */
export const CONFIRM_RATIO = 0.85;

/**
 * Where the knob sits for a finger `dx` px from where it started.
 *
 * Ported from `slideMove` in `Alfred.dc.html`: the knob moves at 60 % of the
 * finger for the first quarter of the track and then catches up at 113 %. That
 * asymmetry is the whole safety argument — a brush against the track goes almost
 * nowhere, and a deliberate drag still ends under the finger.
 */
export function slideKnob(dx: number, max: number): number {
  if (max <= 0) return 0;
  const r = Math.max(0, Math.min(1, dx / max));
  const eased = r < 0.25 ? r * 0.6 : 0.15 + (r - 0.25) * 1.1333;
  return Math.min(max, eased * max);
}

/**
 * "Slide to confirm" fades out over the first half of the travel, so the
 * instruction is gone by the time it would be under the knob.
 */
export function hintOpacity(knob: number, max: number): number {
  if (max <= 0) return 1;
  return Math.max(0, 1 - knob / (max * 0.5));
}
```

Run: `npm test -- src/lib/slide.test.ts`
Expected: `Test Files  1 passed (1)`, 11 tests.

- [ ] **Step 4: Write the failing Door test**

Create `web/src/door/DoorLayer.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TrackedAction } from "@/lib/actions";
import { hhmm } from "@/lib/format";
import { pendingActionFixture } from "@/test/fixtures";
import { DoorLayer } from "./DoorLayer";

const T0742 = Date.parse("2026-09-07T07:42:00Z");
/** 353 px track − 64 = 289 px of travel, the 393 pt phone's real geometry. */
const TRACK_WIDTH = 353;
const MAX = TRACK_WIDTH - 64;

function at(phase: TrackedAction["phase"], extra: Partial<TrackedAction> = {}): TrackedAction {
  return { action: pendingActionFixture, phase, ...extra };
}

function renderDoor(tracked: TrackedAction, options: { online?: boolean } = {}) {
  const onClose = vi.fn();
  const onConfirm = vi.fn();
  render(
    <DoorLayer
      tracked={tracked}
      open
      online={options.online ?? true}
      now={T0742}
      onClose={onClose}
      onConfirm={onConfirm}
    />,
  );
  return { onClose, onConfirm };
}

function drag(toX: number): void {
  const track = screen.getByTestId("slide-track");
  fireEvent.pointerDown(track, { clientX: 0, pointerId: 1 });
  fireEvent.pointerMove(track, { clientX: toX, pointerId: 1 });
  fireEvent.pointerUp(track, { clientX: toX, pointerId: 1 });
}

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => TRACK_WIDTH,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DoorLayer — what it says", () => {
  it("labels itself critical, with the short request id", () => {
    renderDoor(at("pending"));
    expect(screen.getByRole("dialog", { name: "Critical approval" })).toBeInTheDocument();
    expect(screen.getByText("CRITICAL · a91f")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Leave it" })).toBeInTheDocument();
  });

  it("counts the fuse down and says what it is counting to", () => {
    renderDoor(at("pending"));
    expect(screen.getByText("4:00")).toBeInTheDocument();
    expect(screen.getByText("until it lapses")).toBeInTheDocument();
  });

  it("names the action, gives Alfred's reason, and shows the exact call", () => {
    renderDoor(at("pending"));
    expect(screen.getByRole("heading", { name: "Lock unlock" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "You asked me to let the cleaner in when she rings. She rang at 07:41.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'home.lock_unlock { entity_id: "lock.front_door", action: "unlock" }',
      ),
    ).toBeInTheDocument();
  });

  it("states the request plainly when the engine gave no reason", () => {
    renderDoor({ action: { ...pendingActionFixture, reason: null }, phase: "pending" });
    expect(
      screen.getByText("Alfred wants to run 'home.lock_unlock' on home-service."),
    ).toBeInTheDocument();
  });
});

describe("DoorLayer — pending", () => {
  it("offers the slider and says what confirming does and does not do", () => {
    renderDoor(at("pending"));
    expect(screen.getByTestId("slide-track")).toBeInTheDocument();
    expect(screen.getByText("Slide to confirm")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Approval only; the lock itself reports back separately. Releasing before the end snaps back.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back to the room" })).toBeNull();
  });

  it("confirms on a drag past 85% of the travel", () => {
    const { onConfirm } = renderDoor(at("pending"));

    drag(Math.round(MAX * 0.9));

    expect(onConfirm).toHaveBeenCalledWith("a91f3c2e");
    const knob = screen.getByTestId("slide-knob");
    expect(knob).toHaveAttribute("data-knob", String(MAX));
    // `settle` is the 600 ms overshoot curve, used exactly here and nowhere else.
    expect(knob).toHaveAttribute("data-motion", "settle");
  });

  it("snaps home without confirming when released early", () => {
    const { onConfirm } = renderDoor(at("pending"));

    drag(Math.round(MAX * 0.34));

    expect(onConfirm).not.toHaveBeenCalled();
    const knob = screen.getByTestId("slide-knob");
    expect(knob).toHaveAttribute("data-knob", "0");
    expect(knob).toHaveAttribute("data-motion", "snap");
  });

  it("follows the finger with no transition while dragging", () => {
    renderDoor(at("pending"));
    const track = screen.getByTestId("slide-track");

    fireEvent.pointerDown(track, { clientX: 0, pointerId: 1 });
    fireEvent.pointerMove(track, { clientX: 100, pointerId: 1 });

    const knob = screen.getByTestId("slide-knob");
    expect(knob).toHaveAttribute("data-motion", "none");
    expect(knob).not.toHaveAttribute("data-knob", "0");
  });

  it("abandons the drag on pointercancel", () => {
    const { onConfirm } = renderDoor(at("pending"));
    const track = screen.getByTestId("slide-track");

    fireEvent.pointerDown(track, { clientX: 0, pointerId: 1 });
    fireEvent.pointerMove(track, { clientX: MAX, pointerId: 1 });
    fireEvent.pointerCancel(track, { clientX: MAX, pointerId: 1 });

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByTestId("slide-knob")).toHaveAttribute("data-knob", "0");
  });

  it("refuses to confirm while offline, and says the fuse is still running", () => {
    const { onConfirm } = renderDoor(at("pending"), { online: false });

    drag(MAX);

    expect(onConfirm).not.toHaveBeenCalled();
    expect(
      screen.getByText("Cannot confirm while offline; the fuse is still running on the server."),
    ).toBeInTheDocument();
  });
});

describe("DoorLayer — after the answer", () => {
  it("says confirmed and queued, never applied", () => {
    renderDoor(at("queued", { confirmedAt: "2026-09-07T07:42:00Z" }));

    expect(screen.getByText("Confirmed · queued")).toBeInTheDocument();
    expect(
      screen.getByText("Sent to Home Assistant. Waiting for it to report (request a91f)."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("slide-track")).toBeNull();
    expect(screen.getByRole("button", { name: "Back to the room" })).toBeInTheDocument();
  });

  it("says applied only once the stream reported it, with what it reported", () => {
    renderDoor(
      at("applied", {
        confirmedAt: "2026-09-07T07:42:00Z",
        appliedAt: "2026-09-07T07:42:10Z",
        result: {
          request_id: "a91f3c2e",
          tool_name: "home.lock_unlock",
          status: "success",
        },
      }),
    );

    expect(screen.getByText("Applied")).toBeInTheDocument();
    expect(
      screen.getByText(
        `home.lock_unlock reported success at ${hhmm("2026-09-07T07:42:10Z")}.`,
      ),
    ).toBeInTheDocument();
  });

  it("says what did not happen, and how to ask again", () => {
    renderDoor(at("expired"));

    expect(screen.getByText("Expired")).toBeInTheDocument();
    expect(screen.getByText("lapsed")).toBeInTheDocument();
    expect(
      screen.getByText(
        new RegExp(
          "^The five minutes ran out at \\d{2}:\\d{2}\\. Nothing was done\\. Ask again to get a fresh one\\.$",
        ),
      ),
    ).toBeInTheDocument();
  });

  it("spells a different TTL out too", () => {
    renderDoor({ action: { ...pendingActionFixture, ttl_seconds: 600 }, phase: "expired" });
    expect(screen.getByText(/^The ten minutes ran out/)).toBeInTheDocument();
  });

  it("falls back to the number for an unusual TTL", () => {
    renderDoor({ action: { ...pendingActionFixture, ttl_seconds: 45 * 60 }, phase: "expired" });
    expect(screen.getByText(/^The 45 minutes ran out/)).toBeInTheDocument();
  });

  it("says when something else got there first", () => {
    renderDoor(at("answered"));
    expect(screen.getByText("Answered")).toBeInTheDocument();
    expect(screen.getByText("answered elsewhere")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Already answered elsewhere; the house no longer holds this request. Nothing further was sent.",
      ),
    ).toBeInTheDocument();
  });
});

describe("DoorLayer — leaving", () => {
  it("leaves it on the chevron", async () => {
    const user = userEvent.setup();
    const { onClose } = renderDoor(at("pending"));

    await user.click(screen.getByRole("button", { name: "Leave it" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("goes back to the room once answered", async () => {
    const user = userEvent.setup();
    const { onClose } = renderDoor(at("expired"));

    await user.click(screen.getByRole("button", { name: "Back to the room" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders nothing at all when there is no action", () => {
    render(
      <DoorLayer
        tracked={null}
        open={false}
        online
        now={T0742}
        onClose={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
```

- [ ] **Step 5: Run it to verify it fails**

Run: `npm test -- src/door/DoorLayer.test.tsx`
Expected: FAIL — `Failed to resolve import "./DoorLayer"`.

- [ ] **Step 6: Write `web/src/door/SlideToConfirm.tsx`**

Complete file:

```tsx
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { CONFIRM_RATIO, hintOpacity, slideKnob } from "@/lib/slide";

/** Knob 56 px at a 4 px inset — the travel is the track minus both. */
const KNOB_INSET = 64;
/** The one place the handoff's `settle` curve is used: when the knob lands. */
const SETTLE_MS = 600;
const SNAP_MS = 380;

export interface SlideToConfirmProps {
  hint: string;
  disabled: boolean;
  onConfirm: () => void;
}

export function SlideToConfirm({ hint, disabled, onConfirm }: SlideToConfirmProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const startXRef = useRef(0);
  const [max, setMax] = useState(0);
  const [knob, setKnob] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [settling, setSettling] = useState(false);

  function measure(): number {
    const width = trackRef.current?.clientWidth ?? 0;
    const travel = Math.max(0, width - KNOB_INSET);
    setMax(travel);
    return travel;
  }

  // Measure once so the hint's opacity is right before anything is touched; the
  // width is measured again on every press, because the keyboard and rotation
  // both change it.
  useEffect(() => {
    measure();
  }, []);

  function down(event: ReactPointerEvent<HTMLDivElement>): void {
    if (disabled) return;
    measure();
    startXRef.current = event.clientX;
    trackRef.current?.setPointerCapture(event.pointerId);
    setSettling(false);
    setDragging(true);
    setKnob(0);
  }

  function move(event: ReactPointerEvent<HTMLDivElement>): void {
    if (!dragging) return;
    setKnob(slideKnob(event.clientX - startXRef.current, max));
  }

  function up(event: ReactPointerEvent<HTMLDivElement>): void {
    if (!dragging) return;
    trackRef.current?.releasePointerCapture(event.pointerId);
    setDragging(false);

    const travelled = slideKnob(event.clientX - startXRef.current, max);
    if (max > 0 && travelled >= max * CONFIRM_RATIO) {
      setSettling(true);
      setKnob(max);
      onConfirm();
      return;
    }
    setKnob(0);
  }

  function cancel(event: ReactPointerEvent<HTMLDivElement>): void {
    if (!dragging) return;
    trackRef.current?.releasePointerCapture(event.pointerId);
    setDragging(false);
    setKnob(0);
  }

  return (
    <div
      ref={trackRef}
      data-testid="slide-track"
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={cancel}
      className="relative h-16 overflow-hidden rounded-[32px]"
      style={{
        background: "var(--ring)",
        // Without this, iOS treats the drag as a page scroll and the knob never moves.
        touchAction: "none",
        userSelect: "none",
        WebkitUserSelect: "none",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <div
        className="absolute inset-0 flex items-center justify-center pl-14 text-[15px]"
        style={{
          color: "var(--paper-muted)",
          opacity: hintOpacity(knob, max),
          transition: "opacity .15s",
        }}
      >
        {hint}
      </div>

      <div
        data-testid="slide-knob"
        data-knob={String(Math.round(knob))}
        data-motion={dragging ? "none" : settling ? "settle" : "snap"}
        className="absolute top-1 left-1 flex h-14 w-14 items-center justify-center rounded-[28px]"
        style={{
          background: "var(--accent)",
          transform: `translateX(${knob}px)`,
          transition: dragging
            ? "none"
            : settling
              ? `transform ${SETTLE_MS}ms var(--ease-settle)`
              : `transform ${SNAP_MS}ms var(--ease-rise)`,
        }}
      >
        <span
          aria-hidden="true"
          className="block h-2.5 w-2.5 border-t-2 border-r-2"
          style={{ borderColor: "var(--ink)", transform: "rotate(45deg)" }}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Write `web/src/door/DoorLayer.tsx`**

Complete file:

```tsx
import { useState } from "react";
import { fuseRemaining, type TrackedAction } from "@/lib/actions";
import { hhmm, humaniseTool, mmss, rawCall, shortId } from "@/lib/format";
import { FuseRing } from "@/door/FuseRing";
import { SlideToConfirm } from "@/door/SlideToConfirm";
import { Layer } from "@/shell/Layer";

/** Under this the arc turns to paper. */
const DANGER_SECONDS = 30;

/**
 * "The five minutes ran out" reads better than "The 5 minutes ran out", and the
 * TTL is a server setting that may not be 300 s. Spelled out to ten, numeric above.
 */
const MINUTE_WORDS: Record<number, string> = {
  1: "one",
  2: "two",
  3: "three",
  4: "four",
  5: "five",
  6: "six",
  7: "seven",
  8: "eight",
  9: "nine",
  10: "ten",
};

function minutesWord(ttlSeconds: number): string {
  const minutes = Math.round(ttlSeconds / 60);
  return MINUTE_WORDS[minutes] ?? String(minutes);
}

const PILL: Record<string, { word: string; dot: string }> = {
  queued: { word: "Confirmed · queued", dot: "var(--accent)" },
  applied: { word: "Applied", dot: "var(--green)" },
  expired: { word: "Expired", dot: "var(--paper-muted)" },
  answered: { word: "Answered", dot: "var(--paper-muted)" },
};

function subLabel(tracked: TrackedAction): string {
  if (tracked.phase === "pending") return "until it lapses";
  if (tracked.phase === "expired") return "lapsed";
  if (tracked.phase === "answered") return "answered elsewhere";
  return tracked.confirmedAt ? `confirmed ${hhmm(tracked.confirmedAt)}` : "confirmed";
}

function footLine(tracked: TrackedAction, online: boolean): string {
  const { action, phase } = tracked;
  switch (phase) {
    case "pending":
      return online
        ? "Approval only; the lock itself reports back separately. Releasing before the end snaps back."
        : "Cannot confirm while offline; the fuse is still running on the server.";
    case "queued":
      return `Sent to Home Assistant. Waiting for it to report (request ${shortId(action.request_id)}).`;
    case "applied":
      return `${action.tool_name} reported ${tracked.result?.status ?? "success"} at ${hhmm(
        tracked.appliedAt ?? action.expires_at,
      )}.`;
    case "expired":
      return `The ${minutesWord(action.ttl_seconds)} minutes ran out at ${hhmm(
        action.expires_at,
      )}. Nothing was done. Ask again to get a fresh one.`;
    default:
      return "Already answered elsewhere; the house no longer holds this request. Nothing further was sent.";
  }
}

export interface DoorLayerProps {
  tracked: TrackedAction | null;
  open: boolean;
  online: boolean;
  /** Epoch ms from `useDoor().now`. */
  now: number;
  onClose: () => void;
  onConfirm: (id: string) => void;
}

export function DoorLayer({ tracked, open, online, now, onClose, onConfirm }: DoorLayerProps) {
  // Hold the last action through the 420 ms leave animation: `close()` clears
  // the current one immediately, and an empty ink panel sliding away is worse
  // than the one you just dismissed sliding away.
  // Adjusted during render rather than in an effect: the copy must never lag
  // the prop by a frame.
  const [shown, setShown] = useState<TrackedAction | null>(tracked);
  if (tracked && tracked !== shown) setShown(tracked);

  const item = tracked ?? shown;
  if (!item) return null;

  const { action, phase } = item;
  const remaining = fuseRemaining(action, now);
  const percent = action.ttl_seconds > 0 ? Math.max(0, Math.min(100, (remaining / action.ttl_seconds) * 100)) : 0;
  const reason =
    action.reason ?? `Alfred wants to run '${action.tool_name}' on ${action.target_service}.`;
  const pill = PILL[phase];

  return (
    <Layer open={open} label="Critical approval" durationMs={420}>
      <div
        className="flex flex-1 flex-col overflow-hidden"
        style={{ background: "var(--ink)", color: "var(--paper)" }}
      >
        <div className="flex items-center justify-between px-6 pt-16">
          <button
            type="button"
            onClick={onClose}
            className="-ml-1 flex h-11 items-center gap-1.5 border-0 bg-transparent px-1 text-[15px] font-normal"
            style={{ color: "var(--paper-muted)" }}
          >
            <span
              aria-hidden="true"
              className="block h-2.5 w-2.5 border-b-[1.5px] border-l-[1.5px] border-current"
              style={{ transform: "rotate(45deg)" }}
            />
            Leave it
          </button>
          <div className="font-mono text-[11px]" style={{ color: "var(--paper-muted)" }}>
            CRITICAL · {shortId(action.request_id)}
          </div>
        </div>

        <div className="flex flex-1 flex-col items-center justify-center gap-7 px-6 text-center">
          <FuseRing percent={percent} size={168} danger={remaining <= DANGER_SECONDS}>
            <div className="flex flex-col items-center gap-0.5">
              <div className="t-fuse">{mmss(remaining)}</div>
              <div className="font-mono text-[11px]" style={{ color: "var(--paper-muted)" }}>
                {subLabel(item)}
              </div>
            </div>
          </FuseRing>

          <div className="flex flex-col items-center gap-2.5">
            <h2 className="t-gate">{humaniseTool(action.tool_name)}</h2>
            <p
              className="max-w-[300px] text-[15px] leading-[1.45]"
              style={{ color: "var(--paper-muted)" }}
            >
              {reason}
            </p>
            <div
              className="rounded-lg border px-3 py-2 font-mono text-[11.5px] leading-[1.5] break-all"
              style={{ borderColor: "var(--ring)", color: "var(--paper-muted)" }}
            >
              {rawCall(action.tool_name, action.parameters)}
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-3 px-5 pb-10">
          {phase === "pending" ? (
            <SlideToConfirm
              hint="Slide to confirm"
              disabled={!online}
              onConfirm={() => onConfirm(action.request_id)}
            />
          ) : (
            <div
              className="flex h-16 items-center justify-center gap-2.5 rounded-[32px] border-[1.5px] text-[16px] font-medium"
              style={{ borderColor: "var(--ring)" }}
            >
              <span
                aria-hidden="true"
                className="h-2 w-2 rounded-full"
                style={{ background: pill.dot }}
              />
              {pill.word}
            </div>
          )}

          <div
            className="text-center font-mono text-[11px] leading-[1.5]"
            style={{ color: "var(--paper-muted)" }}
          >
            {footLine(item, online)}
          </div>

          {phase === "pending" ? null : (
            <button
              type="button"
              onClick={onClose}
              className="h-[50px] rounded-[25px] border-0 bg-transparent text-[15px] font-medium"
              style={{ color: "var(--paper)" }}
            >
              Back to the room
            </button>
          )}
        </div>
      </div>
    </Layer>
  );
}
```

- [ ] **Step 8: Run the Door test and the suite**

Run: `npm test -- src/door/DoorLayer.test.tsx`
Expected: `Test Files  1 passed (1)`, 19 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  38 passed (38)`, no eslint output, `✓ built in …`.

- [ ] **Step 9: Commit**

```bash
git add web/src/lib/slide.ts web/src/lib/slide.test.ts web/src/door/SlideToConfirm.tsx web/src/door/DoorLayer.tsx web/src/door/DoorLayer.test.tsx
git commit -m "feat(web): the Door — fuse, reason, raw call and slide to confirm"
```

---

### Task 27: `/actions/:id` — the notification tap

Spec §6.3 and the handoff's "Closed · tapped late": a push about an approval must land on *that approval*, and it must land somewhere honest when the fuse has already run out. Phase 5 sends the push; the URL it will open has to work now, because the tombstone path is the interesting half and is testable today.

Three outcomes, one route:

| `GET /api/actions/{id}` | What happens |
|---|---|
| 200 | Track it, open the Door over the Room |
| 404 | A tombstone in the thread — `already answered`, with the tool's name if the notifications history remembers it |
| anything else | Nothing; the Room is still the Room |

In every case the URL is replaced with `/` so a pull-to-refresh does not reopen a decision the user has already answered.

The hook reads the id from `useLocation()` rather than `useParams()`, so it can be called from the Room itself — which both routes render — and therefore keeps its tombstone across the `navigate` instead of unmounting with the route.

**Files:**
- Create: `web/src/door/useActionRoute.ts`, `web/src/door/useActionRoute.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `web/src/door/useActionRoute.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { pendingActionFixture } from "@/test/fixtures";
import { useActionRoute } from "./useActionRoute";

const { arrivedMock, openActionMock } = vi.hoisted(() => ({
  arrivedMock: vi.fn(),
  openActionMock: vi.fn(),
}));

vi.mock("@/door/DoorProvider", () => ({
  useDoor: () => ({ arrived: arrivedMock, openAction: openActionMock }),
}));

let status = 200;
const calls: string[] = [];

function Probe({ titles = {} }: { titles?: Record<string, string> }) {
  const { tombstone } = useActionRoute(titles);
  const location = useLocation();
  return (
    <div>
      <span data-testid="path">{location.pathname}</span>
      <span data-testid="tomb">
        {tombstone && tombstone.kind === "tombstone"
          ? `${tombstone.title}|${tombstone.meta}`
          : "none"}
      </span>
    </div>
  );
}

function renderAt(path: string, titles?: Record<string, string>) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Probe titles={titles} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  status = 200;
  calls.length = 0;
  arrivedMock.mockClear();
  openActionMock.mockClear();
  // Inside the fixture's fuse, as DoorProvider.test pins it.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-09-07T07:42:00Z"));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response(
        status === 200
          ? JSON.stringify(pendingActionFixture)
          : '{"detail":"Pending action not found or expired"}',
        { status },
      );
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useActionRoute", () => {
  it("opens the approval a notification named", async () => {
    renderAt("/actions/a91f3c2e");

    await waitFor(() => expect(arrivedMock).toHaveBeenCalledWith(pendingActionFixture));
    expect(openActionMock).toHaveBeenCalledWith("a91f3c2e");
    expect(calls).toEqual(["/api/actions/a91f3c2e"]);
  });

  it("replaces the URL so a refresh does not reopen it", async () => {
    renderAt("/actions/a91f3c2e");
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/"));
    expect(screen.getByTestId("tomb")).toHaveTextContent("none");
  });

  it("lands on a tombstone when the house has already forgotten it", async () => {
    status = 404;
    renderAt("/actions/a91f3c2e", { a91f3c2e: "Lock unlock" });

    await waitFor(() =>
      expect(screen.getByTestId("tomb")).toHaveTextContent(
        "Lock unlock|already answered · nothing was done",
      ),
    );
    expect(openActionMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("path")).toHaveTextContent("/");
  });

  it("names an unremembered approval by its short id", async () => {
    status = 404;
    renderAt("/actions/a91f3c2e");

    await waitFor(() =>
      expect(screen.getByTestId("tomb")).toHaveTextContent("Action a91f|already answered"),
    );
  });

  it("keeps the tombstone once the history learns the name", async () => {
    status = 404;
    const { rerender } = render(
      <MemoryRouter initialEntries={["/actions/a91f3c2e"]}>
        <Probe titles={{}} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("tomb")).toHaveTextContent("Action a91f"));

    rerender(
      <MemoryRouter initialEntries={["/actions/a91f3c2e"]}>
        <Probe titles={{ a91f3c2e: "Lock unlock" }} />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByTestId("tomb")).toHaveTextContent("Lock unlock"));
    // Still one read: the id was handled the first time.
    expect(calls).toHaveLength(1);
  });

  it("does nothing at all in the Room", async () => {
    renderAt("/");
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/"));
    expect(calls).toHaveLength(0);
    expect(arrivedMock).not.toHaveBeenCalled();
  });

  it("ignores a path that is not a single action id", async () => {
    renderAt("/actions/a91f/extra");
    await waitFor(() => expect(screen.getByTestId("path")).toBeInTheDocument());
    expect(calls).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/door/useActionRoute.test.tsx`
Expected: FAIL — `Failed to resolve import "./useActionRoute"`.

- [ ] **Step 3: Write `web/src/door/useActionRoute.ts`**

Complete file:

```ts
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useDoor } from "@/door/DoorProvider";
import { fetchAction } from "@/lib/actions";
import { shortId } from "@/lib/format";
import type { TimelineItem } from "@/lib/history";

const ACTION_PATH = /^\/actions\/([^/]+)$/;

export interface ActionRouteValue {
  /** A tombstone for an id the house no longer holds — merge it into the thread. */
  tombstone: TimelineItem | null;
}

/**
 * Handle `/actions/:id`, the URL a push notification opens.
 *
 * The id comes from the location rather than `useParams`, so the hook can live in
 * the Room — which both routes render — and keep its tombstone across the
 * `navigate("/")` instead of unmounting with the route it was called from.
 *
 * @param titles `pendingActionTitles(history)`: what the notifications page
 *   remembers each approval was about, so a lapsed one can be named.
 */
export function useActionRoute(titles: Record<string, string>): ActionRouteValue {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { arrived, openAction } = useDoor();
  const [missing, setMissing] = useState<{ id: string; at: string } | null>(null);
  const handledRef = useRef<string | null>(null);

  const id = ACTION_PATH.exec(pathname)?.[1] ?? null;

  useEffect(() => {
    // No cleanup, and no cancellation flag: StrictMode's setup→cleanup→setup
    // would otherwise abort the only read this route ever makes. The ref makes
    // the second pass a no-op instead.
    if (!id || handledRef.current === id) return;
    handledRef.current = id;

    void fetchAction(id)
      .then((action) => {
        arrived(action);
        openAction(id);
      })
      .catch(() => {
        // 404, or the house is unreachable. Either way there is nothing to
        // approve; the thread says so rather than opening an empty Door.
        setMissing({ id, at: new Date().toISOString() });
      })
      .finally(() => {
        // Replace, never push: a pull-to-refresh must not reopen a decision that
        // has already been answered.
        navigate("/", { replace: true });
      });
  }, [id, arrived, openAction, navigate]);

  const tombstone = useMemo<TimelineItem | null>(() => {
    if (!missing) return null;
    return {
      kind: "tombstone",
      id: `tomb:missing:${missing.id}`,
      at: missing.at,
      title: titles[missing.id] ?? `Action ${shortId(missing.id)}`,
      meta: "already answered · nothing was done",
    };
  }, [missing, titles]);

  return { tombstone };
}
```

- [ ] **Step 4: Run the test and the suite**

Run: `npm test -- src/door/useActionRoute.test.tsx`
Expected: `Test Files  1 passed (1)`, 7 tests.

Run: `npm test && npm run lint && npm run build`
Expected: `Test Files  39 passed (39)`, no eslint output, `✓ built in …`.

- [ ] **Step 5: Commit**

```bash
git add web/src/door/useActionRoute.ts web/src/door/useActionRoute.test.tsx
git commit -m "feat(web): land a notification tap on the approval it names"
```

---

### Task 28: Compose the Room, and finish phase 1

Everything exists; this assembles it, deletes 1a's placeholder Room, unlocks the audio context on the first tap, rewrites `web/README.md` for the client that now lives there, and writes the manual iOS checklist that spec §7 requires ("every one of the twelve iOS constraints in §4 gets a test or a documented manual QA step").

**Files:**
- Rewrite: `web/src/room/Room.tsx`, `web/src/App.tsx`, `web/src/main.tsx`, `web/src/App.test.tsx`, `web/README.md`
- Create: `docs/superpowers/qa/2026-09-07-pwa-phase1-ios-checklist.md`

- [ ] **Step 1: Rewrite `web/src/App.test.tsx` for the Room that now exists**

1a's version asserted a placeholder that said `Listening, sir.` unconditionally. The real Room reads its headline from the socket, so the fake must be able to come online, and the fetch stub must answer everything the Room asks for.

Complete file:

```tsx
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  notificationsPage,
  overviewFixture,
  pendingActionFixture,
  reflexObservationsPage,
  userRequestsPage,
  userResponsesPage,
} from "@/test/fixtures";
import App from "./App";

vi.mock("@/lib/chat-socket", () => ({
  ChatSocket: class {
    onstatus: (status: string) => void = () => {};
    connect() {
      // Report online from connect(), which ConnectionProvider calls after it has
      // assigned onstatus — the Room's headline depends on it.
      this.onstatus("online");
    }
    close() {}
    sendText() {
      return true;
    }
    sendAudio() {
      return true;
    }
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

const ROUTES: Record<string, unknown> = {
  "/api/auth/status": { registered: true, authenticated: true },
  "/api/admin/overview": overviewFixture,
  "/api/admin/streams/user_requests?count=50": userRequestsPage,
  "/api/admin/streams/user_responses?count=50": userResponsesPage,
  "/api/admin/streams/reflex_observations?count=50": reflexObservationsPage,
  "/api/admin/streams/notifications?count=50": notificationsPage,
  "/api/actions/pending": { actions: [] },
};

beforeEach(() => {
  // Four minutes before the fixture's fuse lapses, so the deep link opens a
  // live Door and not the one that expired the morning the fixture was written.
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-09-07T07:42:00Z"));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url in ROUTES) return new Response(JSON.stringify(ROUTES[url]), { status: 200 });
      if (url.startsWith("/api/actions/"))
        return new Response(JSON.stringify(pendingActionFixture), { status: 200 });
      return new Response("{}", { status: 200 });
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  window.history.pushState({}, "", "/");
});

describe("App", () => {
  it("boots an authenticated device into a listening Room", async () => {
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Listening, sir." })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Switch theme" })).toBeInTheDocument();
    expect(await screen.findByText(/cloud 1.42 \/ 5.00/)).toBeInTheDocument();
  });

  it("shows the thread the four streams describe", async () => {
    render(<App />);

    expect(
      await screen.findByText("What have I got tomorrow morning?"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "The dentist at nine, sir. I'd leave by twenty to; there's rain forecast from eight.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Your parcel arrived")).toBeInTheDocument();
    // The confirmation notification belongs to the Door, not the thread.
    expect(screen.queryByText("Confirmation required")).toBeNull();
  });

  it("offers the composer and the microphone", async () => {
    render(<App />);
    expect(await screen.findByPlaceholderText("Ask or tell Alfred")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hold to talk" })).toBeInTheDocument();
  });

  it("applies a theme to the document as it mounts", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "Listening, sir." });
    expect(["dark", "light"]).toContain(document.documentElement.dataset.theme);
  });

  it("opens the Door on the action deep link", async () => {
    window.history.pushState({}, "", "/actions/a91f3c2e");
    render(<App />);

    expect(await screen.findByRole("dialog", { name: "Critical approval" })).toBeInTheDocument();
    expect(screen.getByText("CRITICAL · a91f")).toBeInTheDocument();
    // The fuse is still running: the clock is pinned inside it (see beforeEach).
    expect(screen.getByText("until it lapses")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- src/App.test.tsx`
Expected: FAIL — the placeholder Room renders no timeline, no composer and no Door, so four of the five tests fail. The theme test still passes.

- [ ] **Step 3: Rewrite `web/src/room/Room.tsx`**

Complete file (this replaces 1a's frame entirely):

```tsx
import { useEffect, useMemo, useState } from "react";
import { DoorBanner } from "@/door/DoorBanner";
import { DoorLayer } from "@/door/DoorLayer";
import { useDoor } from "@/door/DoorProvider";
import { useActionRoute } from "@/door/useActionRoute";
import { tombstoneItems } from "@/lib/actions";
import { greetingFor, pickHeadline } from "@/lib/headline";
import { pendingActionTitles, toTimelineItems } from "@/lib/history";
import { onVisible } from "@/lib/lifecycle";
import { PresenceSignal } from "@/lib/presence-signal";
import { Composer } from "@/room/Composer";
import { DndRow } from "@/room/DndRow";
import { Headline } from "@/room/Headline";
import { HoldToTalk } from "@/room/HoldToTalk";
import { OfflineNote } from "@/room/OfflineNote";
import { PresenceField } from "@/room/PresenceField";
import { StatusLine } from "@/room/StatusLine";
import { Timeline } from "@/room/Timeline";
import { isFirstRun, useOverview } from "@/room/useOverview";
import { useRoom } from "@/room/useRoom";
import { useRoomHistory } from "@/room/useRoomHistory";
import { HeldBackSheet } from "@/sheets/HeldBackSheet";
import { useConnection } from "@/shell/ConnectionProvider";
import { ThemeToggle } from "@/shell/ThemeToggle";

export function Room() {
  const { online, chatStatus, lastTrueAt } = useConnection();
  const { data: overview } = useOverview();
  const { data: history } = useRoomHistory();
  const door = useDoor();

  // One signal for the app's lifetime: the field reads it every frame and
  // hold-to-talk writes to it, so it must not be rebuilt on a render.
  const [signal] = useState(() => new PresenceSignal());
  const [holding, setHolding] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);

  // Read once, refreshed when the app returns — a PWA left open overnight must
  // not still be saying "Good evening".
  const [hour, setHour] = useState(() => new Date().getHours());
  useEffect(() => onVisible(() => setHour(new Date().getHours())), []);

  const historyItems = useMemo(() => (history ? toTimelineItems(history) : []), [history]);
  const titles = useMemo(() => pendingActionTitles(history), [history]);
  const { tombstone } = useActionRoute(titles);

  const tombstones = useMemo(() => {
    const items = tombstoneItems(door.actions);
    return tombstone ? [...items, tombstone] : items;
  }, [door.actions, tombstone]);

  const room = useRoom({ history: historyItems, tombstones });

  useEffect(() => {
    signal.setThinking(room.thinking);
  }, [signal, room.thinking]);

  const firstRun = isFirstRun(overview);
  // "connecting" is the first-ever attempt and "reconnecting" a later one; both
  // read as still trying, which is truer than "Unreachable." for the 200 ms
  // before the socket opens.
  const reconnecting = chatStatus === "connecting" || chatStatus === "reconnecting";
  const dnd = overview?.dnd ?? { active: false };

  const headline = pickHeadline({
    online,
    reconnecting,
    firstRun,
    dnd,
    holding,
    busy: room.thinking,
    hour,
  });

  const banner = door.pending[0] ?? null;

  return (
    <main
      className="relative flex flex-1 flex-col overflow-hidden"
      style={{ background: "var(--bg)" }}
    >
      <PresenceField signal={signal} offline={!online} />

      <header className="relative z-[1] flex flex-col gap-1 px-6 pt-[72px]">
        <div className="flex items-end justify-between gap-3">
          <Headline text={headline} />
          <ThemeToggle />
        </div>
        <StatusLine overview={overview} online={online} lastTrueAt={lastTrueAt} />
        {online ? null : <OfflineNote reconnecting={reconnecting} lastTrueAt={lastTrueAt} />}
        {dnd.active ? (
          <DndRow
            until={dnd.until}
            heldCount={overview?.counts.deferred ?? 0}
            onOpen={() => setSheetOpen(true)}
          />
        ) : null}
      </header>

      <Timeline
        items={room.items}
        firstDayGreeting={firstRun && room.items.length === 0 ? greetingFor(hour) : null}
      />

      {banner ? (
        <DoorBanner
          tracked={banner}
          now={door.now}
          onOpen={() => door.openAction(banner.action.request_id)}
        />
      ) : null}

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
      />

      <HeldBackSheet open={sheetOpen} onClose={() => setSheetOpen(false)} />

      <DoorLayer
        tracked={door.current}
        open={door.open}
        online={online}
        now={door.now}
        onClose={door.close}
        onConfirm={door.confirm}
      />
    </main>
  );
}
```

- [ ] **Step 4: Put `DoorProvider` in `web/src/App.tsx`**

The Door only exists for a signed-in device, so its provider goes inside `AuthGate`, above the routes. Complete file:

```tsx
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { DoorProvider } from "@/door/DoorProvider";
import { AuthGate } from "@/gates/AuthGate";
import { Room } from "@/room/Room";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { QueryProvider } from "@/shell/QueryProvider";
import { ThemeProvider } from "@/shell/ThemeProvider";

/**
 * Provider order is load-bearing: ConnectionProvider and AuthGate both read the
 * query client, AuthGate reads the connection for the Denied gate's stamp, and
 * DoorProvider sits inside AuthGate so nothing reads `/api/actions/pending`
 * before there is a session to read it with.
 *
 * There is one screen. `/actions/:id` is the Room as well — a notification tap
 * must land on the approval it names (spec §6.3), and `useActionRoute` opens the
 * Door over it.
 */
export default function App() {
  return (
    <QueryProvider>
      <ThemeProvider>
        <ConnectionProvider>
          <BrowserRouter>
            <AuthGate>
              <DoorProvider>
                <Routes>
                  <Route path="/" element={<Room />} />
                  <Route path="/actions/:id" element={<Room />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </DoorProvider>
            </AuthGate>
          </BrowserRouter>
        </ConnectionProvider>
      </ThemeProvider>
    </QueryProvider>
  );
}
```

- [ ] **Step 5: Unlock the audio context in `web/src/main.tsx`**

Complete file:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import { installAudioUnlock } from "@/lib/audio";
import { installViewportVars } from "@/lib/viewport";

// Before the first paint: #root is sized from --app-height and the composer's
// padding comes from --keyboard-inset.
installViewportVars();

// Before the first tap: iOS refuses audio until a gesture has resumed a context,
// and will not retroactively allow a sound requested before then. Installing the
// listener here means the very first spoken reply is audible (constraint §4.7).
installAudioUnlock();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 6: Run the composition test and the whole suite**

Run: `npm test -- src/App.test.tsx`
Expected: `Test Files  1 passed (1)`, 5 tests.

Run: `npm test`
Expected: `Test Files  39 passed (39)`, zero failures. jsdom prints
`Not implemented: HTMLCanvasElement.prototype.getContext` wherever the Room is
rendered without a stubbed canvas — `PresenceField` catches it and draws nothing.
That line is noise, not a failure.

Run: `npm run lint`
Expected: no output.

Run: `npm run build`
Expected: `tsc -b` silent, `✓ built in …`.

- [ ] **Step 7: Commit the client**

```bash
git add web/src/room/Room.tsx web/src/App.tsx web/src/App.test.tsx web/src/main.tsx
git commit -m "feat(web): compose the Room — presence, thread, composer and Door"
```

- [ ] **Step 8: Rewrite `web/README.md`**

Complete file:

```markdown
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

The type check lives in `build`, not `lint`. CI runs all four, then serves the
built `dist/` to `tests/core/channels/test_spa_ci.py`.

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
- Imports use the `@/` alias, except inside `src/lib/`, where siblings are relative.
- The status vocabulary is closed: `queued`, `applied`, `last true HH:MM`,
  `unknown since HH:MM`, `expired · not done`, `takes effect within 60 s`. Always
  mono, always lower case. Do not invent new words for system state.

## What phase 1 covers

Shell, theme and viewport; the four identity gates; the Room (presence field,
headline, status line, offline note, DND row and the held-back sheet, the merged
timeline, composer, hold-to-talk, notifications); and the Door (banner, fuse,
slide-to-confirm, the five phases, tombstones, and the `/actions/:id` deep link).

## What it does not

- **The Workshop** (Activity, Memory, Triggers, System) — phase 2 and 3. There is
  no handle, no `why?` button and no causal thread yet.
- **Install, standalone and Reach gates, the service worker, icons and Web Push**
  — phases 4 and 5. `public/manifest.json` is untouched.
- **Desktop** — phase 6.

## Things worth knowing before you change something

- `--app-height` and `--keyboard-inset` are written by `installViewportVars()`
  from `visualViewport`. `100vh` is wrong in Safari; do not reach for it.
- All audio plays through one `AudioContext` unlocked by the first tap
  (`lib/audio.ts`). A fresh `new Audio()` is silently dropped on iOS until then.
- `MediaRecorder` must negotiate `audio/mp4` → `audio/aac` → default. Safari has
  no WebM and throws from the constructor if you ask for it.
- A 200 from `POST /api/actions/{id}/confirm` means **queued**. `applied` comes
  only from the `home_action_results` telemetry stream.
- 401 and 403 raise a gate over whatever is on screen; they never redirect. The
  last-known Room stays visible behind them.
```

- [ ] **Step 9: Write the iOS QA checklist**

Create `docs/superpowers/qa/2026-09-07-pwa-phase1-ios-checklist.md`:

```markdown
# PWA phase 1 — manual iOS checklist

Spec §7: "Every one of the twelve iOS constraints in §4 gets a test or a
documented manual QA step." The automated half is in `web/src`; this is the half
that needs a real iPhone, Safari, and a house that is actually running.

Run against the deployed build at `https://alfred.example.com` on a phone that has
never signed in. Record the device, iOS version and date at the bottom.

## Before you start

- [ ] `npm run build` output is what is deployed (`git log -1` on the deploy host)
- [ ] The phone is on the house network for registration (§3.1 gates it)
- [ ] A pending critical action can be produced on demand (ask Alfred to unlock
      something, or publish one by hand)

## §4.1 — secure context

- [ ] The address bar shows a padlock; no mixed-content warning in the console
- [ ] Face ID registration is offered at all (WebAuthn is refused outside HTTPS)

## §4.2 — home-screen icon

- [ ] **Deferred to phase 4.** `manifest.json` still ships SVG only, and no
      `apple-touch-icon` PNG exists yet. Expect a screenshot-style icon if you
      add it to the home screen; that is not a phase 1 defect.

## §4.3 — notch, island and home indicator

- [ ] The headline sits below the Dynamic Island, never under it
- [ ] The composer sits above the home indicator with a clear gap
- [ ] Rotate to landscape: nothing is under the rounded corners

## §4.4 — 100vh is wrong

- [ ] Scroll the timeline down and back: the composer never leaves the screen
- [ ] With Safari's toolbar collapsed, the Room still fills the viewport exactly
- [ ] Rotate twice quickly: no strip of background appears at the bottom

## §4.5 — the software keyboard

- [ ] Tap the field: the composer rises with the keyboard, it is not covered
- [ ] The timeline is still scrollable with the keyboard up
- [ ] Dismiss the keyboard: the composer returns without a jump
- [ ] The home-indicator gap does **not** double up while the keyboard is open

## §4.6 — rubber-band scroll

- [ ] Drag down on the header: the page does not bounce as a whole
- [ ] Drag past the top of the timeline: only the timeline bounces

## §4.7 — audio needs a gesture

- [ ] Cold-launch the app, tap once anywhere, then send a message: the spoken
      reply is **audible on the first reply**, not only the second
- [ ] Background the app for a minute, return, trigger an urgent notification:
      the audio still plays

## §4.8 — Safari's recorder

- [ ] Hold the microphone, speak, release: the dashed bubble appears and is
      replaced by the transcript
- [ ] The console shows no `NotSupportedError` from `MediaRecorder`
- [ ] Release inside a second: nothing is sent and the caption clears

## §4.9 / §4.9b — Web Push and install

- [ ] **Deferred to phase 5.** There is no Reach gate, no permission prompt and
      no service worker in phase 1. Confirm only that nothing *offers* them.

## §4.10 — iOS kills suspended PWAs

- [ ] Send a message, background the app for five minutes, return: the thread is
      intact and the status line's clock has moved
- [ ] While backgrounded, have the house produce an act (a reflex action): it is
      present after returning, without a manual refresh
- [ ] Turn off Wi-Fi and mobile data: the headline reads `Unreachable.`, the
      offline note carries a real `last true HH:MM`, and a sent message shows
      `not sent · will retry when connected`
- [ ] Turn the network back on: the queued message sends itself, in order

## §4.11 — no haptics

- [ ] The slide-to-confirm gives no vibration and does not need one: the knob
      resists at the start and only confirms past ~85% of the track

## §4.12 — no browser chrome

- [ ] The Held-back sheet closes on both `Done` and the scrim
- [ ] The Door closes on `‹ Leave it`, and on `Back to the room` once answered
- [ ] The Expired gate can be dismissed by signing in; the Denied gate by
      `Back to the room`
- [ ] No screen is reachable that has no way out

## The Door, end to end

- [ ] Produce a pending action: the banner appears above the composer with a
      counting fuse
- [ ] Open it: the ring, the reason and the raw call all match what was asked
- [ ] Slide half-way and release: the knob snaps home and nothing is confirmed
- [ ] Slide fully: the pill reads `Confirmed · queued`, then `Applied` once the
      house reports — **not** immediately
- [ ] Let a second one lapse: the pill reads `Expired`, and the Room carries a
      struck-through row reading `expired HH:MM · not done · asked HH:MM`
- [ ] Under 30 s the ring's arc changes colour; nothing pulses

## Reduce motion

- [ ] Settings › Accessibility › Motion › Reduce Motion on: the presence field is
      static, gates and the Door cross-fade in about 200 ms instead of rising

## Themes

- [ ] The toggle switches instantly and survives a relaunch
- [ ] Safari's chrome colour matches the theme in both

---

Device: ______________  iOS: ______  Build: ______________  Date: ____________
Tester: ______________
```

- [ ] **Step 10: Full verification**

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client/web
npm run lint && npm test && npm run build
```

Expected: no eslint output, `Test Files  39 passed (39)`, `✓ built in …`.

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
uv sync --all-extras
uv run pytest tests/core/channels/test_spa_ci.py -q
```

Expected: `2 passed`. `2 skipped` means `web/dist/index.html` is missing — build first.

```bash
git grep -n -E '192\.168\.50\.|66\.60\.90\.|anirudhlath\.com'
```

Expected: **no output.** The repository is public; every host in this branch is
`alfred.example.com` and every LAN address is `192.168.1.x`.

```bash
grep -rn "TODO\|FIXME" web/src --include='*.ts' --include='*.tsx'
```

Expected: no output.

- [ ] **Step 11: Commit the documentation**

```bash
git add web/README.md docs/superpowers/qa/2026-09-07-pwa-phase1-ios-checklist.md
git commit -m "docs(web): rewrite the client README and add the iOS QA checklist"
```

---

## Done

Twenty-eight tasks across two plans, one branch, one PR. The client is complete for phase 1: a first-run device is walked through a passkey, Home Assistant and the attention set; a signed-in one lands in a Room that shows Alfred's presence, the merged thread, everything he did while you were not asking, and a composer that keeps what you typed while the house was unreachable; and a critical action rises as a Door with a fuse that cannot be dismissed by accident and cannot be confirmed by a tap.

### Verify the branch

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client/web
npm run lint && npm test && npm run build
```

Expected: eslint prints nothing, `Test Files  39 passed (39)`, `✓ built in …`.

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
uv run pytest tests/core/channels/test_spa_ci.py
```

Expected: `2 passed`. `2 skipped` means `web/dist/` was not built — run `npm run build` in `web/` first; CI builds it in the `web` job and hands it to the `spa` job.

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
git grep -n -E '192\.168\.50\.|66\.60\.90\.|anirudhlath\.com'
```

Expected: **no output.** The repository is public. Every hostname on this branch is `alfred.example.com`, every LAN address `192.168.1.x`, and the setup and sign-in gates read the real hostname from `location.hostname` at runtime.

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
grep -rn "TODO\|FIXME\|placeholder" web/src --include='*.ts' --include='*.tsx' | grep -v "placeholder=" | grep -v "placeholder:"
git grep -n "audio/webm" -- web/
git grep -n "location.assign" -- web/src
git log --oneline origin/master..HEAD | wc -l
```

Expected: nothing from the first three (`placeholder=` on the credential inputs and `placeholder:` in the fixtures are the schema's own field), and `28` commits — one per task.

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
node -e "const d=require('./web/package-lock.json').packages[''].dependencies; const need=['@fontsource-variable/dm-sans','@fontsource/geist-mono'].filter(n=>!(n in d)); if(need.length){console.error('missing: '+need.join(', '));process.exit(1)} console.log('lockfile ok')"
```

Expected: `lockfile ok` — CI runs `npm ci`, so the lockfile must be committed with the dependency swap from 1a Task 1.

### Open the PR

```bash
cd ~/code/.worktrees/alfred/pwa-phase1-client
git push -u origin feat/pwa-phase1-client
gh pr create --title "feat(web): PWA client phase 1 — shell, gates, Room and Door" --body-file -
```

PR title (the `pr-title` job enforces the conventional-commit shape, and this becomes the squash commit):

```
feat(web): PWA client phase 1 — shell, gates, Room and Door
```

PR body draft:

```markdown
Replaces `web/` with the phone-first client from the approved design. Phase 1 of six:
the shell, the four identity gates, the Room and the Door. Merging this deploys it.

Specs: `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md`
Plans: `docs/superpowers/plans/2026-09-07-pwa-phase1a-shell-and-gates.md` (tasks 1–14)
and `…-phase1b-room-and-door.md` (tasks 15–28)
Design: `docs/design/2026-09-04-pwa-client-handoff/`

## What this is

A hard cut, not a retrofit. The outgoing Mission Control SPA — icon rail, telemetry
rail, data tables, shadcn component library — is deleted in the first commit; what
survives is `ws.ts`, `chat-socket.ts`, `telemetry-socket.ts` and `webauthn.ts`.
Everything else is built from the handoff.

**The shell.** Tokens for both themes as CSS custom properties, a twelve-step type
scale, two easing curves, `visualViewport` mirrored into `--app-height` and
`--keyboard-inset`, safe-area classes, and `Layer`/`Sheet` — the two surfaces
everything rises on.

**The gates.** Setup (passkey → Home Assistant credentials → the attention set),
sign-in, expired and denied. 401 and 403 no longer redirect: `api()` emits on a tiny
emitter and `AuthGate` raises the matching gate *over* whatever is on screen, so a
lapsed session never blanks the last-known state.

**The Room.** A canvas presence field driven by the microphone while you hold the
button and by a thinking envelope while Alfred is working; a headline that tells the
truth about the connection before anything else; a status line in four states; the
merged timeline (four stream pages plus everything live); a composer that queues what
you type offline and retries it in order; hold-to-talk with a one-second floor; and
the held-back queue behind the DND row.

**The Door.** A pending approval tracked from three independent sources — the
`/api/actions/pending` read, the `/ws` notification carrying `pending_action_id`, and
the `home_action_results` telemetry stream — so it survives a cold launch, a
notification tap and a suspended app. Confirmed by a slide with travel, never a tap.
Expiry leaves a struck-through tombstone in the thread.

## Honesty, specifically

- A 200 from `POST /api/actions/{id}/confirm` reads `Confirmed · queued`. `Applied`
  comes only from the `home_action_results` stream.
- `Drain queue now` becomes `Queued`, with a note saying the server does not report
  delivery.
- Offline and reconnecting are always stamped with `last true HH:MM`.
- DND with no expiry says `Quiet until further notice.` and the queue is one tap away.
- A lapsed approval says `expired HH:MM · not done`, and a 404 says `already answered`.

## iOS constraints (spec §4)

Automated: viewport and Apple metas (`index-html.test.ts`), `--app-height` and the
keyboard inset (`viewport.test.ts`, `Composer.test.tsx`), `overscroll-behavior`,
one unlocked `AudioContext` (`audio.test.ts`), `audio/mp4` → `audio/aac` codec
negotiation (`recorder.test.ts`), rehydration on `visibilitychange`
(`lifecycle.test.ts`, `ConnectionProvider.test.tsx`), explicit dismissal on every
layer (`Layer.test.tsx`, `DoorLayer.test.tsx`), reduce-motion (`Layer.test.tsx`,
`PresenceField.test.tsx`).

Manual, on a real phone: `docs/superpowers/qa/2026-09-07-pwa-phase1-ios-checklist.md`.
Icons (§4.2) and Web Push (§4.9) are deferred to phases 4–5 and the checklist says so.

## Deliberate deviations from the handoff

| Where | Handoff | Shipped | Why |
|---|---|---|---|
| Expired gate | "ran out after 30 days" | "after eight hours" | Phase 0 cut `_AUTH_SESSION_TTL` to 8 h; the prototype's number is now false |
| Room headline | `Quiet until 08:30.` | adds `Quiet until further notice.` | DND can have no expiry, and that queue never drains |
| Headline priority | first-day greeting wins | offline/reconnecting win | An unreachable house must not say "Good morning" |
| Act row from a live frame | `HH:MM · {source} · {urgency}` | `HH:MM · live · {urgency}` | The `/ws` notification frame carries no `source` |
| Alfred row, error reply | `mood · tools · HH:MM` | `error · HH:MM` | An error has no mood and ran no tools |
| Thinking row | `conscious mind · calendar.today running` | `conscious mind · working` | The tools are only known once the reply arrives |
| Door state pill | four words | adds `Answered` | The handoff's own "already consumed (404)" case needed one |
| Door foot, queued | "Waiting for the lock to report" | "Waiting for it to report" | The same Door renders lights and media players |
| `signal()` band loop | `s / (hi - lo)` | `s / max(1, hi - lo)` | With `fftSize 256` the top band is empty and the prototype divides by zero, turning the whole field into `NaN` |

## Not in this PR

The Workshop and the causal thread (phases 2–3), `vite-plugin-pwa`, the service
worker, icons, the install/standalone/Reach gates and Web Push (phases 4–5), and the
desktop composition (phase 6). `public/manifest.json` is untouched.

## Testing

`npm run lint && npm test && npm run build` — 39 test files, all green — plus
`uv run pytest tests/core/channels/test_spa_ci.py`.
```

### Self-review before requesting review

- [ ] Every task's commit exists and its message is a conventional-commit line
- [ ] `git diff origin/master --stat -- web/src` shows no file this plan did not name
- [ ] No component reads a colour outside `var(--…)`, and no ad-hoc font size that the `.t-*` scale already covers
- [ ] The three copy blocks that must be verbatim — the setup gate, the held-back sheet and the Door's five foot lines — read identically to the handoff, except where the deviations table says otherwise
- [ ] The QA checklist has been run once on a real phone, and its footer is filled in
