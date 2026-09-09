# PWA phase 1 follow-ups

**Priority:** low
**Source:** the four Mission Control backlog tickets the phase 1 hard cut made moot
(`web-frontend-followups.md`, `voice-enrollment-card-polish.md`,
`lan-only-writes-affordance.md`, `web-activity-virtualized-list.md`), rewritten for the
client that replaced them. Those files are deleted. §1–§7 are what survived that cut.
§8 onward are new, from the phone-fixes work of 2026-09-09
(`docs/superpowers/plans/2026-09-09-pwa-phase1-phone-fixes.md`).

## 1. Credential writes are LAN-only, and the client still cannot tell

Carried over whole from `lan-only-writes-affordance.md` — the surface moved, the problem
did not. Credential writes (`PUT/DELETE /api/integrations/{name}/credentials`) and
device-token writes (`POST/DELETE /api/devices/register`) need a passkey session **and**
a trusted network. The setup gate's second step (`web/src/gates/SetupGate.tsx`) `PUT`s
home-service credentials with no idea whether the current origin can write at all, so a
first run over the public host gets a 403 — which now raises the **Denied gate** over the
setup gate, mid-wizard. That is a worse failure than the old toast, not a better one.

**Acceptance:**
- The client knows whether the current origin is LAN-only-write capable (e.g. an
  `/api/auth/status` field, or a cheap probe) and says so *before* the step — "open
  Alfred on the house network to add credentials" — rather than letting the write fail.
- A 403 from a credential or device-token write renders a short human message; the raw
  operator `detail` (env-var name, example CIDR) is not surfaced in the UI.
- The Workshop specifies the same affordance before it ships its own credential cards.

## 2. `web/src/lib/types.ts` still hand-mirrors the bus and admin schemas

From `web-frontend-followups.md` §3, unchanged by the rewrite: the TS contract
(`ChatServerMessage`, `TelemetryMessage`, `Overview`, `StreamPage`, `PendingAction`,
`ActionResultEvent`) is hand-written against the Pydantic models and the admin response
shapes, with no drift guard in either direction. A server-side field rename type-checks
green on both sides and fails at runtime. Largely unavoidable across Python↔TS.
**Acceptance (optional):** a comment on the mirrored bus schemas pointing at `types.ts`;
consider a `datamodel-code-generator` step as a future task.

## 3. Stream names are hardcoded client-side

From `web-frontend-followups.md` §1, much reduced. The three-way duplication is gone with
`AlfredProvider` and `ActivityPage`; what remains is two small hardcoded sets —
`ROOM_STREAMS` in `web/src/lib/history.ts` and `RESULT_STREAM` in
`web/src/door/DoorProvider.tsx` — naming streams that `core/channels/stream_catalog.py`
owns. Renaming a stream there leaves the client silently reading nothing; no test or type
catches it. **Acceptance:** a catalog assertion in the SPA CI test, or expose the catalog
(`GET /api/admin/stream-catalog`) and derive the names.

## 4. The Workshop's Activity view will need virtualization

From `web-activity-virtualized-list.md`. The page it was written about is deleted, and
the Room's timeline is bounded — four stream pages of 50, plus live rows — so nothing
needs virtualizing today. The finding still stands for phase 2: mounting every entry of a
live feed degrades scroll on low-end hardware past ~500 nodes, and
`@tanstack/react-virtual` or `react-window` is the answer when the Activity view returns.
Log it against that plan rather than the current client.

## 5. Voice enrollment has no home in the client

From `voice-enrollment-card-polish.md`. `web/src/pages/VoiceEnrollmentCard.tsx` is
deleted and phase 1 has no enrollment surface (`docs/voice-satellites.md` already says
so). When the Workshop reinstates enrollment, the three gaps the
original ticket found are worth building in from the start rather than fixing after:
the mic control disabled while a submit is in flight, the error state cleared as soon as
a new sample is recorded, and the status region announced via `aria-live="polite"`.

## 6. Three QA scripts to rewrite when the Workshop lands

Deleted with the surfaces they drove; the flows behind them still exist server-side and want
a script again once there is a screen to run it on.

- `web-live-telemetry.md` — the telemetry rail and Activity page live feed. Phase 1
  subscribes to one stream (`home_action_results`, for the Door), so there is nothing to
  watch; the reconnect-after-SIGKILL half of it is still worth keeping when Activity returns.
