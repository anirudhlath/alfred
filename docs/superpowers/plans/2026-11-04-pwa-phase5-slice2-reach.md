# PWA Phase 5 Slice 2: Asking in the Room and the Icon Badge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An iPhone tab offers the install path when a Door is waiting, the first home-screen launch asks once, the iOS answer is written into the Room, and the icon counts pushes since the app was last opened.

**Architecture:** The worker keeps the count in IndexedDB in its own `waitUntil` branch, and the page clears it; neither can cost a notification. Device-only facts (Act rows, answered Doors, the gate's flag) live in `localStorage`. One hook, `useReachPrompts`, derives the prompt row and the self-opening gate from Doors, device and house key, and one component, `ReachGates`, hosts the gate pair for both the Room and Reach. No server change.

**Tech Stack:** React 19, TanStack Query, Vitest + jsdom, hand-written `public/sw.js` tested through `src/test/sw-harness.ts`, `fake-indexeddb` (new dev dependency).

**Spec:** `docs/superpowers/specs/2026-11-04-pwa-phase5-slice2-reach-design.md`. Read it before starting. Its copy is verbatim and it outranks this plan.

## Global Constraints

- The repo is public. Placeholders only (`alfred.example.com`). Never quote the secret-hygiene checks in a committed file.
- Handoff copy is verbatim. Anything invented goes in the spec's Deviations table (D8–D11).
- `enable()` stays the first thing in any tap handler that asks iOS: an await or state change ahead of it can end the gesture.
- Nothing about the badge may delay or prevent `showNotification`.
- `sw.js` rule 3: every worker global goes through `self.`. `sw.js` is not prettier-formatted.
- `erasableSyntaxOnly` is on (no parameter properties). The react-hooks purity rule bans `Date.now()` and `Math.random()` in render. State is adjusted during render, not in effects (the `wasAsking` idiom).
- No model identifier in commits, PRs or code. PR titles are conventional commits.
- Markdown in the repo cites tickets with a full `https://github.com/anirudhlath/alfred/issues/N` link; code comments use `issue #N`.
- Every review finding gets fixed, Minors included.
- Frontend gate: `cd web && npm run lint && npm run test && npm run build`. Run only the narrow test file while iterating, the gate once per task before its commit.

## Decisions the spec left to this plan

| Question | Decision |
|---|---|
| Detecting an iPhone | `/\biPhone\b/.test(navigator.userAgent)` in a new `lib/device.ts`. Its own module, so the tests' factory mocks of `@/lib/push` keep working and it reads false in jsdom unless a test stubs it. iPadOS reports a Mac, which D9 wants |
| Prompt row | A new `TimelineItem` kind, `reach`, merged by `useRoom` beside tombstones: never windowed, because it must last as long as the Door |
| Device Act rows joining the merge | Ordinary `act` items, handed to `sessionWindow` with the history. They are not turns, so they follow the window without extending it |
| The gate's `signedInAt` in the Room | Omitted (`null`), under slice 1's D5. A launch should not read the sessions list for one line of meta |
| Telling the Room about a new Act row from Reach | The query cache: `usePush` already writes there, so `REACH_ACTS_KEY` needs no new store |
| IndexedDB | Database `alfred-badge`, version 1, store `count`, key `pushes`, value a number |
| `localStorage` | `alfred.reach.acts` (`[{at, outcome}]`), `alfred.reach.answeredDoors` (`[request_id]`), `alfred.reach.gateAnswered` (`"1"`) |
| The badge branch's time limit | `BADGE_TIMEOUT_MS = 5000` |
| Worker tests | `fake-indexeddb`, for real transaction semantics, with `clockHeld` on the harness (Task 1) because its timing is real and the harness's clock is virtual |
| The message | `{type: "BADGE_ADDED"}` |

## Review Focus

1. **A push the worker can't read** (`event.data` null, or not JSON) still shows `Alfred` and must still add one, or the number under-counts what the shade shows. Task 1.
2. **Junk in the stored count** (a string, a negative, a fraction) must count as zero, not make `setAppBadge` throw or show `NaN`. Task 1.
3. **`setAppBadge` rejecting or never answering** must not hold the event or stop the notification; a rejection still tells the windows, since the count is written. Task 1.
4. **`localStorage` throwing on a write** (`Not now`, a gate button, an Act row) must not throw into a tap handler or block `enable()`, and what was tapped holds for this run. Tasks 3, 4 and 5.
5. **A Door whose `timestamp` is unreadable** must still give a row, placed at the Door clock's time, not a `NaN` that scrambles the timeline's sort. Task 5.

---

### Task 1: The worker counts

**Files:**
- Modify: `web/public/sw.js` (header rule 3 and the "caches nothing for either" paragraph; new badge section; `push` listener at about 840-860; `openPath` at about 875-890)
- Modify: `web/eslint.config.js:48-59`
- Modify: `web/src/test/sw-harness.ts` (`SEALED`, `LoadOptions`, `Harness`, the scope, `after`, the "ten globals" comment)
- Modify: `web/src/test/sw-harness.test.ts:1089` (extend the bare-global test to both new names)
- Create: `web/src/test/sw-badge.test.ts`
- Modify: `web/package.json`, `web/package-lock.json`

**Interfaces:**
- Produces: the IndexedDB schema and `{type: "BADGE_ADDED"}` message from the table above. Harness levers later tasks use: `loadWorker(source, { indexedDB, appBadge, clockHeld })`, `worker.badges: number[]`.

- [ ] **Step 1: Set up and commit the design**

```bash
cd web && npm ci && npm install -D fake-indexeddb@^6 && cd ..
git add docs/superpowers/specs/2026-11-04-pwa-phase5-slice2-reach-design.md docs/superpowers/plans/2026-11-04-pwa-phase5-slice2-reach.md
git commit -m "docs(pwa): phase 5 slice 2 design and plan"
```

- [ ] **Step 2: Add the harness levers**

In `sw-harness.ts`:
- `SEALED` gains `"indexedDB"` and `"navigator"`. The comment on `loadWorker` says twelve globals.
- `LoadOptions` gains:
  - `indexedDB?: Pick<IDBFactory, "open">`. What `self.indexedDB` is; absent (undefined) by default.
  - `appBadge?: "sets" | "rejects" | "hangs"`. Absent by default, so `self.navigator` is `{}`. Otherwise `self.navigator.setAppBadge(n)` records `n` in `badges` and `` `badge ${n}` `` in `events` at the call, then resolves, rejects with `NotAllowedError`, or never settles.
  - `clockHeld?: boolean`. Timers are armed and do not fire until `releaseClock()`. A doc comment says why: `fake-indexeddb` takes real macrotasks, and the virtual clock would otherwise fire a 5-second deadline before a healthy store answers.
- `Harness` gains `badges: number[]` and `releaseClock(): void`.

`after()` skips `wake()` while held; `releaseClock()` clears the hold and wakes. No existing test passes these, so none changes.

- [ ] **Step 3: Write the failing tests in `src/test/sw-badge.test.ts`**

Follow `sw-push.test.ts:1-28` for `?raw` source, the `FIRED` check and the stands-down test. Helper:

```ts
const NOTICE = { title: "Bins go out tonight", body: "Friday.", urgency: "important", id: "n-7", action_id: null };
const counting = (options: LoadOptions = {}) =>
  loadWorker(source, { indexedDB: new IDBFactory(), appBadge: "sets", clockHeld: true, ...options });
const BADGE_TIMEOUT_MS = 5000;
```

| Test name | Arrange and assert |
|---|---|
| `adds one per push` | Two pushes in turn. `badges` is `[1, 2]`, `shown` has 2 |
| `counts two pushes at once as two` | `await Promise.all([push, push])`. `[...badges].sort()` is `[1, 2]` |
| `counts nothing where setAppBadge is absent` | `loadWorker(source, { indexedDB: factory })`. `shown` has 1, `posted` empty, `await factory.databases()` is `[]` |
| `shows the notification when the store throws` | `indexedDB: { open() { throw new DOMException("x", "UnknownError"); } }`. Resolves; `shown` has 1; `badges` and `posted` empty |
| `shows the notification when the store errors` | A fake `open` whose request fires `onerror` on the next macrotask. Same assertions |
| `gives up on a store that hangs` | `indexedDB: { open: () => ({}) as IDBOpenDBRequest }`, clock not held. Resolves; `shown` has 1; `deadlines` is `[BADGE_TIMEOUT_MS]`; `posted` empty |
| `does not hold the notification behind a hung store` | Same store, `clockHeld: true`. `const pushed = worker.push(NOTICE); await vi.waitFor(() => expect(worker.shown).toHaveLength(1)); worker.releaseClock(); await pushed;` |
| `tells every open window after the badge is set` | `clients: [{ path: "/" }, { path: "/actions/abc" }]`. `events` is `["badge 1", "post <origin>/", "post <origin>/actions/abc"]`; each `posted[].data` is `{ type: "BADGE_ADDED" }` |
| `clears its deadline` | After one push, `deadlinesCleared === deadlines.length` |
| `counts a push it cannot read` | `push(undefined, { raw: "{not json" })` then `push()`. `badges` is `[1, 2]`, both shown as `Alfred` |
| `treats junk in the store as zero` | For each of `"junk"`, `-3`, `1.5`: seed through the factory (open with version 1, create store `count`, put under `pushes`). Next push: `badges` is `[1]` |
| `tells the windows when setAppBadge rejects` | `appBadge: "rejects"`. Resolves; `shown` has 1; `posted` has 1. A second push gives `badges` `[1, 2]`, so the count was written |
| `gives up on a setAppBadge that hangs` | `appBadge: "hangs"`, `clockHeld: true`. `waitFor` `badges` is `[1]`, `releaseClock()`, push resolves, `shown` has 1 |

Import `IDBFactory` from `fake-indexeddb`.

- [ ] **Step 4: Run them to see them fail**

Run: `cd web && npx vitest run src/test/sw-badge.test.ts`
Expected: the counting tests FAIL with `badges` empty. Tests that only assert `shown` may pass already.

- [ ] **Step 5: Implement in `sw.js`**

- Constants: `BADGE_DB`, `BADGE_STORE`, `BADGE_KEY`, `BADGE_TIMEOUT_MS`.
- `addToCount(): Promise<number>`. Open version 1, creating the store in `onupgradeneeded`. In one read-write transaction: `get`, then in its `onsuccess` `put(next, BADGE_KEY)`, where `next` is the stored value plus one, or `1` when that value is not a positive integer. Resolve `next` on `oncomplete`, reject on `onerror` and `onabort`. Close the database either way. No `await` between `get` and `put`: the transaction would be inactive.
- `ownWindows(): Promise<WindowClient[]>`, extracted from `openPath` (`matchAll({type: "window", includeUncontrolled: true})`, same-origin filter); `openPath` uses it.
- `countPush(): Promise<void>`, which never rejects. Returns at once unless `"setAppBadge" in self.navigator` and `self.indexedDB`. Otherwise races this against a `self.setTimeout` of `BADGE_TIMEOUT_MS`, and clears the timer afterwards: `addToCount()`, then `self.navigator.setAppBadge(n)` with its rejection swallowed, then post `{type: "BADGE_ADDED"}` to `ownWindows()`.
- `push` listener: `event.waitUntil(showNotification(...))` stays first and unchanged. Then `event.waitUntil(countPush())`.
- Header: rule 3 says twelve globals and names `indexedDB` and `navigator`. The "caches nothing for either" paragraph stays true and gains a sentence: each push also adds to the icon's count, kept in IndexedDB rather than the caches because rule 1 keeps them for the shell, `setAppBadge` has no getter, and this worker dies between pushes. `VERSION` stays `v1`: the cache did not change.
- `eslint.config.js`: add both names to the list. Extend `sw-harness.test.ts:1089` to refuse `indexedDB` and `navigator` reached bare.

- [ ] **Step 6: Run the worker suites**

Run: `cd web && npx vitest run src/test/sw-badge.test.ts src/test/sw-push.test.ts src/test/sw.test.ts src/test/sw-harness.test.ts`
Expected: PASS.

- [ ] **Step 7: Run the gate and commit**

```bash
cd web && npm run lint && npm run test && npm run build && cd ..
git add web/public/sw.js web/eslint.config.js web/src/test web/package.json web/package-lock.json
git commit -m "feat(pwa): count pushes on the icon from the worker"
```

---

### Task 2: The page clears the count

**Files:**
- Create: `web/src/lib/badge.ts`, `web/src/lib/badge.test.ts`
- Modify: `web/src/shell/PushKeeper.tsx`, `web/src/shell/PushKeeper.test.tsx`
- Modify: `web/src/lib/push.ts` (`forgetPush`), `web/src/lib/push.test.ts`
- Modify: `web/src/workshop/ReachSection.tsx`, `web/src/workshop/ReachSection.test.tsx`
- Modify: `web/src/test/sw-badge.test.ts`

**Interfaces:**
- Consumes: Task 1's schema and message.
- Produces, in `lib/badge.ts`: `badgeSupported(): boolean`; `clearBadge(): Promise<void>`, which never rejects; `onBadgeAdded(fn: () => void): () => void`.

- [ ] **Step 1: Write the failing tests**

`lib/badge.test.ts`, with `vi.stubGlobal("indexedDB", new IDBFactory())` and `setAppBadge`/`clearAppBadge` defined on `navigator` where a test needs them:
- `clearBadge does nothing where setAppBadge is absent`: `indexedDB.open` is not called.
- `clearBadge clears the icon when the store hangs`: `open` never answers; `clearAppBadge` was called.
- `clearBadge never rejects`: `open` throws and `clearAppBadge` rejects; `await expect(clearBadge()).resolves.toBeUndefined()`.
- `onBadgeAdded hears BADGE_ADDED only`: ignores `{type: "OPEN_PATH", path: "/"}` and non-objects; the unsubscriber stops it; does nothing where `serviceWorker` is absent. Fake the container as `push.test.ts`'s `onOpenPath` tests do.

`push.test.ts`:
- `onOpenPath ignores BADGE_ADDED`.
- `forgetPush clears the badge, and a clear that never settles does not hold it`, with `vi.mock("./badge")` and `clearBadge` returning a pending promise; `forgetPush` resolves and `clearBadge` was called, including with no `serviceWorker` in `navigator`.

`PushKeeper.test.tsx`, with `clearBadge` mocked and `onBadgeAdded` real:
- `clears on mount in the installed app`.
- `clears on return to the foreground` (dispatch `visibilitychange` with `visible`).
- `clears on the worker's message while visible`.
- `does not clear on the message while hidden` (stub `document.visibilityState` to `hidden`).
- `clears nothing in a tab`, with `reachContext` returning `"tab"`: mount, foreground and message all leave it uncalled.

`sw-badge.test.ts`, the pin between worker and page, with `navigator.setAppBadge`/`clearAppBadge` stubbed and `indexedDB` stubbed to the worker's factory:
- `the page's clear zeroes the worker's count`: push, push, `await clearBadge()`, push. `badges` is `[1, 2, 1]`.
- `the worker reads a database the page created`: `await clearBadge()` on a fresh factory, then push. `badges` is `[1]`.

`ReachSection.test.tsx`:
- `names the badge in the on line where setAppBadge exists`: exact text `on · this iPhone · important and urgent only · badge = notifications since you last opened`.
- `leaves the clause out elsewhere`: exact text `on · this iPhone · important and urgent only`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && npx vitest run src/lib/badge.test.ts src/shell/PushKeeper.test.tsx src/lib/push.test.ts src/workshop/ReachSection.test.tsx src/test/sw-badge.test.ts`
Expected: FAIL (`@/lib/badge` does not exist).

- [ ] **Step 3: Implement**

- `lib/badge.ts`: non-exported copies of the schema names, with a comment naming `sw.js` and the pin test. `clearBadge`: return at once unless `badgeSupported()`; then, independently, so neither stops the other, put `0` in the store (same open and upgrade as the worker, closing the database) and call `navigator.clearAppBadge()`; settle when both have. `onBadgeAdded` follows `onOpenPath`'s shape.
- `PushKeeper`: an effect that returns at once unless `reachContext() === "capable"`, calls `clearBadge()`, and subscribes `onVisible(clearBadge)` and `onBadgeAdded(() => { if (document.visibilityState === "visible") clearBadge(); })`. Comment that the worker always adds and the page decides, because a worker's reading of `visibilityState` is unverified on iOS.
- `forgetPush`: `void clearBadge();` as its first statement, unawaited, so a hung store can't hold sign-out, and ahead of the `serviceWorker` check.
- `ReachSection`: the `on` line takes the clause when `badgeSupported()`. The `LINES` table keeps slice 1's text.

- [ ] **Step 4: Run them to see them pass**

Run: the command from Step 2.
Expected: PASS.

- [ ] **Step 5: Run the gate and commit**

```bash
cd web && npm run lint && npm run test && npm run build && cd ..
git add web/src/lib web/src/shell web/src/workshop web/src/test
git commit -m "feat(pwa): clear the icon badge on open and on sign-out"
```

---

### Task 3: The Act rows

**Files:**
- Create: `web/src/lib/device.ts`, `web/src/lib/reach-local.ts`, `web/src/lib/reach-local.test.ts`
- Create: `web/src/room/useReachPrompts.ts`
- Modify: `web/src/shell/usePush.ts`, `web/src/shell/usePush.test.tsx`
- Modify: `web/src/room/useRoom.ts`, `web/src/room/useRoom.test.tsx`
- Modify: `web/src/room/Room.tsx`

**Interfaces:**
- Produces, in `lib/device.ts`: `onIPhone(): boolean`.
- Produces, in `lib/reach-local.ts`:

```ts
export const REACH_ACTS_KEY = ["reach", "acts"] as const;
export type ReachOutcome = "on" | "off";
export interface ReachAct { at: string; outcome: ReachOutcome }
export function readReachActs(): ReachAct[];
export function addReachAct(outcome: ReachOutcome, at: string): ReachAct[]; // the list including it, even if the write failed
export function reachActRow(act: ReachAct): TimelineItem; // kind "act", hue 255
export function readAnsweredDoors(): string[];
export function writeAnsweredDoors(ids: string[]): void;
export function gateAnswered(): boolean;
export function markGateAnswered(): void;
```

- Produces, in `room/useReachPrompts.ts`: `useReachActRows(): TimelineItem[]`.
- Produces, on `useRoom`: option `deviceActs?: TimelineItem[]`.

Later tasks use `readAnsweredDoors`, `writeAnsweredDoors`, `gateAnswered`, `markGateAnswered` and `onIPhone`.

- [ ] **Step 1: Write the failing tests**

`reach-local.test.ts`:
- `reachActRow on`: text `Notifications on for this iPhone`, meta `${hhmm(at)} · notifications · iOS allowed · important and urgent only`, hue 255.
- `reachActRow off`: text `Notifications stay off on this iPhone`, meta `${hhmm(at)} · iOS declined · Settings › Alfred › Notifications to change · not asked again`, hue 255.
- `each list survives a reload`: write, then read back from `localStorage`.
- `corrupt storage reads as empty`: `"{"`, `"null"`, and for acts `[{"outcome":"bad","at":"x"}]`, an unreadable `at`, or a non-object entry give `[]` (a good entry beside a bad one survives); for doors non-string entries are dropped.
- `a throwing store loses the facts, never the app`: `vi.spyOn(Storage.prototype, "setItem")` and `getItem` throwing. Nothing throws. `addReachAct` still returns the new act. `gateAnswered()` is false.

`usePush.test.tsx`, with a helper stubbing `navigator.userAgent` to an iPhone string and reading `client.getQueryData(REACH_ACTS_KEY)`:
- `writes the on row when iOS allows and the house registers`.
- `writes the off row when iOS declines`.
- `writes no row for a dismissed dialog` (`"default"`).
- `writes no row when the registration fails` (rejects 503).
- `writes no row when the house has no key` (rejects `ApiError(404)`).
- `writes no row off an iPhone` for both `"granted"` and `"denied"`.

`useRoom.test.tsx`:
- `shows a device act at its time`: an act stamped between two turns sorts between them.
- `drops a device act the window has moved past`: an act from yesterday, no session turn reaching back.
- `a device act does not extend the session`: a turn older than `idleMs` stays out although a device act is newer than it.

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && npx vitest run src/lib/reach-local.test.ts src/shell/usePush.test.tsx src/room/useRoom.test.tsx`
Expected: FAIL (modules and option missing).

- [ ] **Step 3: Implement**

- `lib/device.ts`: as in the decisions table.
- `lib/reach-local.ts`: the `readUnsent`/`writeUnsent` pattern (`useRoom.ts:133-148`), every access in try/catch.
- `usePush.enable`: in the fulfilled handler, after the existing `setQueryData`, when `answer !== "default" && onIPhone()`:
  `queryClient.setQueryData(REACH_ACTS_KEY, addReachAct(answer === "granted" ? "on" : "off", new Date().toISOString()))`. The rejection branch writes nothing. `requestAndSubscribe()` stays the handler's first line.
- `useRoom`: `sessionWindow([...(history ?? []), ...(deviceActs ?? [])], now, idleMs)`, and `deviceActs` joins the memo's dependencies. Update the option comment that says what is and isn't windowed.
- `useReachActRows`: `useQuery({ queryKey: REACH_ACTS_KEY, queryFn: readReachActs, initialData: readReachActs, staleTime: Infinity })`, mapped through `reachActRow` in a `useMemo`.
- `Room`: pass `deviceActs: useReachActRows()` to `useRoom`.

- [ ] **Step 4: Run them to see them pass**

Run: the command from Step 2.
Expected: PASS.

- [ ] **Step 5: Run the gate and commit**

```bash
cd web && npm run lint && npm run test && npm run build && cd ..
git add web/src/lib web/src/shell web/src/room
git commit -m "feat(pwa): write the iOS answer into the Room"
```

---

### Task 4: The gate opens itself

**Files:**
- Create: `web/src/gates/ReachGates.tsx`, `web/src/gates/ReachGates.test.tsx`
- Modify: `web/src/workshop/ReachSection.tsx`, `web/src/workshop/ReachSection.hook.test.tsx`
- Modify: `web/src/room/useReachPrompts.ts`
- Create: `web/src/room/useReachPrompts.test.tsx`
- Modify: `web/src/room/Room.tsx`, `web/src/App.test.tsx`

**Interfaces:**
- Consumes: `onIPhone`, `gateAnswered`, `markGateAnswered` (Task 3); `reachContext`, `permission`, `fetchPublicKey` (`lib/push.ts`).
- Produces:

```ts
// gates/ReachGates.tsx
export type ReachGate = "install" | "standalone";
export interface ReachGatesProps { gate: ReachGate | null; signedInAt: string | null; onClose: () => void }
export function ReachGates({ gate, signedInAt, onClose }: ReachGatesProps);

// room/useReachPrompts.ts
export const PUSH_KEY_QUERY_KEY = ["push", "public-key"] as const;
export function useHouseKey(wanted: boolean): { answered: boolean; has: boolean };
export interface ReachPrompts { gate: ReachGate | null; closeGate: () => void }
export function useReachPrompts(): ReachPrompts; // Task 5 adds parameters and fields
```

- [ ] **Step 1: Write the failing tests**

`ReachGates.test.tsx`, over a mocked `@/lib/push`:
- `Let me reach you asks iOS, holds the button, and closes when the asking ends`: `requestAndSubscribe` called once; the primary is busy until it resolves; `onClose` called after.
- `a tap on Let me reach you ends the self-opening even if the dialog is dismissed`: resolves `"default"`; `gateAnswered()` is true.
- `Not yet closes and ends the self-opening`: `onClose` called; `gateAnswered()` true; `requestAndSubscribe` not called.
- `the install gate's Not now only closes`: `onClose` called; `gateAnswered()` false.
- `a throwing store does not stop the ask`: `setItem` throws; `requestAndSubscribe` still called; nothing thrown.

`useReachPrompts.test.tsx`, `renderHook` with a fresh `QueryClient` per launch, `reachContext` `"capable"`, `permission` `"default"`, `fetchPublicKey` resolving a key, UA stubbed to an iPhone unless the test says otherwise:
- `opens once the house's key is read`.
- `does not open off an iPhone`.
- `does not open for granted`, `does not open for denied`: `fetchPublicKey` not called either.
- `does not open with no key` (`null`).
- `does not open in a tab`.
- `skips a launch whose key read fails, and opens on the next`: rejected, stays null; a new client and mount with a key opens.
- `does not open after either button`: `markGateAnswered()`; on mount null; `fetchPublicKey` not called.
- `shows again after a launch with neither tap`: unmount with no `closeGate`; a new mount opens.
- `stays closed for this launch once closed`: `closeGate()`, then `client.invalidateQueries()`; still null.

`ReachSection.hook.test.tsx` (Reach's route to the Act rows, with an iPhone UA):
- `Enable then Let me reach you writes the on row`, and `... declined writes the off row`. Read `REACH_ACTS_KEY` from the client.

`App.test.tsx` (the Room's route). Mock `@/lib/push` with `importOriginal` and a `reachContext` spy passing through to the original by default, so existing tests are unchanged. Route `/api/push/public-key` in the `fetch` stub:
- `an iPhone's first launch from the home screen raises the gate, and a decline is written into the Room`: `reachContext` `"capable"`, iPhone UA, `vi.stubGlobal("Notification", { permission: "default", requestPermission: async () => "denied" })`. Find `You came in the front way.`; tap `Let me reach you`; gate closes; `Notifications stay off on this iPhone` is in the Room.

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && npx vitest run src/gates/ReachGates.test.tsx src/room/useReachPrompts.test.tsx src/workshop/ReachSection.hook.test.tsx src/App.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `ReachGates`: move the two `Layer`s (with their labels, `level="gate"`, `durationMs={400}`) and the `wasAsking` adjustment out of `ReachSection`. It calls `usePush({ syncOnMount: false })` for `enable` and `asking`. `onAllow={() => { enable(); markGateAnswered(); }}`, `enable()` first. `Not yet`: `markGateAnswered(); onClose();`. The install gate's way out is `onClose` alone.
- `ReachSection`: renders `<ReachGates gate={gate} signedInAt={signedInAt} onClose={() => setGate(null)} />`. It keeps `usePush({ syncOnMount: false })` for `reach` and `retry`. `ReachSection.test.tsx` passes unedited.
- `useHouseKey`: `useQuery({ queryKey: PUSH_KEY_QUERY_KEY, queryFn: fetchPublicKey, enabled: wanted, retry: false, staleTime: Infinity, refetchOnWindowFocus: false })`. `answered` is `isFetched`; `has` is `typeof data === "string"`.
- `useReachPrompts`: `iPhone`, `context` and the launch's eligibility (`iPhone && context === "capable" && permission() === "default" && !gateAnswered()`) are each read once in a `useState` initialiser. A `selfOpened` latch, adjusted during render: once eligible and the key has answered, set the latch and, if `has`, set the gate to `"standalone"`. The latch is what stops a later re-read reopening it.
- `Room`: `const prompts = useReachPrompts();` and `<ReachGates gate={prompts.gate} signedInAt={null} onClose={prompts.closeGate} />` after `DoorLayer`.

- [ ] **Step 4: Run them to see them pass**

Run: the command from Step 2.
Expected: PASS.

- [ ] **Step 5: Run the gate and commit**

```bash
cd web && npm run lint && npm run test && npm run build && cd ..
git add web/src/gates web/src/workshop web/src/room web/src/App.test.tsx
git commit -m "feat(pwa): open the standalone gate once on an iPhone's launch"
```

---

### Task 5: The prompt row

**Files:**
- Create: `web/src/room/rows/ReachRow.tsx`
- Modify: `web/src/lib/history.ts` (`TimelineItem`)
- Modify: `web/src/room/Timeline.tsx`, `web/src/room/Timeline.test.tsx`
- Modify: `web/src/room/useRoom.ts`, `web/src/room/useRoom.test.tsx`
- Modify: `web/src/room/useReachPrompts.ts`, `web/src/room/useReachPrompts.test.tsx`
- Modify: `web/src/room/Room.tsx`, `web/src/App.test.tsx`

**Interfaces:**
- Consumes: `useHouseKey`, `readAnsweredDoors`, `writeAnsweredDoors`, `onIPhone`; `TrackedAction` (`lib/actions.ts`); `door.pending` and `door.now: number`.
- Produces:

```ts
// lib/history.ts, a new TimelineItem member
| { kind: "reach"; id: string; at: string }

// room/useReachPrompts.ts, replacing Task 4's signature
export interface ReachPrompts {
  row: TimelineItem | null;      // kind "reach"
  notNow: () => void;
  showMe: () => void;            // opens the install gate
  gate: ReachGate | null;
  closeGate: () => void;
}
export function useReachPrompts(pending: TrackedAction[], now: number): ReachPrompts;
```

- On `useRoom`: option `prompt?: TimelineItem | null`. On `Timeline`: prop `prompt?: { onShowMe: () => void; onNotNow: () => void }`.

- [ ] **Step 1: Write the failing tests**

`useReachPrompts.test.tsx`, new describe, `reachContext` `"tab"`, iPhone UA, key resolving, with a local `door(id, timestamp)` helper that wraps `pendingActionFixture` (`src/test/fixtures.ts`) as a `TrackedAction`:
- `shows one row, at the Door's time, once the house's key is read`: `row` is `{ kind: "reach", at: door.timestamp, ... }`.
- `does not read the key until a Door waits`: `pending` `[]`; `fetchPublicKey` not called. Rerender with a Door; it is called.
- `no row while the read is pending`, `no row when the read fails`, `no row with no key`.
- `no row off an iPhone`, `no row in the installed app`.
- `leaves with the last Door`: rerender with `[]`; `row` is null.
- `one row for several Doors, at the oldest's time`.
- `Not now removes it, and that holds across a reload`: `notNow()`, null; a new mount with the same `pending` is null.
- `one tap answers every Door then waiting`: `[A, B]`, `notNow()`; a new mount with `[A, B]` is null.
- `comes back with a new Door, at that Door's time`: `[A]`, `notNow()`, rerender `[A, B]`; `row.at` is B's.
- `does not come back for a Door already waiting`: `[A, B]`, `notNow()`, rerender `[B]`; null.
- `Not now holds for this run when the store throws`: `setItem` throws; `notNow()` does not throw; null. (Review Focus 4)
- `a Door with an unreadable time is placed at the Door clock`: `timestamp: "garbage"`, `now` given; `row.at` is `new Date(now).toISOString()`. (Review Focus 5)
- `the install gate stays open when the last Door leaves`: `showMe()`, rerender `[]`; `gate` is `"install"`, `row` is null.

`Timeline.test.tsx`:
- `renders the prompt row verbatim`: `That door is waiting on you below. Had this tab been closed, I would have had no way to tell you. Add me to the home screen and I can knock when something cannot wait.` and `notifications · unavailable in Safari · ${hhmm(at)}`.
- `its buttons call back`: `Show me how` calls `onShowMe`; `Not now` calls `onNotNow`.

`useRoom.test.tsx`:
- `the prompt sits at its time`: between two turns.
- `the prompt is not windowed`: stamped two days ago, still present.

`App.test.tsx`, default `reachContext` (a tab in tests), iPhone UA, a waiting action in the pending-actions route, key routed:
- `a waiting Door in an iPhone tab offers the install path`: the row's text appears; `Show me how` raises `Add me to the home screen.`; its `Not now` closes the gate and the row is still there; the row's `Not now` removes it and no new line is added to the timeline.

- [ ] **Step 2: Run them to see them fail**

Run: `cd web && npx vitest run src/room/useReachPrompts.test.tsx src/room/Timeline.test.tsx src/room/useRoom.test.tsx src/App.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `ReachRow({ at, onShowMe, onNotNow })`: read `docs/design/2026-09-04-pwa-client-handoff/Alfred.dc.html:72-79` and README `:143` first. Column gap 12, max-width 330. Text 19px/1.4, letter-spacing -.01em. Meta `.t-meta` (mono 11 muted, line-height 1.5). Buttons gap 8, both 44px tall, radius 22, weight 500 at 14px: `Show me how` filled ink on paper, padding 0 18, no border; `Not now` transparent, 1px `--line` border, padding 0 14. Take the ink, paper and line tokens from `gates/Gate.tsx`'s primary and secondary rather than inventing any.
- `Timeline`: a `case "reach"` that renders `ReachRow` with the `prompt` callbacks, or nothing when `prompt` is absent. The `never` default keeps the switch honest.
- `useRoom`: `prompt` joins the merged list beside `tombstones`, unwindowed; add it to the memo's dependencies.
- `useReachPrompts(pending, now)`:
  - `answered` is `useState(readAnsweredDoors)`.
  - `waiting` is `pending` without the answered ids. `pending` is oldest first, so `waiting[0]` is the first such Door.
  - `useHouseKey` is wanted when eligible for the gate, or when `iPhone && context === "tab" && waiting.length > 0`.
  - `row` is non-null only when that second condition holds and `has`, with `id: "reach:prompt"` and `at` as `waiting[0].action.timestamp`, or `new Date(now).toISOString()` when `Date.parse` of it is `NaN`. Memoise on `at`.
  - `notNow` sets `answered` to every `pending` request id and calls `writeAnsweredDoors` with them, replacing the old list so it can't grow.
- `Room`: `useReachPrompts(door.pending, door.now)`, `prompt: prompts.row` into `useRoom`, `prompt={{ onShowMe: prompts.showMe, onNotNow: prompts.notNow }}` into `Timeline`, with the object memoised so a Door tick doesn't re-create it.

- [ ] **Step 4: Run them to see them pass**

Run: the command from Step 2.
Expected: PASS.

- [ ] **Step 5: Run the gate and commit**

```bash
cd web && npm run lint && npm run test && npm run build && cd ..
git add web/src/lib web/src/room web/src/App.test.tsx
git commit -m "feat(pwa): offer the install path beside a waiting Door"
```

---

### Task 6: Docs and the device checklist

**Files:**
- Modify: `docs/web-frontend.md` (lines 29-30; "Not in slice 1", about 1848-1857; "What phases 1 to 5 do not cover", about 1873-1877)
- Modify: `web/README.md:164-166`
- Modify: `docs/PRD.md` (line 3 and row 78)
- Modify: `docs/superpowers/qa/2026-10-29-pwa-phase5-ios-checklist.md` (lines 7-9, 212-214, a new section)
- Modify: `docs/superpowers/qa/2026-09-16-pwa-phase4-ios-checklist.md:316-320`

**Interfaces:** none; this describes what Tasks 1 to 5 built.

- [ ] **Step 1: Find every line this slice made false**

Run: `git grep -n -i -E "slice 2|setAppBadge|pushsubscriptionchange|prompt row" -- docs/web-frontend.md docs/web-push.md web/README.md web/src web/public`
Expected: the lines above, plus any others to correct in Step 2. Dated plans and specs stay as written.

- [ ] **Step 2: Update `docs/web-frontend.md`**

- Lines 29-30: slice 2 is now what's built, and the desktop composition stays later.
- Replace "Not in slice 1" with what slice 2 does: the prompt row and its conditions, the self-opening gate, the Act rows, where device state lives (`lib/reach-local.ts`), and the badge (the worker adds in IndexedDB, the page clears it, why the worker never reads `visibilityState`). Add the `localStorage` keys and the `alfred-badge` schema. D8 to D11 are named with a pointer to the spec.
- `pushsubscriptionchange` is not built: link https://github.com/anirudhlath/alfred/issues/427. Seen and unseen: https://github.com/anirudhlath/alfred/issues/424. Taking back a suggestion: https://github.com/anirudhlath/alfred/issues/425.
- "What phases 1 to 5 do not cover": drop the slice 2 bullet; the iOS prompt can now be raised from the Room's gate as well.

- [ ] **Step 3: Update `web/README.md:164-166`**

Remove the slice 2 bullet. Its claim that Reach's `on` line names a badge was false, and now the line does, only where `setAppBadge` exists. The worker section gains one sentence about the count in IndexedDB.

- [ ] **Step 4: Update `docs/PRD.md`**

Row 78 keeps the prefix `Web app — reaches the phone when the app is closed` (`evals/coverage.yaml` keys on it) and gains: in an iPhone tab a waiting approval offers the install path, the first launch from the home screen asks once, the answer is written into the Room, and the icon counts pushes since the app was last opened. Its evidence gains plan `2026-11-04-pwa-phase5-slice2-reach.md`; the PR number joins when the PR opens. Line 3's date becomes the commit's date.

- [ ] **Step 5: Update the checklists**

Phase-5 checklist:
- Lines 7-9 now say it covers both slices.
- The "Nothing lies" line 212-214 becomes the true version: the row, the gate, the Act rows and the badge exist and are run in the new section; the number counts pushes, not approvals.
- New section `## Slice 2`, with these checkboxes, each with the exact test-push steps from the slice 1 sections:
  - **Right after deploy (owner):** `A newer Alfred is waiting` shows once. App closed: two pushes show `2`, and opening clears it. App in background: a push adds. App on screen: a push leaves no number. Sign-out clears the number. Notification Center keeps its notifications after an open. Reach's `on` line carries the badge clause.
  - **On a fresh install:** in a Safari tab, a waiting Door shows the prompt row, `Show me how` opens the install gate, `Not now` holds across a reload, and a new Door brings it back. First launch from the home screen raises `You came in the front way.`. `Not yet` holds on the next launch and Reach keeps `Enable`. A launch killed with neither tap shows it again. Allowing writes `Notifications on for this iPhone`; declining writes `Notifications stay off on this iPhone`.

Phase-4 checklist lines 316-320: the Room's prompt row and the gate's prompt now exist and the phase-5 checklist runs them.

- [ ] **Step 6: Verify and commit**

```bash
uv venv --python 3.13 && uv sync --all-extras
uv run pytest tests/evals/test_prd_coverage.py -q
cd web && npm run lint && npm run test && npm run build && cd ..
git add docs web/README.md
git commit -m "docs(pwa): describe phase 5 slice 2"
```

Expected: all pass.
