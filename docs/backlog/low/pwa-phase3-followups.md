# PWA phase 3 follow-ups

**Priority:** low
**Source:** `docs/superpowers/plans/2026-09-16-pwa-phase3-memory-triggers-system.md` —
the deviations table it took knowingly, and what the three benches found while being
built. Phase 3 changed no backend code, by decision; most of what follows is the bill
for that.

Every claim below was re-checked against the tree on the branch that files it. Where the
plan's own wording turned out to be wrong, this file says what the code actually does,
not what the plan expected — see §13, §16 and the closing note.

## 1. Episodic search cannot say why nothing matched

The handoff's empty search state is
`best score 0.31 · threshold 0.55 · 128 hot, 1 204 cold searched`. The bench ships
`Nothing close enough to "<query>".` because none of those three numbers is in the
response. `EpisodicMemory.recall` (`core/memory/episodic/memory.py:62`) returns
`list[EpisodicResult]` — entry, score, source store — and the route flattens exactly
that (`core/channels/admin_api.py:518-527`). Rows below the cut are dropped by
`merged[:limit]` (`memory.py:109`) and nothing counts what was searched. There is not
even a threshold to report: both stores are searched with the ABC default
`min_similarity: float = 0.0` (`core/memory/vector_store.py:59`), so the only cut that
happens is the limit.

**Acceptance:** `recall()` returns the corpus sizes it searched, the effective
`min_similarity` and the best rejected score alongside the matches; `GET
/api/admin/memory/episodic?q=` carries them in the body beside `entries`; the bench
prints the handoff's line and deletes its honest substitute.

## 2. No embedder readiness probe, so the model pill starts at `unknown`

`model: unknown` until a search answers, then `ok` or `503`. There is no route that
reports whether the embedding backend is up: `GET /health`
(`core/channels/web_server.py:450-452`) is a hardcoded literal, and the overview's
`inference` block (`core/channels/admin_api.py:366-369`) probes `OLLAMA_HOST/api/tags`
and `LMSTUDIO_HOST/v1/models` — not the embedding host. The only way to learn the
embedder is down is to search and be refused, which is why `useMemory` sets the pill
from inside the query function.

**Acceptance:** the overview's `inference` block gains an `embeddings` entry probed the
same way the other two are, or a dedicated `GET /api/admin/memory/embedder`; `useMemory`
reads it and the pill is honest before the first search.

## 3. Browse-hot episodic rows have no id

`GET /api/admin/memory/episodic` walks the hot store with
`scan_iter(match=f"{CONTEXT_PREFIX}*")` and then `hgetall(key)`, discarding `key`
(`core/channels/admin_api.py:530-538`; `CONTEXT_PREFIX = "ctx:"` at
`shared/streams.py:35`). The hash itself stores no id field — the id *is* the key
(`core/memory/redis_vector_store.py:225`). So the client keys those rows `hot:<index>`,
which is correct only because the list is replaced whole on every read: nothing can link
to one, select one or notice that two reads returned the same memory.

**Acceptance:** the route sets `entry["id"] = decode_stream_value(key).removeprefix(CONTEXT_PREFIX)`
before appending; `toEpisodicRow` drops the index branch and `EpisodicRow.id` stops being
nullable.

## 4. Three response shapes for one row

Browse-hot, browse-cold and search answer with different field names and different types
for the same memory: `content` vs `summary`, a comma string vs a JSON string vs an array
of entities, epoch-seconds-as-a-string vs a REAL vs an ISO 8601 string, a string float vs
JSON text vs a dumped object for significance. `toEpisodicRow` (`web/src/lib/memory.ts`)
absorbs all of it and is tested against all three literal shapes, which is the only
reason nothing above that file branches on a store.

This is cheap to keep and expensive to get wrong once: the adapter has to parse numbers
before `Date.parse`, or an epoch string becomes the year 1758.

**Acceptance:** the three branches of `memory_episodic` (`core/channels/admin_api.py:487`)
serialise one Pydantic row model, with one name and one type per field; the client's
adapter shrinks to the store flag and the score.

## 5. No `next_fire_time` over HTTP, so a cron trigger can only be printed

A one-shot's meta reads `one-shot · runs 08:40 tomorrow` from `run_at`; a recurring one
reads `recurring · cron 0 19 * * 4`, because the schedule is all there is.
`next_fire_time` is a method on the trigger objects
(`core/triggers/models.py:60`, `core/triggers/types/time.py:66`,
`core/triggers/types/composite.py:81`) whose only consumer is `TriggerEngine.next_wakeup`
inside the triggers process (`core/triggers/engine.py:104`, called at `:117`). The list
route hands back the stored JSON verbatim (`core/channels/admin_api.py:424`), which for
a `TimeTrigger` is `{cron, run_at}` and nothing computed.

