# PWA Phase 1 — Phone Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four things the first phone test of phase 1 found: the header and composer scroll away, new replies do not auto-scroll, routine suggestions show only a title, and the Room shows a month of old conversation.

**Architecture:** One CSS root-cause fix (the shell must be the viewport, so the timeline is the only scroller); notification rows render the notification *body*; the Room shows only the **current conversation session** — the turns since the last silence as long as the server's `SESSION_TIMEOUT_MINUTES` — with the house's rows (notifications, reflex acts) kept for the day; the client learns the idle timeout from `GET /api/admin/overview` and rotates its stored session id at the same boundary. Involuntary recall on the server is untouched.

**Tech Stack:** Vite 8 / React 19 / TypeScript strict / Vitest 4 + Testing Library + jsdom (web); FastAPI + pytest (admin API). Run web tests from `web/` (`npx vitest run`), never the repo root. Python tests: `uv run pytest tests/core/channels/test_admin_api.py -q`.

**Repo rules that apply to every task:**
- Work in `~/code/.worktrees/alfred/pwa-phase1-phone-fixes` on branch `fix/pwa-phase1-phone-fixes`. Never touch `~/code/alfred-deploy/alfred`.
- The repo is public. No real hostnames, LAN addresses or names in any file.
- Each task ends with a commit. Do not push.
- jsdom does no layout: a CSS change is verified with the headless-Chrome fixture in Task 1, not with Vitest.

**Evidence the plan rests on (measured 2026-09-09):**
- `#root { min-height: … }` at 390×844 with a 5000 px thread: root 5305 px tall, the document scrolls, `Timeline.scrollHeight === clientHeight` (so `el.scrollTop = el.scrollHeight` is a no-op). With `height: …; overflow: hidden`: root 844 px, document not scrollable, timeline 613/5074 — overflows and scrolls. That is issues 1 and 2 with one cause.
- Routine suggestions arrive as `title: "Routine Suggestion"`, `body: "I've noticed a pattern: 'morning_coffee' — … Want me to start doing this automatically?"`. Both `history.ts::notificationItem` and the live path in `useRoom.ts` render `title` only. There is no backend endpoint or tool that accepts a routine, so the interaction is a follow-up (backlog, Task 6), not a button.
- The server's chat session (`core/conscious/session.py`, `SESSION_TIMEOUT_MINUTES`, `shared/config.py:238,343`, default 30) expires 30 minutes after the last turn. The Room reads the last 50 rows per stream with no age bound, so it showed 2026-08-14 → 09-09. The client (`chat-socket.ts`) keeps `alfred.session` forever and re-sends it on every reconnect, so the server keeps resurrecting the same id (with a fresh, empty context once expired). Satellite turns (`channel: "satellite"`, ~36 of 50 requests) stay in the Room by decision.

---

## File map

| File | Change |
|---|---|
| `web/src/index.css` | `#root`: `height` + `overflow: hidden` (Task 1) |
| `web/src/lib/history.ts` | notification rows use `body` (Task 2); `SESSION_IDLE_MS`, `sessionWindow()` (Task 4) |
| `web/src/lib/history.test.ts` | tests for both (Tasks 2, 4) |
| `web/src/room/useRoom.ts` | live notification rows use `body` (Task 2); apply `sessionWindow`, `idleMs` option (Task 4) |
| `web/src/room/useRoom.test.tsx` | tests + fixture re-dating (Tasks 2, 4) |
| `core/channels/admin_api.py` | `session.idle_minutes` in the overview (Task 3) |
| `tests/core/channels/test_admin_api.py` | tests (Task 3) |
| `web/src/lib/types.ts`, `web/src/test/fixtures.ts` | `Overview.session` (Task 3) |
| `docs/admin-api.md` | overview field (Task 3); restated as behaviour (Task 6) |
| `web/src/room/useOverview.ts` | `sessionIdleMs()` (Task 4) |
| `web/src/room/Room.tsx` | pass `idleMs` to `useRoom` and to the chat socket (Tasks 4, 5) |
| `web/src/App.test.tsx` | session-window integration test (Task 4) |
| `web/src/lib/chat-socket.ts`, `chat-socket.test.ts` | session id rotation (Task 5) |
| `docs/web-frontend.md`, QA checklist, `docs/backlog/low/pwa-phase1-followups.md` | Task 6 |

---

### Task 1: The shell is the viewport

**Files:**
- Modify: `web/src/index.css:123-131`
- Modify: `docs/web-frontend.md:481-483`