- `web-admin-controls.md` — the Triggers page and ⌘K palette controls (DND, drain, trigger
  fire, run Librarian). DND and drain **do** have a phase 1 surface — the Room's DND row and
  the Held-back sheet — but trigger fire and Librarian have none, and the script was written
  against the page, not the endpoints.
- `service-credentials-settings-ui-flow.md` — the Settings page `IntegrationCard` for
  `kind=service` entries: badges, TEST CONNECTION, CLEAR, and the 502-on-unreachable-service
  path. Phase 1's only credential surface is the setup gate's second step, which writes
  home-service and nothing else. Rewrite alongside item 1 above.

## 7. No sign-out and no Workshop › System in phase 1

`POST /api/auth/logout` exists server-side, but the client's `logout()` went with the
rest of the orphaned helpers: phase 1 has no screen that would call it, and a sign-out
button in the Room is not in the design. The attention set chosen in setup step 3 is the
same story — once written, it cannot be changed from the phone. Both belong to the
Workshop's System page (spec §8, phase 3).

Until the Workshop's health page, the telemetry pump's `status` (`redis_error`) and
`error` (`invalid JSON`) frames reach only the console: `ConnectionProvider` logs them
with `console.warn`, the same complaint at most once a minute, and nothing on screen
shows them.

`summarize` and `timeOf` in `format.ts` were deleted rather than held for phase 2; the
Activity view will write its own summariser against the bus schema it actually renders.

## 8. Routine suggestions cannot be accepted or declined

The Room now shows the suggestion's body, but there is nothing to tap, and that is a
backend gap, not a client one:

- **Nothing changes a routine's state on purpose.** `core/memory/routines/store.py` is a
  YAML store — `save`, `get`, `list_all`, `list_by_state`, `delete` — with no endpoint, no
  LLM tool and no channel behind it. The lifecycle states are `candidate`, `active`,
  `dormant`, `archived` (`RoutineSpec`, `core/memory/schemas.py`); there is no `rejected`,
  and **nothing in the tree ever writes `active`**. The only transitions that happen are
  the Librarian's, in `_update_routine_lifecycle` (`core/librarian/consolidator.py`):
  three consecutive misses → `dormant`, thirty days dormant → `archived`, and confidence
  decayed below `routine_archive_threshold` (0.3) → `archived`. A routine is born a
  candidate and can only fade, and nothing records a refusal: `RoutineSpec` has no field
  for a suggestion the user declined to answer.
- **The suggestion carries no identity.** `check_routine_suggestions`
  (`core/conscious/engine.py`) publishes `title="Routine Suggestion"`, the body and
  `source="librarian"` — no `metadata`, which `NotificationPublisher.publish` then
  defaults to `{}`. The client cannot tell which routine the row is about. It needs
  `metadata.routine_name`: the store keys by `name`, one YAML file per routine, and there
  is no id.
- **Nothing executes an `active` routine.** `list_by_state` is called from exactly one
  place — `engine.py`'s `_eligible_candidates` — and only ever with `"candidate"`. The
  consolidator's lifecycle pass and `GET /api/admin/memory/routines` read `list_all()`.
  Accepting a suggestion today would change a label and nothing else.
- **It re-fires daily while it stays a candidate, and nothing bounds that.**
  `_ROUTINE_SUGGESTION_COOLDOWN_HOURS` is 24, checked by a 15-minute loop in
  `core/conscious/__main__.py`; `_build_routine_hint` spends the same budget, so a chat
  turn can be the day's suggestion instead. Observed on a live house: 47 of the last 50
  notifications were the same coffee routine. Only one thing stops it — three consecutive
  Librarian misses → `dormant`, which takes the routine out of
  `list_by_state("candidate")` — and a routine whose `trigger_pattern` does not parse
  escapes even that: `match_trigger_pattern` (`core/memory/routines/patterns.py`) returns
  `True` for an unrecognised pattern on purpose, so it never misses, never goes dormant,
  never decays, and suggests itself for ever. The confidence decay looks like the brake
  and is not one — it sits in the `else` of `if pattern_fired:` **and** needs
  `now - last_suggested >= 24 h`, while both suggestion paths re-stamp
  `last_suggested = now` on every fire, so a daily re-firer never satisfies both.