**Acceptance:** `GET /api/admin/triggers` includes a computed `next_fire_time` per row
(the engine already has the code; the admin API needs the same construction), and
`triggerMeta` formats a cron the way it formats a `run_at`.

## 6. Corrupt triggers are dropped from the list rather than shown

The handoff draws a `This record can't be read.` card. The bench can only draw it when a
*mutation* answers 500 on a row that decayed since the read, because the list route
swallows the failure:

```python
try:
    items.append(dict(json.loads(val)))
except (json.JSONDecodeError, ValueError):
    continue
```

(`core/channels/admin_api.py:423-426`). The trigger id is discarded a line earlier, so
even a placeholder row has nothing to key on. The single-trigger path does the honest
thing already — `_validate_trigger_exists` 500s on unparseable data
(`admin_api.py:648-653`). The same swallow guards deferred notifications
(`:440-441`) and devices (`:483-484`), so this is one pattern in three places.

**Acceptance:** the list route yields `{"trigger_id": tid, "error": "..."}` for a value it
cannot parse instead of `continue`, the client renders the handoff's card from it, and
the deferred and device lists get the same treatment.

## 7. No trigger create, edit or delete

The admin API has exactly three trigger routes: `GET /api/admin/triggers`
(`core/channels/admin_api.py:417`), `POST /api/admin/triggers/{id}/enabled` (`:655`) and
`POST /api/admin/triggers/{id}/fire` (`:674`). So the bench browses, filters, toggles and
fires, and its footer says `Nothing here edits a trigger — ask Alfred to change or remove
one.` rather than leaving a reader hunting for a button.