Why: `min-height` lets `#root` grow with the thread. The document becomes the scroller, the header and composer scroll with it, and the Timeline never overflows, so its follow-the-bottom anchor (`Timeline.tsx:70-87`) has nothing to do. `height` plus `overflow: hidden` pins the shell to the viewport; the Timeline (`flex flex-1 flex-col overflow-y-auto`, inside `main`'s `flex flex-1 flex-col overflow-hidden`) becomes the only scroller, which is what §4.4/§4.6 and the auto-scroll assume.

- [ ] **Step 1: Reproduce with the headless-Chrome fixture (jsdom cannot)**

Write `/tmp/pwa1/layout/repro.html` if it is not there:

```html
<!doctype html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  html, body { height: 100%; margin: 0; overscroll-behavior: none; }
  #root { display: flex; flex-direction: column; min-height: var(--app-height, 100dvh); }
  main { position: relative; display: flex; flex: 1; flex-direction: column; overflow: hidden; }
  header { padding-top: 72px; }
  #timeline { position: relative; display: flex; flex: 1; flex-direction: column; overflow-y: auto; }
  footer { padding: 12px 24px 34px; }
</style></head>
<body><div id="root"><main><header>Listening, sir.</header>
<div id="timeline"><div id="content"></div></div>
<footer><input placeholder="Ask or tell Alfred"></footer></main></div>
<script>
  const c = document.getElementById("content");
  for (let i = 0; i < 100; i++) { const p = document.createElement("p"); p.textContent = "row " + i; p.style.height = "50px"; c.appendChild(p); }
  const t = document.getElementById("timeline");
  const pre = document.createElement("pre");
  pre.textContent = "RESULT root=" + document.getElementById("root").offsetHeight
    + " docScrollable=" + (document.documentElement.scrollHeight > window.innerHeight)
    + " timeline=" + t.clientHeight + "/" + t.scrollHeight;
  document.body.appendChild(pre);
</script></body></html>
```

Run:
```bash
~/.cache/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell --no-sandbox --disable-gpu --window-size=390,844 --dump-dom file:///tmp/pwa1/layout/repro.html | grep -o 'RESULT[^<]*'
```
Expected (the bug): `RESULT root=5xxx docScrollable=true timeline=NNNN/NNNN` with the two timeline numbers equal.

(If the `chromium_headless_shell-*` directory has a different number, use whatever `ls ~/.cache/ms-playwright/` shows.)

- [ ] **Step 2: Apply the fix in the fixture and re-measure**

Change the fixture's `#root` line to `height: var(--app-height, 100dvh); overflow: hidden;` and re-run the command.
Expected: `RESULT root=844 docScrollable=false timeline=613/5xxx` — the timeline overflows.

- [ ] **Step 3: Apply the fix in `web/src/index.css`**

Replace lines 123–131 with:

```css
  /* Constraint §4.4: 100vh is wrong in Safari. --app-height is the innerHeight
     mirror; the fallback keeps the shell full-height before the JS has run. One
     declaration, not two — an undefined var() is invalid at computed-value time
     and would fall all the way back to `auto`, not to a previous declaration.

     `height`, not `min-height`, and `overflow: hidden`: a min-height root grows
     with the thread, so the document becomes the scroller, the header and the
     composer scroll away with it, and the Timeline never overflows — its
     follow-the-bottom anchor has nothing to scroll. Measured at 390x844 with a
     5000px thread: root 5305px and the Timeline's scrollHeight equal to its
     clientHeight. A fixed height makes the shell the viewport and the Timeline
     the only scroller. */
  #root {
    display: flex;
    flex-direction: column;
    height: var(--app-height, 100dvh);
    overflow: hidden;
  }
```

- [ ] **Step 4: Check the gates still fit in a fixed-height shell**

`web/src/shell/Gate.tsx` is `flex flex-1 flex-col overflow-hidden`, the setup gate's list has `max-h-[40dvh] overflow-y-auto`, and the auth gate is `flex flex-1 flex-col`. Read each (`grep -n "className" web/src/shell/*.tsx web/src/gates/*.tsx`) and confirm nothing relies on the document scrolling — every tall region must have its own `overflow-y-auto`. If one does not, add `overflow-y-auto min-h-0` to that region and say so in the commit body.

- [ ] **Step 5: Run the suite and lint**

```bash
cd web && npx vitest run && npm run lint && npm run build
```
Expected: 42 files / 593 tests pass; lint and build clean. (Nothing here can fail from a CSS change; this is the regression floor.)

- [ ] **Step 6: Update the constraints paragraph in `docs/web-frontend.md`**

Replace the bullet at lines 481–483:

```markdown
- **`100vh` is wrong in Safari.** `installViewportVars()` writes `--app-height` from
  `innerHeight` and `--keyboard-inset` from `visualViewport`; `#root` is sized from the
  former — `height`, not `min-height`, with `overflow: hidden`, so the shell is the
  viewport and the Timeline is the only scroller (a growing root scrolls the document
  instead, taking the header and composer with it and leaving nothing for the
  Timeline's follow-the-bottom anchor to scroll) — and the composer pays for the
  keyboard once, via `.pb-keyboard`.
```

- [ ] **Step 7: Commit**

```bash
git add web/src/index.css docs/web-frontend.md
git commit -m "fix(web): pin the shell to the viewport so the timeline is the only scroller

#root was min-height, so it grew with the thread: the document scrolled, the
header and composer went with it, and the Timeline never overflowed, which left
the follow-the-bottom anchor with nothing to scroll. height + overflow hidden
makes the shell the viewport. Measured at 390x844 in headless Chrome: root
5305px -> 844px, timeline 613/5074 overflows."
```

---

### Task 2: Notification rows show the body

**Files:**
- Modify: `web/src/lib/history.ts` (`notificationItem`)
- Modify: `web/src/room/useRoom.ts:305-316` (live notification row)
- Test: `web/src/lib/history.test.ts`, `web/src/room/useRoom.test.tsx`

Why: the row's `text` is the title. For a routine suggestion that is the literal string "Routine Suggestion"; the actual suggestion is in `body`. The read-back pairing key for act rows is `act:${hue}:${text}` (`useRoom.ts:readBackKey`), so the history row and the live row must derive `text` the same way — both use `body`, falling back to `title` when the body is empty. `meta` (`HH:MM · source · urgency`) is unchanged.

- [ ] **Step 1: Write the failing history tests**

In `web/src/lib/history.test.ts`, inside `describe("toTimelineItems", …)`, replace the test `"renders a notification as its title, in the NT hue"` with:

```ts
  it("renders a notification as its body, in the NT hue", () => {
    const nt = items.find((item) => item.kind === "act" && item.hue === 255);
    expect(nt).toEqual({
      kind: "act",
      id: "nt:1788801600000-0",
      at: "2026-09-07T18:20:00",
      hue: 255,
      text: "The door sensor saw it at 18:20.",
      meta: "18:20 · trigger:trg_parcel · important",
    });
  });

  it("falls back to the title when a notification has no body", () => {
    const [nt] = toTimelineItems({
      ...EMPTY,
      notifications: [
        {
          id: "1-0",
          event: { timestamp: "2026-09-07T18:20:00", title: "Routine Suggestion", body: "" },
        },
      ],
    });
    expect(nt.kind === "act" && nt.text).toBe("Routine Suggestion");
  });
```

The existing `"files an unlabelled notification under the house, informational"` test has no `body` and asserts only `meta`; leave it.

- [ ] **Step 2: Run to verify they fail**

```bash
cd web && npx vitest run src/lib/history.test.ts
```
Expected: the first new test fails with `text: "Your parcel arrived"` received.

- [ ] **Step 3: Implement in `history.ts`**

In `notificationItem`, after the `pending_action_id` guard:

```ts
  const source = str(entry.event.source) ?? "house";
  const urgency = str(entry.event.urgency) ?? "informational";
  // The body is the message; the title is a label ("Routine Suggestion"). The
  // live row in useRoom derives its text the same way — the read-back pairing
  // key is `act:hue:text`, so the two must agree.
  const body = str(entry.event.body);
  return {
    kind: "act",
    id: `nt:${entry.id}`,
    at,
    hue: 255,
    text: body ?? title,
    meta: `${hhmm(at)} · ${source} · ${urgency}`,
  };
```

- [ ] **Step 4: Run the history tests**

```bash
cd web && npx vitest run src/lib/history.test.ts
```
Expected: all pass.

- [ ] **Step 5: Write the failing useRoom tests**

In `web/src/room/useRoom.test.tsx`:

(a) In `"turns a notification into a quiet act row"`, change the text assertion to:
```ts
    expect(act_.kind === "act" && act_.text).toBe("Collection moved to Friday.");
```

(b) Add after it:
```ts
  it("reads a bodiless notification by its title", () => {
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Routine Suggestion",
        body: "",
        urgency: "informational",
        notification_id: "ntf-10",
        metadata: {},
      }),
    );

    const act_ = result.current.items.find((item) => item.kind === "act")!;
    expect(act_.kind === "act" && act_.text).toBe("Routine Suggestion");
  });
```

(c) In `"lets the history's copy of a notification replace the live one"` and `"does not let a reflex answer for a notification that says the same"`, the history copies must say what the stream copy would now say: change both `text: "Bins go out tonight"` to `text: "Collection moved to Friday."`. (The second test still expects 2 acts: the reflex hue differs.)

- [ ] **Step 6: Run to verify they fail**

```bash
cd web && npx vitest run src/room/useRoom.test.tsx
```
Expected: (a) and (c)'s first test fail (live text is still the title); (b) passes already, which is fine — it pins the fallback.

- [ ] **Step 7: Implement in `useRoom.ts`**

Replace `text: msg.title,` in the live notification row with:

```ts
            // The body is the message, the title a label; empty body → title.
            // Same rule as history.ts::notificationItem, which the read-back
            // pairing (`act:hue:text`) depends on.
            text: msg.body || msg.title,
```

- [ ] **Step 8: Run the suite and lint**

```bash
cd web && npx vitest run && npm run lint
```
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add web/src/lib/history.ts web/src/lib/history.test.ts web/src/room/useRoom.ts web/src/room/useRoom.test.tsx
git commit -m "fix(web): notification rows show the body, not the title

A routine suggestion read \"Routine Suggestion\" and nothing else; the
suggestion is in the body. History and live rows derive the text the same
way (body, else title) so the read-back pairing key still matches."
```

---

### Task 3: The overview reports the session idle timeout

**Files:**
- Modify: `core/channels/admin_api.py:186-200` (`_base_overview`), `:334-336` (`overview`)
- Modify: `tests/core/channels/test_admin_api.py` (`test_overview_shape`, `test_overview_reports_redis_down`, one new test)
- Modify: `web/src/lib/types.ts:15-31` (`Overview`), `web/src/test/fixtures.ts` (`overviewFixture`)
- Modify: `docs/admin-api.md:95-110`

Why: the client windows the Room to the server's chat session and rotates its session id at the same boundary (Tasks 4, 5). It must not hard-code 30 when the server reads `SESSION_TIMEOUT_MINUTES`. `_base_overview()` is the single source of the Overview shape and the degraded path returns it as-is, so the field goes there — it comes from config, not Redis, and is right even when Redis is down.

- [ ] **Step 1: Write the failing tests**

In `tests/core/channels/test_admin_api.py`:

(a) At the end of `test_overview_shape` add:
```python
    assert data["session"] == {"idle_minutes": 30}
```