The fix is four pieces, and the order matters. First a state a user can put a routine
in: `RoutineSpec` has no `declined` and nothing writes `active`, so the schema comes
before the `POST /api/routines/{name}/state` endpoint that writes it (behind the admin
gate, with a conscious-engine tool so "yes, do that" in the thread works too). That is
also the only thing above that stops the daily re-fire — a routine that stops being a
candidate drops out of `list_by_state("candidate")`. Then `metadata.routine_name` on the
notification, so a row can name its routine; then an executor for `active` routines; then
the client's accept/decline on the row. Until the executor exists a button would be a lie
— spec §5.2, item 3 names routine lifecycle as one of the two places where "Alfred knows
this" has degrees.

## 9. Notifications missed between sessions surface nowhere

The Room keeps the house's rows for the day and the conversation for the session, so a
notification from yesterday that you never saw is gone from the phone until the Activity
view (phase 2) or push (phase 5). Phase 1's honesty rule applies: nothing pretends to be
a badge. Recall noise is already filed under `docs/backlog/high/`
(`passive-observations-are-75-percent-duplicates.md`,
`involuntary-recall-threshold-too-high.md`).

## 10. `tests/core/channels/test_admin_api.py` types its fixture `monkeypatch: Any`

Seventeen times, in the one file. The repo's convention is `monkeypatch: pytest.MonkeyPatch`
— 196 uses across `tests/` — and `Any` throws away the only type information the fixture
has. Nothing behind it: a single mechanical pass, worth doing in one commit rather than a
line at a time as the file is touched.

## 11. Two web tests time out under heavy suite parallelism

Under eight concurrent vitest suites (load average ~118 on a 16-thread box) two tests
time out, twice each. They are not the same failure:

- `"installs the viewport and audio hooks, then renders the App under StrictMode"`
  (`web/src/main.test.ts`) has no `waitFor` or `findBy*` at all — it is
  `await import("./main")` followed by synchronous expects, so what it exceeds is
  vitest's own 5 s **test** timeout: a dynamic import that never settles under load.
  Nothing to race, and no assertion to make more patient; if it recurs, the test timeout
  or the suite concurrency is the lever.
- `"says queued, not delivered"` (`web/src/sheets/HeldBackSheet.test.tsx`) is a
  `findByRole` at the 1 s default. That one *is* the same shape as the `useActionRoute`
  race fixed in `5d827f1` — an assertion racing an effect rather than a slow machine —
  and the same fix would apply.

Both are clean at four-way and single-suite, so nothing is broken today. If the frontend
suites are ever run in parallel in CI, attach this entry to that change.

## 12. A satellite turn holds the thread open but not the session

The Room's window and the chat socket's session id are read off two different clocks, and
a satellite turn moves only one of them. `sessionWindow` (`web/src/lib/history.ts`) walks
the merged streams, and satellites write `user_requests` and `user_responses` exactly as
the phone does, so a satellite turn breaks the silence — even though the satellite
pipeline is running a server session of its own
(`core/channels/satellite/pipeline.py`, `session_id = f"sat-{entry.name}"`).
`alfred.session-at`, which `ChatSocket.sessionIdle()` reads to decide whether to rotate,
is stamped only by a PWA send.

On a house with the default thirty-minute timeout: you send from the phone at 20:00 and
the stamp reads 20:00; you say something to a satellite at 20:20; the app foregrounds at
20:40 and the window keeps both turns, because no gap in it reaches thirty minutes; you
send again at 20:41 and `sessionIdle()` reads a stamp forty-one minutes old, so `forget()`
rotates the id and Alfred answers with an empty context. The thread says nothing about it:
`withDividers` sees a twenty-one-minute gap between 20:20 and 20:41 and draws no
`new conversation` divider. The mismatch only runs this way — the stamp is never newer
than the newest PWA turn, so the divider is never drawn without a rotation behind it.

The fix is to stop inferring the rotation and read it: `ChatSocket.forget()` surfaces the
turnover (an `onsession` callback, or a `sessionStartedAt` the Room can read alongside
`sessionId`), and `useRoom` draws the `new conversation` divider on that signal rather
than on the stamp gap `withDividers` computes. Low priority — nothing is lost from the
thread and every turn is still yours; what is wrong is only the label above them.