Full CRUD does exist, but on the triggers process's own tool-dispatch server — `POST
/triggers`, `PATCH /triggers/{id}`, `DELETE /triggers/{id}` (`core/triggers/server.py:25`,
`:35`, `:41`) on `TRIGGER_PORT` (`core/triggers/__main__.py:322-323`) — which carries **no
authentication dependencies at all**. It is an internal service and must not be proxied
as-is.

**Acceptance:** `POST`, `PATCH` and `DELETE /api/admin/triggers[/{id}]` on the
session-gated admin router, delegating to the store the internal service already uses;
the bench gains an edit sheet and the footer's second line comes out.

## 8. Nothing reports the *effective* DND state

There is no `GET /api/admin/dnd`. The bench reads `overview.dnd`, which is the raw manual
key straight out of Redis (`core/channels/admin_api.py:353-354`) — no expiry check, no
calendar. `DNDChecker` (`core/notifications/dnd.py:24`) is what actually decides: it
reads the manual window and *lazily expires and deletes* a stale one
(`dnd.py:50`, `:62-70`), then falls through to the calendar (`:81`). So the bench can
read `off` during a meeting that is quieting Alfred, or `on` for a window the notifier
would have expired. The Quiet card therefore carries the footnote
`A meeting in your calendar can also quiet Alfred; that is not shown here.`

**Acceptance:** `GET /api/admin/dnd` (or an `overview.dnd` widened to the same shape)
returns `DNDChecker.is_active()`'s answer with the reason — `manual` or `calendar` — and
the expiry it is honouring; the footnote is replaced by the calendar's own row.

## 9. Health has no service, GPU or satellite inventory

The handoff's Health grid reads `bus · redis 6 services, 8 streams` and
`reflex · reflex-3b · gpu 41%`. Neither number has a source. `_base_overview`
(`core/channels/admin_api.py:188`, keys at `:195-205`) exposes `redis`, `cost`, `dnd`,
`counts`, `streams`, `inference`, `reflex{model,last_ms,p50_ms}`, `librarian` and
`session` — no service registry, no GPU telemetry anywhere in the repo, and the satellite
bridge is wired into the lifespan (`core/channels/web_server.py:361-389`) and never
exposed. So the bench ships `bus · redis · N streams` from
`Object.keys(overview.streams).length` and `reflex · <model>`. The nearest per-service
fact is `GET /api/integrations/{name}/status` (`web_server.py:764`), one service at a
time, which the Connected services section already uses.

**Acceptance:** the overview gains a `services` list (name, healthy, latency) built from
the registry the status route already walks, and a `gpu` block where there is a device to
report one from; the two cells print the handoff's line.

## 10. No ops actions — no restart, no log download

The old SPA could restart a service and download logs. Nothing in the API can: there is
no restart, logs or exec route on any router. The System bench's Maintenance section is
therefore the two things that do exist — `POST /api/admin/notifications/drain`
(`core/channels/admin_api.py:627`) and `POST /api/admin/librarian/run` (`:633`), both of
which publish an internal action and answer `{"status":"queued"}`.

**Acceptance:** a decision first — whether a phone should be able to restart a service at
all — then `POST /api/admin/services/{name}/restart` and a log route with a bounded tail,
both session- *and* network-gated like the credential writes, or this is closed as
deliberately out of scope.

## 11. Passkey removal has a route and no UI

`DELETE /api/auth/credentials/{credential_id}` exists and is thorough
(`core/identity/auth_routes.py:752`): session-gated, validates the id, refuses to remove
the last passkey, and ends the sessions that credential opened. The bench lists passkeys,
mints a pairing code and signs out, and offers no removal — because the missing half is
the confirmation design, not the endpoint. Removing the credential you are holding is a
different sentence from removing one you left at an old address, and the route's own
last-passkey refusal needs a sentence of its own.

**Acceptance:** a `Remove` affordance on `CredentialRow` with a two-step confirmation
that names the device, a distinct sentence for the last-passkey refusal, and a reload of
`["system", "credentials"]` and `["system", "sessions"]` behind it — the route already
invalidates both on the server side.

## 12. The credential `PUT` is network-gated while the bench around it is not

`PUT /api/integrations/{name}/credentials` carries `_CREDENTIAL_GATES` —
`[Depends(require_trusted_network), Depends(require_authenticated)]`
(`core/channels/web_server.py:441`, applied at `:660-663`; `DELETE` the same at
`:680-683`). Every read on the bench is session-only (`GET /api/integrations` at `:603`,
`GET /api/integrations/{name}/status` at `:764`, and the whole `/api/admin` router at
`admin_api.py:332-335`). So off the home network the whole System bench works and exactly
one control fails. The client handles it rather than hiding it: `IntegrationRow` prints
`Credentials can only be changed from the home network.` and keeps the typed values in
front of the reader, while the 403 also raises the Denied gate over the layer — both
deliberately, because the gate names the network and the sentence names the row.

**Acceptance:** either the `PUT` becomes session-gated with a re-authentication step of
its own, or the gate is discoverable before the typing — the form knows it is
network-gated on first paint and says so, which needs the gate reported in `GET
/api/integrations` rather than discovered by being refused.

## 13. Two copies of the raw-dump markup

The expanded payload block — mono 11px, `--surface` ground, `--fg2` text, wrapped and
break-words — exists twice: hoisted as `DUMP` / `DUMP_STYLE` in
`web/src/workshop/TriggerRow.tsx:44-46` (used for the action and the conditions), and
inline in `web/src/workshop/EventRow.tsx:79-84`. **They are not identical**, which is the
correction to the plan's own wording: `DUMP` adds `m-0` and orders the last two classes
the other way round. The style objects do match by value.

Task 6 kept its copy local rather than inventing a cross-file styling module inside a
bench commit, which is still the right call for two.

**Acceptance:** a third copy appears — then one `RawDump` component in `workshop/`, with
the `m-0` question settled once and both call sites re-tested. Not before.

## 14. One owner for the deferred-queue drain

`HeldBackSheet` keeps a local `queuedAt` reset on every open
(`web/src/sheets/HeldBackSheet.tsx:21`, `:30-36`) and POSTs the drain route itself
(`:51`); `useSystem` keeps `maintenance.drainedAt` (`web/src/workshop/useSystem.ts:312`)
and goes through `drainDeferred()` (`web/src/lib/system.ts:805-807`). Neither reads the
other. So the System bench can read
`queued 21:14 · the notifier sends them when it next reads the queue` while the sheet —
opened from the row two lines above it — still offers `Drain queue now`.

The server is idempotent: the route publishes an internal action
(`core/channels/admin_api.py:627`) and the handler LPOPs until the queue is empty, so a
second drain finds nothing. This is a narrative defect, not a delivery one.

**Acceptance:** `drainedAt` has one owner above both surfaces — the Room, or a small
provider — and the sheet's button reads `Queued` when the bench queued it, and the other
way round.

## 15. Activity's scroll position dies on a tab trip

Measured while mounting the benches: `scrollTop` 420 → 0 and a fresh `<ul>` node, while
`expanded` and `solo` survive because they live in `useActivity` up in `WorkshopPanel`
(`web/src/workshop/Workshop.tsx:117`). A reader four hundred rows down who glances at
Memory comes back with the row still open and the viewport at the top — and the anchor
refs (`ActivityBench.tsx:81-83`) reset with the component, so the first live prepend
after every return is unanchored too.

This is a consequence of one bench being mounted at a time (`Workshop.tsx:204-221`), not
of the phase-3 mounting commit: phase 2 unmounted `ActivityBench` the same way. The
alternative — four mounted panels with three hidden — costs `SystemBench`'s two timers
running for the life of the Workshop behind a panel nobody can see.

**Acceptance:** the scroll offset and the anchor state are lifted into `useActivity`
beside `expanded` and `solo`, restored in a layout effect on remount, and asserted by a
test that trips to another bench and back. The virtualisation ticket
(`pwa-phase2-followups.md` §1) would subsume it.

## 16. Cold rows never carry honest recall stats

A cold *browse* row reads `never recalled`, and any cold row under the decay floor reads
`decaying`, whatever its real history — `recalled` is 0 because the response has no such
field, and `decaying` is computed against that zero
(`web/src/lib/memory.ts:168`, `:184-190`, `DECAY_FLOOR = 0.3` at `:79`).

The plan blamed the SELECT. It is worse than that, and this is the correction: the
columns **do not exist in the cold schema at all**. The browse SELECT
(`core/channels/admin_api.py:547-552`) reads
`id, timestamp, source, summary, entities, valence, significance, semantic_key`, and
`core/memory/episodic/schema.sql:12-20` plus the v2 migration
(`core/memory/sqlite_vec_store.py:308-312`) is the whole table. Real retrieval stats live
only in the hot Redis hash, written by `record_retrievals()`
(`core/memory/vector_store.py:88-96`).

Nor does search rescue a cold row, which the plan assumed it did: the cold store
hardcodes `retrieval_count=0, last_retrieved=0.0` into the metadata it returns
(`core/memory/sqlite_vec_store.py:630-631`), and `recall()` writes
`search_result.metadata.retrieval_count + 1` into the entry
(`core/memory/episodic/memory.py:132`). **So every cold search row reports
`retrieval_count: 1` regardless of history, and the bench prints `recalled 1×` from it.**
`EpisodicEntry.last_retrieved` is never set by `recall()` at all
(`core/memory/schemas.py:44`), so it is null for hot and cold search rows alike. That is a
number the screen presents as fact and the server invented — a §5.2 failure that the
client cannot detect from the response.

**Acceptance:** `retrieval_count` and `last_retrieved` columns on `episodic_entries`,
written by the cold store's own `record_retrievals`, returned by both the browse SELECT
and the search metadata; `recall()` stops incrementing a count it was handed. Until then,
consider having the client drop the recall clause for cold rows entirely rather than
print a fabricated `recalled 1×`.

## 17. Health's four stats are one card, not four

The handoff draws a 2×2 grid with an 8 px gap whose cells are each their own radius-12
bordered card (`Alfred.dc.html:356-358`). The bench
draws a 2×2 grid of internal borders inside one `SystemSection`
(`web/src/workshop/SystemBench.tsx:253-258`, edges computed at `:149`), because the
section frame is itself the radius-12 card (`SystemFrame.tsx:36-39`) and every other
section on the bench needs exactly that frame. Four free-standing cards inside it would
be a card in a card, or a section that is not a `SystemSection`.

**Acceptance:** revisited only if a second section ever wants a frame of its own — the
tension is the frame's, not the grid's.

## 18. The health dot sits above its value, and the value is mono

The handoff puts the dot 8 px to the left of a 20 px value that inherits DM Sans
(`Alfred.dc.html:359`). The stat is a `flex flex-col gap-1` with the dot first and a
`t-title font-mono` value under it (`web/src/workshop/SystemBench.tsx:151-166`), for the
reason §17 gives: the column is what fits a 2×2 grid inside a section-width card at
360 px.

**Acceptance:** re-measure on the device at 360 px with the four-card layout of §17; the
two are one decision.

## 19. Two copy deviations already shipped

`2.1 ev/s`, against the handoff's `2.1/s`: `rateText` (`web/src/lib/format.ts:92-95`) is
shared by the Room's status line, the Workshop's header and the health grid's rate cell,
and the handoff itself writes `ev/s` in two of those three places. One number reading two
ways is worse than the mismatch.

`resets 00:00`, dropped from the spend note (`web/src/lib/system.ts:340-352`):
`core/conscious/cost.py` rolls the day on `datetime.now(UTC)`, so the string is false for
any household outside UTC.

**Acceptance:** the cost block reports the window it actually uses — the boundary as an
ISO instant — and the note formats it in the reader's zone; the rate one closes when the
handoff is revised.

---

## Checked and not filed

The plan's task 11 listed a twentieth item: `bus · redis · 1 streams`, an ungrammatical
count for a house running a single stream. **It is not a defect.** `web/src/lib/system.ts:536-538`
reads `` `bus · redis · ${streamCount} ${streamCount === 1 ? "stream" : "streams"}` ``,
and `web/src/lib/system.test.ts:738` ("calls the rate alive on a house carrying a single
stream") asserts `bus · redis · 1 stream` exactly, with a companion at `:721-727` pinning
`0 streams`. The singular was fixed in the same round that found it and the plan's list
was never amended.