(b) At the end of `test_overview_reports_redis_down` add:
```python
    assert data["session"] == {"idle_minutes": 30}
```

(c) After `test_overview_reports_redis_down`, add:
```python
@pytest.mark.parametrize("redis_up", [True, False], ids=["healthy", "degraded"])
def test_overview_session_idle_follows_config(monkeypatch: Any, redis_up: bool) -> None:
    """The SPA windows the Room to the server's session; it must read the real value —
    including when Redis is down, since this is config, not Redis."""
    monkeypatch.setenv("SESSION_TIMEOUT_MINUTES", "10")
    r = _overview_redis()
    if not redis_up:
        r.ping = AsyncMock(side_effect=ConnectionError("down"))
    client = make_admin_client(r)

    resp = client.get("/api/admin/overview")
    assert resp.status_code == 200
    assert resp.json()["session"] == {"idle_minutes": 10}
```
The degraded case is the one the feature exists for: an implementation that hard-codes
30 in `_base_overview` and overwrites `session` after the ping passes `[healthy]` and
fails `[degraded]`.

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/core/channels/test_admin_api.py -q -k "overview"
```
Expected: four failures with `KeyError: 'session'` (the new test is two cases).

- [ ] **Step 3: Implement**

In `core/channels/admin_api.py`, give `_base_overview` the one scalar it needs, and add the field:

```python
def _base_overview(*, idle_minutes: int) -> dict[str, Any]:
    """Full Overview shape with placeholders — the single source of truth for the
    field set. The frontend `Overview` type requires every key, so both the degraded
    path (returned as-is) and the happy path (which overwrites what it can compute)
    build from this, and neither can drift into a partial payload.

    ``session`` comes from config, not Redis, so it is real on both paths — the client
    needs the idle timeout most when the house is degraded, and must not guess it."""
    return {
        "redis": {"connected": False},
        "cost": None,
        "dnd": {"active": False},
        "counts": {"sessions": 0, "devices": 0, "deferred": 0, "triggers": 0},
        "streams": {},
        "inference": {"ollama": False, "lmstudio": False},
        "reflex": {"model": None, "last_ms": None, "p50_ms": None},
        "librarian": {"last_run_at": None, "reviewed": None, "next_run_at": None},
        "session": {"idle_minutes": idle_minutes},
    }
```

In `overview()`, move the config read to the top and pass it in — replace:
```python
        r = _redis(request)
        out = _base_overview()
```
with:
```python
        r = _redis(request)
        # Read up front so the degraded path carries it too. A malformed value can't
        # 500 us here: core/channels/__main__.py loads the same config before
        # create_app, so the process would never have started.
        cfg = AlfredConfig.from_env()
        out = _base_overview(idle_minutes=cfg.session_timeout_minutes)
```
and delete the later `cfg = AlfredConfig.from_env()` line (before `out["inference"] = …`).

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/core/channels/test_admin_api.py -q
```
Expected: all pass.

- [ ] **Step 5: Mirror the type on the client**

In `web/src/lib/types.ts`, add to `Overview` after `librarian?`:
```ts
  /** The chat session's idle timeout (`SESSION_TIMEOUT_MINUTES`), so the client never guesses. */
  session: { idle_minutes: number };
```
(Required, not optional: `_base_overview` always carries it.)

In `web/src/test/fixtures.ts`, add to `overviewFixture`:
```ts
  session: { idle_minutes: 30 },
```

Run `cd web && npx tsc -b && npx vitest run` — expected clean (any other `Overview` literal the compiler flags gets the same line).

- [ ] **Step 6: Document**

In `docs/admin-api.md`, after the last `librarian.*` bullet add:
```markdown
- `session.idle_minutes` — `SESSION_TIMEOUT_MINUTES`: how long a chat session survives without a turn. Config, not Redis, so it is present on the degraded path too. Served so the web client can window the Room to the current session without hard-coding 30
```

- [ ] **Step 7: Commit**

```bash
git add core/channels/admin_api.py tests/core/channels/test_admin_api.py web/src/lib/types.ts web/src/test/fixtures.ts docs/admin-api.md
git commit -m "feat(admin-api): report the chat session idle timeout in the overview

session.idle_minutes mirrors SESSION_TIMEOUT_MINUTES so the web client can
window the Room to the current session without guessing the value."
```

---

### Task 4: The Room shows the current session

**Files:**
- Modify: `web/src/lib/history.ts` (`CONVERSATION_GAP_MS` → exported `SESSION_IDLE_MS`; new `sessionWindow`; `withDividers` takes a required `idleMs`)
- Modify: `web/src/room/useRoom.ts` (`idleMs` option; apply `sessionWindow` in the items memo, and pass `idleMs` to `withDividers`)
- Modify: `web/src/room/useOverview.ts` (`sessionIdleMs`)
- Modify: `web/src/room/Room.tsx:52` (pass `idleMs`)
- Modify: `web/src/test/fixtures.ts` (comment only: the 20:52/21:14 turns are 22 min apart — one session, no gap divider)
- Test: `web/src/lib/history.test.ts`, `web/src/room/useRoom.test.tsx`, `web/src/room/useOverview.test.tsx`, `web/src/App.test.tsx`

Decision (user, 2026-09-09): the Room shows **the current session only** — the turns since the last silence of `idle_minutes` or more, which is exactly what Alfred has in context — and nothing older. After a break the Room opens empty; older turns come back in the Activity view (phase 2). Satellite turns are included. The house's own rows (notifications, reflex acts) are not conversation and stay for the day (`earlier today`), or from the session start if that is earlier. Tombstones, live rows and the unsent queue are never windowed.

The window uses `now` from `useRoom`, which refreshes on `visibilitychange` — so a thread you are looking at does not vanish under you at minute 30; it is gone when you come back.

- [ ] **Step 1: Write the failing `sessionWindow` tests**

In `web/src/lib/history.test.ts`, add `sessionWindow, SESSION_IDLE_MS` to the import from `./history`, and add a new describe after `describe("withDividers", …)`:

```ts
describe("sessionWindow", () => {
  const now = new Date(2026, 8, 7, 21, 30);
  // Every assertion below reads the ids, so each row carries its own.
  const you = (id: string, at: string): TimelineItem => ({
    kind: "you",
    id: `you:${id}`,
    at,
    text: id,
    state: "sent",
  });
  const alfred = (id: string, at: string): TimelineItem => ({
    kind: "alfred",
    id: `alfred:${id}`,
    at,
    text: id,
    actions: [],
  });
  const act = (id: string, at: string): TimelineItem => ({
    kind: "act",
    id: `nt:${id}`,
    at,
    hue: 255,
    text: id,
    meta: "",
  });
  const ids = (items: TimelineItem[]) => items.map((item) => item.id);
  const kept = (items: TimelineItem[], at = now) => ids(sessionWindow(items, at, SESSION_IDLE_MS));

  it("keeps the turns since the last long silence and drops the rest", () => {
    const items = [
      you("a", "2026-09-07T19:00:00"),
      alfred("b", "2026-09-07T19:00:04"),
      // 31 minutes of silence: a new session starts here.
      you("c", "2026-09-07T19:31:04"),
      alfred("d", "2026-09-07T19:31:10"),
      you("e", "2026-09-07T21:10:00"), // 99 min later — the newest session
      alfred("f", "2026-09-07T21:10:05"),
    ];
    expect(kept(items)).toEqual(["you:e", "alfred:f"]);
  });

  it("shows no conversation when the newest turn is already idle", () => {
    const items = [you("a", "2026-09-07T20:59:00"), alfred("b", "2026-09-07T21:00:00")];
    expect(sessionWindow(items, now, SESSION_IDLE_MS)).toEqual([]);
  });

  it("measures the silence from the newest turn, not from now", () => {
    // 29 minutes ago: still live, and everything chained to it within 30 min stays.
    const items = [you("a", "2026-09-07T20:32:00"), alfred("b", "2026-09-07T21:01:00")];
    expect(kept(items)).toEqual(["you:a", "alfred:b"]);
  });

  it("keeps the house's rows for the day even with no conversation", () => {
    const items = [act("dawn", "2026-09-07T06:10:00"), act("old", "2026-09-06T23:59:00")];
    expect(kept(items)).toEqual(["nt:dawn"]);
  });

  it("keeps the house's rows back to a session that began yesterday", () => {
    const items = [
      you("a", "2026-09-06T23:50:00"),
      alfred("b", "2026-09-06T23:50:04"),
      act("late", "2026-09-06T23:55:00"),
    ];
    const justAfterMidnight = new Date(2026, 8, 7, 0, 5);
    // The act is yesterday's, so only the session's start can keep it.
    expect(kept(items, justAfterMidnight)).toEqual(["you:a", "alfred:b", "nt:late"]);
  });

  it("honours a different idle timeout", () => {
    const items = [you("a", "2026-09-07T21:15:00"), you("b", "2026-09-07T21:27:00")];
    expect(ids(sessionWindow(items, now, 10 * 60 * 1000))).toEqual(["you:b"]);
    expect(ids(sessionWindow(items, now, 20 * 60 * 1000))).toEqual(["you:a", "you:b"]);
  });

  it("treats a turn stamped after now as current", () => {
    // The server stamps history; a phone a few seconds behind must not lose it.
    const items = [you("a", "2026-09-07T21:30:03")];
    expect(kept(items)).toEqual(["you:a"]);
  });

  it("does not let a turn with an unreadable stamp stand in for one", () => {
    // NaN compares false against everything, so an unreadable row must be
    // stepped over — not treated as a turn that bridges the silence.
    const items = [you("a", "2026-09-07T10:00:00"), you("nope", "not a date")];
    expect(sessionWindow(items, now, SESSION_IDLE_MS)).toEqual([]);
  });

  it("cuts at the same silence the gap divider draws on", () => {
    const turns = [you("a", "2026-09-07T21:00:00"), you("b", "2026-09-07T21:30:00")];

    // Exactly SESSION_IDLE_MS apart: the window keeps only the newer turn, and
    // the divider that would separate the two is drawn at the same instant.
    expect(kept(turns)).toEqual(["you:b"]);
    expect(
      withDividers(turns, now, SESSION_IDLE_MS).filter((item) => item.kind === "divider"),
    ).toHaveLength(2);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd web && npx vitest run src/lib/history.test.ts
```
Expected: fails to compile — `sessionWindow` / `SESSION_IDLE_MS` are not exported.

- [ ] **Step 3: Implement in `history.ts`**

Replace the constant:
```ts
/**
 * The client's fallback for the server's chat session idle timeout
 * (`SESSION_TIMEOUT_MINUTES`, `shared/config.py`, default 30), until the overview
 * reports the real one (`Overview.session.idle_minutes` → `sessionIdleMs`).
 */
export const SESSION_IDLE_MS = 30 * 60 * 1000;
```

Add after `startOfDay`:

```ts
/**
 * The current session: the turns since the last silence of `idleMs` or more,
 * walked back from the newest turn — which is what Alfred still has in context.
 * A newest turn that is itself `idleMs` old means no session, and no turns.
 * `items` must be sorted by `at`, as `toTimelineItems` returns them: the walk
 * relies on it.
 *
 * The house's own rows (notifications, reflex acts) are not conversation; they
 * stay for the day, or from the session's start if that came earlier. Everything
 * else (tombstones, live rows, the unsent queue) is the caller's, never windowed
 * here.
 *
 * `now` is only the reference for "idle" and "today": a turn stamped after it
 * (the server's clock, a phone a few seconds behind) is current.
 */
export function sessionWindow(items: TimelineItem[], now: Date, idleMs: number): TimelineItem[] {
  const stamps = items.map((item) => Date.parse(item.at));
  let start: number | null = null;
  let next = now.getTime();
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind !== "you" && item.kind !== "alfred") continue;
    const at = stamps[i];
    // An unreadable stamp is not a turn: standing in for one would swallow
    // the silence on both sides of it.
    if (Number.isNaN(at)) continue;
    if (next - at >= idleMs) break;
    start = at;
    next = at;
  }

  const today = startOfDay(now);
  const houseSince = start === null ? today : Math.min(today, start);
  // Both comparisons are false for a NaN stamp, which is what drops the
  // unreadable row itself — and with it the `divider:day:NaN` the thread would
  // otherwise open on.
  return items.filter((item, i) => {
    const at = stamps[i];
    if (item.kind === "you" || item.kind === "alfred") return start !== null && at >= start;
    return at >= houseSince;
  });
}
```

`idleMs` is required — the caller always knows it, and a default would let a call
site silently disagree with the Room. Then give `withDividers` the same timeout,
required for the same reason, so the Room cannot draw `new conversation ·` inside
the one session it is showing (at `idle_minutes: 60` the hard-coded half hour
would):

```ts
export function withDividers(items: TimelineItem[], now: Date, idleMs: number): TimelineItem[] {
```
— comparing `stamp - lastTurnAt >= idleMs`, with its JSDoc's "a half-hour silence"
reworded to "a silence of the session's idle timeout". Every call in the
`withDividers` describe now names the timeout, through one local helper at the top
of it:

```ts
  const divided = (items: TimelineItem[], idleMs = SESSION_IDLE_MS) =>
    withDividers(items, now, idleMs);
```

and add:

```ts
  it("draws the gap at the timeout it is given, not at the default", () => {
    // Twenty minutes: one conversation at the default, two when the house
    // idles out at ten — the Room must not split the session it is showing.
    const turns = [turnAt("2026-09-07T21:00:00"), turnAt("2026-09-07T21:20:00")];
    const dividers = (items: TimelineItem[], idleMs?: number) =>
      divided(items, idleMs).filter((item) => item.kind === "divider").length;

    expect(dividers(turns)).toBe(1);
    expect(dividers(turns, 10 * 60 * 1000)).toBe(2);
  });
```

- [ ] **Step 4: Run the history tests**

```bash
cd web && npx vitest run src/lib/history.test.ts
```
Expected: all pass.

- [ ] **Step 5: Apply the window in `useRoom.ts` and write its tests**

In `useRoom.ts`:

(a) Import: `import { SESSION_IDLE_MS, sessionWindow, withDividers, type TimelineItem } from "@/lib/history";`

(b) Add to `UseRoomOptions`:
```ts
  /**
   * The server's session idle timeout, from `Overview.session.idle_minutes`
   * (`sessionIdleMs`). The history is windowed to the current session
   * (`sessionWindow`); live rows and tombstones never are.
   */
  idleMs?: number;
```

(c) Signature: `export function useRoom({ history, tombstones, idleMs = SESSION_IDLE_MS }: UseRoomOptions): RoomValue {`

(d) The items memo:
```ts
  const items = useMemo(() => {
    // Windowed here, after the read-back has been settled against the full
    // history: a turn the house read back and then let go idle is simply gone,
    // which is the point. `now` moves on visibilitychange, so an open thread
    // does not vanish at minute thirty — it is gone when you come back.
    const merged = [
      ...sessionWindow(history ?? [], now, idleMs),
      ...(tombstones ?? []),
      ...live,
    ].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
    return withDividers(merged, now, idleMs);
  }, [history, tombstones, live, now, idleMs]);
```

In `useRoom.test.tsx`:

(e) The fixtures are dated 2026-09-01/07 and the real clock is later, so every history row would now be idle. Pin the clock: add a module constant and set it in the top-level `beforeEach`:
```ts
// Every history fixture below is dated against this: `historyRow` inside the
// session window (`sessionWindow`), `idleTurn` before it — the tests that need
// the clock to move set it themselves.
const NOW = new Date("2026-09-07T21:05:00");
```
and in `beforeEach` add `vi.setSystemTime(NOW);` as the first line. (`vi.setSystemTime` without `useFakeTimers` mocks `Date` only; `afterEach`'s `vi.useRealTimers()` restores it, and `vi.useFakeTimers()` inside a test starts from the mocked date.)

(f) Re-date `historyRow` to `at: "2026-09-07T20:52:06"` and rewrite its comment:
```ts
// Thirteen minutes before NOW: inside the session window, and before the live
// rows, which are stamped with the (mocked) clock — useRoom sorts by timestamp.
```

(g) `"relabels the day when the app returns after midnight"`: the row must still be in session at 00:01, so use a late row:
```ts
  it("relabels the day when the app returns after midnight", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-07T23:59:00"));
    const lateRow = { ...historyRow, at: "2026-09-07T23:50:00" };
    const { result } = renderRoom({ history: [lateRow], online: true });
    expect(result.current.items[0]).toMatchObject({ kind: "divider", label: "earlier today" });

    vi.setSystemTime(new Date("2026-09-08T00:01:00"));
    act(() => void document.dispatchEvent(new Event("visibilitychange")));

    expect(result.current.items[0]).toMatchObject({ kind: "divider", label: "yesterday" });
  });
```

(h) Add a new describe at the end of the file:
```ts
describe("useRoom — the session window", () => {
  const idleTurn: TimelineItem = {
    kind: "you",
    id: "you:idle",
    at: "2026-09-07T20:15:00", // 50 min before NOW, and 37 before `historyRow`
    text: "Lock up for the night.",
    state: "sent",
  };
  const dawnAct: TimelineItem = {
    kind: "act",
    id: "nt:dawn",
    at: "2026-09-07T06:10:00",
    hue: 255,
    text: "The coffee machine is on.",
    meta: "06:10 · routine · informational",
  };

  it("opens empty after a break, keeping only the house's rows for the day", () => {
    const { result } = renderRoom({ history: [idleTurn, dawnAct], online: true });
    expect(kinds(result.current.items)).toEqual(["act"]);
  });

  it("keeps the current session", () => {
    const { result } = renderRoom({ history: [idleTurn, historyRow], online: true });
    expect(kinds(result.current.items)).toEqual(["alfred"]);
  });

  it("follows the server's idle timeout", () => {
    const { result } = renderRoom({ history: [idleTurn], idleMs: 60 * 60 * 1000, online: true });
    expect(kinds(result.current.items)).toEqual(["you"]);
  });

  it("never windows what was sent from this phone", () => {
    localStorage.setItem(
      UNSENT_KEY,
      JSON.stringify([
        { kind: "you", id: "you:cold", at: "2026-09-07T19:00:00", text: "held over", state: "unsent" },
      ]),
    );
    const { result } = renderRoom({ history: [idleTurn], online: false });
    expect(kinds(result.current.items)).toEqual(["you"]);
    expect(result.current.items.find((item) => item.kind === "you")).toMatchObject({ text: "held over" });
  });

  it("does not drop the thread under you before the app has been away", () => {
    // Open at 21:05 with a live session; the clock passes 30 min with no
    // visibilitychange: the rows stay until the app returns.
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    const { result } = renderRoom({ history: [historyRow], online: true });
    act(() => void vi.advanceTimersByTime(40 * 60 * 1000));
    expect(kinds(result.current.items)).toEqual(["alfred"]);

    act(() => void document.dispatchEvent(new Event("visibilitychange")));
    expect(result.current.items).toEqual([]);
  });
});
```

Check `UNSENT_KEY` is already imported at the top (it is: `import { UNSENT_KEY, useRoom, type UseRoomOptions } from "./useRoom";`).

- [ ] **Step 6: Run the useRoom tests**

```bash
cd web && npx vitest run src/room/useRoom.test.tsx
```
Expected: all pass, including every pre-existing test (the `readBack` rows at 21:00:00/21:00:04 and `"leaves a client-made error row alone"` sit inside NOW's window). If any pre-existing test fails, the fix is in the test's dates, not by loosening `sessionWindow` — re-date the row to within 30 minutes before the clock that test uses.

- [ ] **Step 7: Read the timeout from the overview**

In `web/src/room/useOverview.ts`, add:

```ts
import { SESSION_IDLE_MS } from "@/lib/history";

/**
 * The server's session idle timeout in ms, or the client's default until the
 * overview has answered (or if it reports nonsense — a zero would window
 * everything away). The house always sends `session`; the guard is for a cached
 * shell meeting a server from before it did, not for the current contract.
 */
export function sessionIdleMs(overview: Overview | undefined): number {
  const minutes = overview?.session?.idle_minutes;
  return typeof minutes === "number" && minutes > 0 ? minutes * 60_000 : SESSION_IDLE_MS;
}
```

Add a describe block to the existing `web/src/room/useOverview.test.tsx` (extend its imports as needed):

```ts
import { describe, expect, it } from "vitest";
import { SESSION_IDLE_MS } from "@/lib/history";
import type { Overview } from "@/lib/types";
import { overviewFixture } from "@/test/fixtures";
import { sessionIdleMs } from "./useOverview";

describe("sessionIdleMs", () => {
  it("reads the server's idle timeout in minutes", () => {
    expect(sessionIdleMs({ ...overviewFixture, session: { idle_minutes: 10 } })).toBe(600_000);
  });

  it("falls back to the default before the overview has answered", () => {
    expect(sessionIdleMs(undefined)).toBe(SESSION_IDLE_MS);
  });

  // The house always sends `session`; a cached shell can still meet a server
  // from before it did.
  it("falls back to the default when the overview has no session at all", () => {
    const overview = { ...overviewFixture, session: undefined as unknown as Overview["session"] };
    expect(sessionIdleMs(overview)).toBe(SESSION_IDLE_MS);
  });

  it.each([0, -5, Number.NaN])("falls back to the default for %s minutes", (minutes) => {
    expect(sessionIdleMs({ ...overviewFixture, session: { idle_minutes: minutes } })).toBe(
      SESSION_IDLE_MS,
    );
  });
});
```


In `web/src/room/Room.tsx`:
- import: `import { isFirstRun, sessionIdleMs, useOverview } from "@/room/useOverview";`
- after `const { data: overview } = useOverview();` add `const idleMs = sessionIdleMs(overview);`
- change line 52 to `const room = useRoom({ history: historyItems, tombstones, idleMs });`

- [ ] **Step 8: Integration test in `App.test.tsx`**

Add to `describe("App", …)` after `"shows the thread the four streams describe"`:

```ts
  it("shows only the current session, and the house's rows for the day", async () => {
    // 21:20 local: the fixture's 20:52–21:14 turns are one live session, the
    // 18:20 parcel notification is today's, and yesterday's request is not.
    vi.setSystemTime(new Date(2026, 8, 7, 21, 20));
    routes["/api/admin/streams/user_requests?count=50"] = {
      entries: [...userRequestsPage.entries, ...yesterdayRequestPage.entries],
      next_before: null,
    };
    render(<App />);

    expect(await screen.findByText("What have I got tomorrow morning?")).toBeInTheDocument();
    expect(screen.getByText("Your parcel arrived")).toBeInTheDocument();
    expect(screen.queryByText("Lock up for the night.")).toBeNull();
  });
```

Import `yesterdayRequestPage` from `@/test/fixtures` (check the existing import line and extend it). Note the `routes` object is reset per test — confirm by reading `beforeEach` (line ~95); if it is module-level and mutated, follow the pattern the test at line ~218 uses.

Wait — after Task 2 the parcel row's text is its body. Assert the body instead:
```ts
    expect(screen.getByText("The door sensor saw it at 18:20.")).toBeInTheDocument();
```
and in `"shows the thread the four streams describe"` change `screen.getByText("Your parcel arrived")` to the same body text (Task 2 changed what the row says; that test is red until this is done — do it in this task's first run).

- [ ] **Step 9: Run the suite, lint, build**

```bash
cd web && npx vitest run && npm run lint && npm run build
```
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add web/src/lib/history.ts web/src/lib/history.test.ts web/src/room/useRoom.ts web/src/room/useRoom.test.tsx web/src/room/useOverview.ts web/src/room/useOverview.test.tsx web/src/room/Room.tsx web/src/App.test.tsx web/src/test/fixtures.ts
git commit -m "feat(web): the Room shows the current session only

The history read is fifty rows per stream with no age bound, so the Room
opened on a month of conversation. sessionWindow keeps the turns since the
last silence of the server's idle timeout — what Alfred still has in
context — and the house's rows for the day; tombstones, live rows and the
unsent queue are never windowed. The timeout comes from the overview."
```

---

### Task 5: The stored session id rotates with the session

**Files:**
- Modify: `web/src/lib/chat-socket.ts`
- Modify: `web/src/lib/ws.ts` (hand the app the open before saying "online" — its own commit)
- Modify: `web/src/room/Room.tsx` (hand `idleMs` to the socket)
- Test: `web/src/lib/chat-socket.test.ts`, `web/src/lib/ws.test.ts`
- Modify: `web/src/App.test.tsx` (its `ChatSocket` mock gains `setIdleMs`)

Why: the client keeps `alfred.session` forever and sends it on the first message of every connection; the server (`core/channels/web_server.py:464-510`) restores whatever id it is given, so an expired session is resurrected under the old id with an empty context — harmless server-side, but the id no longer means "this conversation", and the Room's window (Task 4) says a new session began. The client now remembers when it last sent (`alfred.session-at`) and, on the first message of a connection, drops a stored id that has been idle for `idleMs` or longer in favour of the id the server assigned for this connection. A phone from before this change has no `session-at` and is treated as stale — one fresh session, the same cost as the `alfred_session_id` rename.

Limits: the server locks the id after the first message of a connection, so a session that idles out mid-connection cannot rotate until the socket reopens (iOS closes it in the background, so in practice it does). The server's own context is fresh either way.

- [ ] **Step 1: Write the failing tests**

`web/src/lib/chat-socket.test.ts` grows a third subject — `describe("ChatSocket sessions", …)` — and "payloads" keeps only the timezone/channel and audio tests, "frames" only the pong and status plumbing. The harness needs two additions: `onopen: () => void` on the hoisted `sockets` type (a reconnect is now observable behaviour), and a switch for a socket that will not take a frame:

```ts
const { sent, sockets, delivers } = vi.hoisted(() => ({
  sent: [] as Record<string, unknown>[],
  sockets: [] as {
    onmessage: (data: unknown) => void;
    onstatus: (s: string) => void;
    onopen: () => void;
  }[],
  /** Whether the fake socket is open enough to take a frame. See the failed-send test. */
  delivers: { value: true },
}));
```
— with `send(payload)` returning `false` without recording when `delivers.value` is false, and `delivers.value = true` in `beforeEach`. The two-`setItem` setup every session test wants goes in one helper, which keeps the literal keys:

```ts
/** A stored session last used `ageMs` ago; omit the age for one that predates the stamp. */
function storedSession(id: string, ageMs?: number): void {
  localStorage.setItem("alfred.session", id);
  if (ageMs !== undefined) {
    localStorage.setItem("alfred.session-at", new Date(Date.now() - ageMs).toISOString());
  }
}
```

The block covers: adopting the server's id when there is none (moved from "frames"); carrying a still-live stored id on the first message only; keeping a live stored id over the server's while remembering the server's; dropping an id idle for the timeout and taking the server's; treating a missing and an unreadable stamp as idle; following a `setIdleMs` timeout; stamping the send; forgetting a stale id before the server has spoken and adopting what it then says. Three carry the decisions worth reading:

```ts
  it("stamp every send as the session's last activity", () => {
    const socket = new ChatSocket();
    const before = Date.now();
    socket.sendText("hello");
    expect(Date.parse(localStorage.getItem("alfred.session-at")!)).toBeGreaterThanOrEqual(before);
  });

  it("do not spend the first message on a send that never left", () => {
    storedSession("s_9f2", 60_000);
    const stamp = localStorage.getItem("alfred.session-at");
    const socket = new ChatSocket();

    delivers.value = false;
    expect(socket.sendText("into the void")).toBe(false);
    expect(localStorage.getItem("alfred.session-at")).toBe(stamp);

    delivers.value = true;
    socket.sendText("the real first message");
    expect(sent[0].session_id).toBe("s_9f2");
  });

  it("offer the stored id again on the next connection, before the server names one", () => {
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_a" });
    socket.sendText("first");

    sockets[0].onopen();
    socket.sendText("after a reconnect");

    expect(sent[1].session_id).toBe("s_a");
  });
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd web && npx vitest run src/lib/chat-socket.test.ts
```
Expected: the live-id test passes; the stale/stamp tests fail (`session_id` still sent, no `alfred.session-at`, no `setIdleMs`).

- [ ] **Step 3: Implement**

```ts
import { SESSION_IDLE_MS } from "./history";
import type { ChatServerMessage } from "./types";
import { ReconnectingSocket, type SocketStatus } from "./ws";

/**
 * Every `localStorage` key is `alfred.<noun>` (see `THEME_KEY`, `DEVICE_KEY`,
 * `UNSENT_KEY`). This one was `alfred_session_id` in the client this one
 * replaced; the rename costs a phone one fresh conversation session.
 */
const SESSION_KEY = "alfred.session";
/**
 * ISO stamp of the last send. The server forgets a session after its idle
 * timeout and would otherwise restore the old id with an empty context; this is
 * how the client knows to let the id go too. A phone from before the stamp
 * existed reads as idle — one fresh session.
 */
const SESSION_AT_KEY = "alfred.session-at";

export class ChatSocket {
  private socket = new ReconnectingSocket("/ws");
  private listeners = new Set<(msg: ChatServerMessage) => void>();
  private firstMessageSent = false;
  /** The id the server assigned this connection; adopted when ours is gone or idle. */
  private assigned: string | null = null;
  private _sessionId: string | null = localStorage.getItem(SESSION_KEY);
  private idleMs = SESSION_IDLE_MS;

  onstatus: (s: SocketStatus) => void = () => {};

  constructor() {
    this.socket.onstatus = (s) => this.onstatus(s);
    this.socket.onopen = () => {
      this.firstMessageSent = false;
      // The previous connection's assigned id means nothing to this one, which
      // will announce its own — and ours must be offered again until it does.
      this.assigned = null;
    };
    this.socket.onmessage = (data) => {
      const msg = data as ChatServerMessage;
      // Keepalive plumbing. `lastMessageAt` on the socket already recorded it;
      // nothing above this layer should have to skip it.
      if (msg.type === "pong") return;
      if (msg.type === "session") {
        // The server assigns one per connection. Ours wins on the first send if
        // it is still live (web_server.py restores it); otherwise this is the id.
        this.assigned = msg.session_id;
        if (!this._sessionId) this.adopt(msg.session_id);
      }
      for (const fn of this.listeners) fn(msg);
    };
  }

  connect(): void { this.socket.connect(); }
  close(): void { this.socket.close(); }

  /** Read-only: the id and its two keys move together, in `adopt` and `forget` alone. */
  get sessionId(): string | null { return this._sessionId; }

  /**
   * Follow the server's own idle timeout (`Overview.session.idle_minutes`); the
   * Room sets it. A method rather than a field because `react-hooks/immutability`
   * refuses an assignment to an object a hook returned.
   */
  setIdleMs(ms: number): void { this.idleMs = ms; }

  private adopt(id: string): void {
    this._sessionId = id;
    localStorage.setItem(SESSION_KEY, id);
  }

  /** Let an idled-out id go, and take this connection's own if the server has named one. */
  private forget(): void {
    this._sessionId = null;
    localStorage.removeItem(SESSION_KEY);
    localStorage.removeItem(SESSION_AT_KEY);
    if (this.assigned) this.adopt(this.assigned);
  }

  /**
   * `!(… < …)` rather than `>= idleMs`: both are false for the NaN an unreadable
   * stamp parses to, and only this direction reads that as idle.
   */
  private sessionIdle(): boolean {
    const at = localStorage.getItem(SESSION_AT_KEY);
    return at === null || !(Date.now() - Date.parse(at) < this.idleMs);
  }

  private payload(type: "text" | "audio", content: string): Record<string, unknown> {
    const body: Record<string, unknown> = {
      type,
      content,
      channel: "web_pwa",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
    if (!this.firstMessageSent) {
      // Not only a builder: the server reads session_id from the first message
      // and locks it for the connection, so this branch is where a session turns
      // over. Idempotent, for the send below that does not leave.
      if (this._sessionId && this.sessionIdle()) this.forget();
      // Only ever carry an id the server does not already have.
      if (this._sessionId && this._sessionId !== this.assigned) body.session_id = this._sessionId;
    }
    return body;
  }

  private send(type: "text" | "audio", content: string): boolean {
    if (!this.socket.send(this.payload(type, content))) return false;
    // Only a frame that left is a first message, and only it is activity the
    // server can have recorded against the session.
    this.firstMessageSent = true;
    localStorage.setItem(SESSION_AT_KEY, new Date().toISOString());
    return true;
  }

  sendText(content: string): boolean { return this.send("text", content); }
  sendAudio(dataUrl: string): boolean { return this.send("audio", dataUrl); }

  listen(fn: (msg: ChatServerMessage) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}
```

Four points the sketch this replaced got wrong or left out:

- **`sessionId` is a getter over `_sessionId`.** The id, `SESSION_KEY` and `SESSION_AT_KEY` are one invariant, maintained only by `adopt` and `forget`; a public mutable field invited a fifth writer. Readers (the tests) are unchanged.
- **`assigned` is cleared in `onopen`.** The server assigns a fresh uuid per connection (`web_server.py`, before its receive loop), so the previous connection's id must not still be standing there — otherwise a stored id equal to it is suppressed by the `!== this.assigned` guard on the reconnect's first message, and the server silently starts a session the client never hears about.
- **`send` commits only on a frame that left.** `payload()` no longer sets `firstMessageSent` or stamps; `send()` does, after `socket.send()` returns true. A frame refused because the socket was not OPEN (`useRoom`'s unsent flush calls `sendText` ungated) must not burn the connection's one chance to carry `session_id`, nor record activity the server never saw. The drop-and-adopt stays in `payload()`, where it is idempotent on the retry.
- **`forget()` clears the stamp with the id.** They are one fact; leaving `alfred.session-at` behind was masked only by the unconditional re-stamp that `send()` now owns.

`payload()` only ever carries an id the server does not already have, so after a drop-and-adopt the first message carries no `session_id` — the wire is unchanged for the common case.

Import direction: `history.ts` imports `./api`, `./format`, `./types` — no cycle with `chat-socket.ts`.

- [ ] **Step 4: Fix the order of `onopen` and "online" in `web/src/lib/ws.ts`**

A pre-existing bug that this task's per-connection state made load-bearing, so it lands as its own commit, before the feature. `ws.onopen` announced `"online"` before calling `this.onopen()` — and `"online"` synchronously runs `ConnectionProvider`'s listeners, one of which (`useRoom.ts:245-252`) flushes the unsent queue through `chat.sendText`. The flush therefore ran before `ChatSocket.onopen` had reset `firstMessageSent`/`assigned`: the flushed frame was the connection's first message with no `session_id`, the server locked an id of its own, and the stamp was refreshed anyway, so the stored id could never idle out. Swap the two calls:

```ts
      this.startPing(ws);
      // Open first, "online" second: the app's online listeners send on it
      // (useRoom flushes the unsent queue), and ChatSocket clears its
      // per-connection session state in onopen — that has to happen before a
      // send, or the flushed frame is the connection's first message with no
      // session_id and the server locks an id of its own.
      this.onopen();
      this.onstatus("online");
```
`telemetry-socket.ts` only re-subscribes in its `onopen`, so it does not care which runs first. `web/src/lib/ws.test.ts` had no ordering coverage; add to `describe("ReconnectingSocket", …)`:

```ts
  it("hands the app the open before it says online", () => {
    // ChatSocket clears its per-connection session state in onopen, and the
    // "online" status is what makes useRoom flush the unsent queue — a flush
    // that ran first would be the new connection's first message with no
    // session_id, and the server locks a fresh id on that.
    const sock = new ReconnectingSocket("/ws/test");
    const order: string[] = [];
    sock.onopen = () => order.push("open");
    sock.onstatus = (s) => {
      if (s === "online") order.push("online");
    };
    sock.connect();
    FakeWebSocket.instances[0].open();
    expect(order).toEqual(["open", "online"]);
  });
```

- [ ] **Step 5: Hand the timeout to the socket**

In `web/src/room/Room.tsx`, destructure `chat` (`const { online, chatStatus, lastTrueAt, chat } = useConnection();`) and put the effect beside its twin, the `signal.setThinking(room.thinking)` effect — the two pokes at a long-lived singleton read as one pattern:

```ts
  // The socket lets a stored session id go at the same boundary the Room windows on.
  useEffect(() => {
    chat.setIdleMs(idleMs);
  }, [chat, idleMs]);
```

Not `chat.idleMs = idleMs`: `react-hooks/immutability`, in the recommended set this repo lints with (`web/eslint.config.js`), refuses an assignment to a value a hook returned. `PresenceSignal.setThinking` is the same shape for the same reason. `web/src/App.test.tsx` renders the Room behind a `ChatSocket` mock, so that mock needs `setIdleMs() {}` — a runtime need, not a type one. `useRoom.test.tsx` mounts the hook rather than the Room and needs nothing.

- [ ] **Step 6: Run the suite, lint, build**

```bash
cd web && npx vitest run && npm run lint && npm run build
```
Expected: all pass.

- [ ] **Step 7: Commit — the ws.ts ordering fix first, then the feature**

```bash
git add web/src/lib/ws.ts web/src/lib/ws.test.ts
git commit -m "fix(web): hand the app the socket's open before saying it is online

\"online\" is what makes useRoom flush the unsent queue, and it ran before
the onopen callback — so on a reconnect with a queued message the flushed
frame was the new connection's first message, sent before ChatSocket had
cleared its per-connection session state. It carried no session_id, the
server locked an id of its own, and the stamp was refreshed anyway, so the
stored id could never idle out."

git add web/src/lib/chat-socket.ts web/src/lib/chat-socket.test.ts web/src/room/Room.tsx web/src/App.test.tsx
git commit -m "feat(web): let the stored session id go when the session has idled out

The client kept alfred.session forever and re-sent it on every connection,
so the server resurrected expired sessions under the old id. It now stamps
its last send (alfred.session-at) and, on the first message of a
connection, drops an id idle for the server's timeout in favour of the one
the server assigned — the same boundary the Room windows on."
```

---

### Task 6: Docs, QA rows and the backlog entries

**Files:**
- Modify: `docs/web-frontend.md` (the file map's four stale one-liners, the Room's
  Timeline bullet, the client-state table, the `session_id` line and the session
  paragraph, the reconnect bullet)
- Modify: `docs/admin-api.md` (the `session.idle_minutes` bullet, now behaviour)
- Modify: `docs/superpowers/qa/2026-09-07-pwa-phase1-ios-checklist.md` (§4.4, §4.10 and a
  new section)
- Modify: `docs/backlog/low/pwa-phase1-followups.md` (append §8–§11)

Everything here is written from the source at the end of Task 5, not from this plan: the
prose below was drafted before Tasks 3–5 landed and several of its claims did not survive
them. Where the two disagree, the code wins.

- [ ] **Step 1: `docs/web-frontend.md`**

(a) The **Timeline** bullet gains the window: `sessionWindow` keeps the current session —
the turns since the last silence of `Overview.session.idle_minutes` (default 30 min,
satellite turns included, they land on the same two streams) — plus the house's own rows
for the day, or from the session's start if that came earlier; the live rows and the
Door's tombstones `useRoom` merges in are never windowed. After a break the Room opens
empty; older turns are the Activity view's (phase 2). A notification row's text is its
body.

(b) The `localStorage` row gains `alfred.session-at`. The trailing "every key is
`alfred.<noun>`" claim is kept but qualified — `session-at` is the one compound, and the
JSDoc at `chat-socket.ts:6` makes the same unqualified claim.

(c) The session paragraph is replaced outright; the version drafted here described a
client that keeps its id for ever, which Task 5 ended. What it says now, from
`chat-socket.ts`, `ws.ts`, `Room.tsx` and `useOverview.ts`:

- The server assigns an id per connection and pushes a `session` frame before the client
  speaks; a client holding no id adopts it. `alfred.session` is the id, `alfred.session-at`
  an ISO stamp of the last send; `adopt` and `forget` are the id's only writers, `forget`
  takes the stamp with it, and an id adopted but never sent on carries no stamp — which
  the next connection reads as idle.
- On the first message of a connection, a stored id whose stamp is idle for the timeout
  or longer is dropped (both keys) and the assigned id adopted. A missing or unreadable
  stamp reads as idle.
- `session_id` is carried only when the surviving stored id differs from the assigned one
  — when they agree the frame says nothing, because the server named that id itself.
- `firstMessageSent` and the stamp are committed in `send()`, after `socket.send()`
  returns true, so a refused send (the unsent queue calls `sendText` ungated) spends
  neither the connection's first message nor a record of activity the server never saw.
- The timeout is `Overview.session.idle_minutes` → `sessionIdleMs()` → `chat.setIdleMs()`
  in a `Room` effect, with `SESSION_IDLE_MS` (30 min) standing in until the overview
  answers — the same boundary the Room windows on.
- The server locks the id after the first message, so a session that idles out
  mid-connection turns over on the next open (iOS closes the socket in the background).

(d) Two neighbours the same work made false: the `session_id` rides-the-first-frame line
above the protocol block (now conditional both ways), and the reconnect bullet
`ChatSocket.onopen resets firstMessageSent…` (it also clears the assigned id, and `ws.ts`
calls `onopen()` before `onstatus("online")` so the unsent flush sees fresh per-connection
state).

(e) The file map's one-liners: `format.ts` gains `notificationText` (Task 2), `history.ts`
gains `sessionWindow` and `SESSION_IDLE_MS`, `useOverview.ts` gains `sessionIdleMs()`, and
`useRoom.ts` names the session window (Tasks 3–5).

- [ ] **Step 2: `docs/admin-api.md`**

The `session.idle_minutes` bullet was purpose-phrased ("Served so the web client can
window the Room…"). Restate it as behaviour now that the client does both halves: windows
the Room's timeline to the current session, and at the same boundary lets go of a stored
`alfred.session` idle that long.

- [ ] **Step 3: QA checklist**

Under `## §4.4 — 100vh is wrong`, after the existing first row:

```markdown
- [ ] Scroll a long thread: the headline and status line stay put at the top and the
      composer at the bottom; only the timeline moves
- [ ] Send a message with the thread scrolled to the bottom: the reply scrolls into view
      on its own
```

`## §4.10` needs two qualifiers rather than new rows: "background the app for five
minutes, return: the thread is intact" holds only inside the idle timeout, and "have the
house produce an act … present after returning" only for an act **today**.

A new section before the sign-off block. The third row is the one worth having — the
product consequence of Task 4, written down as expected behaviour so a tester does not
file it:

```markdown
## The Room's window

- [ ] Open the app after more than the session idle timeout away (30 min unless the
      house's `session.idle_minutes` says otherwise): the conversation is gone, and the
      first message starts a new session — Alfred does not refer back to it
- [ ] Today's notifications and reflex acts are still there, under `earlier today`
- [ ] **A completely blank Timeline is correct** on an established house that has
      produced no notification and no reflex act today: there is no conversation left to
      show and no house rows to keep, and the first-day greeting is deliberately gated on
      a first run (`isFirstRun`), so nothing fills the space. Empty is the intended Room
      after a break, not a failed history read
- [ ] Reopen within the timeout: the conversation is still there and continues
- [ ] Leave the app open and idle past the timeout, then send: a `new conversation ·
      HH:MM` divider separates the two and the older turns stay on screen — the window
      moves when the app is backgrounded and returned to, not at minute thirty. The
      session id follows the socket, so it turns over on the next reconnect rather than
      here
- [ ] A routine suggestion reads as the suggestion itself, not "Routine Suggestion"
```

- [ ] **Step 4: Backlog §8–§11**

§8 (routine suggestions cannot be accepted or declined) is the backend gap behind Task 2.
Grep before writing it — the draft of this plan got four things wrong:

| drafted | true |
|---|---|
| `core/routines/` | `core/memory/routines/` |
| state machine `candidate` → `active`/`rejected` | states are `candidate`, `active`, `dormant`, `archived`; no `rejected` |
| "nothing calls it from a channel" | true, and stronger: **nothing in the tree ever writes `active`** |
| "the consolidator's decay reads the table" | the consolidator runs the whole lifecycle (`_update_routine_lifecycle`): 3 misses → `dormant`, 30 days → `archived`, confidence < 0.3 → `archived`; `GET /api/admin/memory/routines` reads it too |

The rest holds: `check_routine_suggestions` publishes with no `metadata`, which
`NotificationPublisher.publish` defaults to `{}`; `list_by_state` is called from one place
only (`_eligible_candidates`) and only with `"candidate"`; `_ROUTINE_SUGGESTION_COOLDOWN_HOURS`
is 24, checked by a 15-minute loop, and `_build_routine_hint` spends the same budget. Two
corrections to the ask itself: routines are keyed by `name` (one YAML file each) and have
no id, so the endpoint is `POST /api/routines/{name}/state`; and the re-firing is bounded
— an ignored candidate loses 0.05 confidence per cycle and is archived below 0.3, which is
decay rather than consent, and worth saying so.

§9 is the missed-notification gap, naming the two recall-noise tickets already under
`docs/backlog/high/`. §10 and §11 come from the Task 3 and Task 4 reviews: `monkeypatch: Any`
in `tests/core/channels/test_admin_api.py` (17 uses against 196 `pytest.MonkeyPatch` in
`tests/`), and the two `waitFor` timeouts seen only under 8-way vitest over-subscription
(`web/src/main.test.ts`, `web/src/sheets/HeldBackSheet.test.tsx`) — same shape as the
`useActionRoute` race fixed in `5d827f1`.

- [ ] **Step 5: Check for real hostnames and commit**

The repo is public; the QA checklist already uses `alfred.example.com`.

```bash
git grep -n -E '192\.168\.50\.|66\.60\.90\.|anirudhlath\.com'
```
Expected: no output.

```bash
git add docs/web-frontend.md docs/admin-api.md \
  docs/superpowers/qa/2026-09-07-pwa-phase1-ios-checklist.md \
  docs/backlog/low/pwa-phase1-followups.md
git commit -m "docs(web): the Room's session window, session-id rotation, and the routine follow-ups"
```

---

## Self-review

- **Coverage:** issue 1 (pinned header/footer) and 2 (auto-scroll) → Task 1 (one root cause); issue 3 → Task 2 (body) + Task 6 §8 (interaction, backend follow-up per the user's choice); issue 4 → Tasks 3–5 (current session only, satellite included, recall untouched) + Task 6.
- **Types:** `SESSION_IDLE_MS` and `sessionWindow(items, now, idleMs)` are defined in Task 4 and used in Tasks 4–5; `Overview.session.idle_minutes` is defined in Task 3 and read by `sessionIdleMs` in Task 4; `chat.idleMs` is defined in Task 5 and set in Task 5's Room change. `readBackKey` for acts is `act:hue:text` — Task 2 keeps history and live text in step.
- **Wall clock:** every new test pins `Date` (`vi.setSystemTime`) or uses `Date.now()`-relative stamps; CI runs at UTC and the fixtures use naive local timestamps, so the App test picks a local `new Date(2026, 8, 7, 21, 20)`.
