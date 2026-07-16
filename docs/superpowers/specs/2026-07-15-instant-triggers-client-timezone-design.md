# Instant Triggers + Client Timezone Awareness — Design

**Date:** 2026-07-15
**Status:** Approved
**Repos:** `alfred/` (server + web), `alfred-ios/` (AlfredKit DTOs)

## Summary

Two user-facing defects share a root in Alfred's time handling:

1. **Reminder latency.** "Remind me in 5 seconds" fires 10–20s late. Each process holds its own
   `TriggerStore` in-memory cache synced only by a 60s full refresh, so a trigger created in the
   conscious process is invisible to the triggers process for 0–60s. Firing itself runs on a 1s
   poll loop, and cron fires silently skip when a tick misses a <1s match window.
2. **No client-local time.** The server runs entirely in UTC and no client ever sends its
   timezone. The LLM's only clock is a hardcoded-UTC `## Current Time` prompt line, so absolute
   wall-clock requests ("remind me at 3pm"), cron schedules, and routine time patterns are all
   silently interpreted as UTC.

This design makes trigger visibility and firing event-driven (Redis pub/sub cache coherence +
scheduled-wakeup firing, no polling), and threads client IANA timezone from web/iOS clients
through `UserRequest` into the prompt, trigger semantics, and routine matching.

## Root causes (evidence)

- `core/triggers/__main__.py` — `_periodic(store.refresh, 60.0)`: only cross-process
  visibility path for trigger mutations.
- `core/triggers/store.py` — `list_all()` serves the stale in-memory cache; `refresh()` is a
  full `HGETALL` explicitly banned from the hot path.
- `core/triggers/feature.py` — only `create_trigger` publishes any event; update/delete/toggle
  publish nothing. Staleness is bidirectional (one-shot fire deletes in the triggers process are
  invisible to the conscious process's cache).
- `core/triggers/__main__.py` — `tick_loop()` polls every 1s; the repo's design rules ban
  polling where an event-driven alternative exists.
- `core/triggers/types/time.py` — cron evaluation fires only when a tick lands within a
  `diff < 1.0` second window of the cron boundary; a busy event loop skips the fire entirely.
- `core/conscious/context_assembler.py` — `## Current Time` rendered with a hardcoded `Z`
  (UTC) suffix; `core/conscious/engine.py` uses `datetime.now(UTC)`.
- `web/src/lib/chat-socket.ts`, `alfred-ios .../DTOs/ClientDTOs.swift`,
  `bus/schemas/events.py` (`UserRequest`) — no timezone field anywhere in the transport.
- `core/memory/routines/patterns.py` — `match_trigger_pattern()` compares `now.hour` /
  `now.weekday()` against UTC `now` from callers.

## Goals

- A reminder created by the LLM is visible to the triggers process within milliseconds and fires
  within milliseconds of its due time.
- Cron fires are guaranteed (computed, not window-matched); a late wakeup fires once, never skips.
- All processes holding a `TriggerStore` see every mutation instantly, both directions.
- The LLM sees the user's local wall-clock time (weekday + offset + zone name); "at 3pm" means
  3pm where the user is.
- Cron schedules and routine time patterns evaluate in the user's current timezone (DST-correct).
- Old clients (and Signal) keep working with graceful UTC fallback.

## Non-goals (YAGNI, deliberate)

- Per-identity/multi-user timezone keys — single Redis key now; per-identity is a later extension.
- Instant-vs-deferred mutation classification — mutations are rare and cost one `HGET`; all are
  instant. Noisy inputs (sensor storms) ride the separate, already-event-driven state stream.
- New semantic bus events (`TriggerUpdated`/`TriggerDeleted`) — cache coherence uses the pub/sub
  channel; the semantic event bus is unchanged (`TriggerCreated` stays as-is).
- Streaming/durable delivery for cache coherence — pub/sub + 60s reconciliation + reconnect
  refresh is sufficient; a missed message self-heals within 60s.

## Part 1 — Trigger latency

### 1a. Store-level pub/sub cache coherence

Cache coherence becomes `TriggerStore`'s own responsibility; callers notice nothing.

- New constant in `shared/streams.py`: `TRIGGERS_CHANGED_CHANNEL = "alfred:triggers:changed"`.
- `TriggerStore.save()` → existing `HSET` + YAML snapshot, then
  `PUBLISH TRIGGERS_CHANGED_CHANNEL {"op": "saved", "trigger_id": ...}`.
- `TriggerStore.delete()` → existing `HDEL` + YAML delete, then publish `{"op": "deleted", ...}`.
- `TriggerStore.start_sync()` — new background subscriber task, started wherever a store is
  instantiated (currently the triggers and conscious processes; the implementation plan must
  verify no other process constructs a `TriggerStore`):
  - `saved` → single `HGET` of that trigger, parse, upsert cache. `HGET` returning `None`
    (deleted between publish and fetch) → evict instead.
  - `deleted` → evict from cache.
  - `tz-changed` → no cache mutation; fire callbacks only (see Part 2).
  - After applying any message, fire registered `on_change` callbacks (the triggers process
    registers the scheduler wake; other processes register none).
  - Self-published messages are received and applied idempotently — harmless by design.
- **Failure handling:** pub/sub is fire-and-forget. Safety nets: (a) the periodic full
  `refresh()` stays at **60s** (user decision: conservative) and now also fires `on_change`;
  (b) on subscriber error/reconnect → log, resubscribe, then full `refresh()` to heal anything
  missed. `_resolve_trigger`'s refresh-on-miss stays as defense in depth.

### 1b. Scheduled-wakeup firing (replaces the 1s tick)

- New method `BaseTrigger.next_fire_time(context) -> datetime | None`. Default `None`
  (= not clock-driven). `SensorTrigger` inherits the default.
  - `TimeTrigger` with `run_at`: returns `run_at` if not yet fired (`last_fired is None or
    last_fired < run_at`), else `None`.
  - `TimeTrigger` with `cron`: next cron fire strictly after `last_fired or created_at`,
    computed via croniter in the user's timezone (Part 2). May be in the past if the process
    slept through it — that is the guarantee mechanism.
  - `CompositeTrigger`: min over children's `next_fire_time`, `None` if all `None`.
- **Scheduler loop** (replaces `tick_loop` in `core/triggers/__main__.py`):
  ```
  while not shutdown:
      wake_event.clear()
      next_due = min(next_fire_time over enabled triggers, ignoring None)
      timeout = None if next_due is None else max(0, next_due - now_utc)
      try:
          await asyncio.wait_for(wake_event.wait(), timeout)   # woken by mutation → recompute
      except TimeoutError:
          await engine.evaluate_tick(datetime.now(UTC))         # due → evaluate + fire
  ```
  Ordering note: `clear()` happens **before** computing `next_due`, so a mutation landing during
  the computation leaves the event set and `wait_for` returns immediately — re-arms can never be
  missed.
- **Cron evaluation rewrite** in `TimeTrigger.evaluate()`: fire when
  `now >= next fire after (last_fired or created_at)`, deduped by `last_fired`. Replaces the
  fragile `diff < 1.0` window. A process that slept through N boundaries fires exactly **once**
  (catch-up), then re-anchors from the new `last_fired`.
- `run_at` evaluation semantics are unchanged (`now >= target`, deduped by `last_fired`); a
  `run_at` already in the past at creation fires immediately on the wake triggered by its own
  creation message.
- The event path (sensor triggers via the home-state stream listener) is untouched.
- `responds_to_tick` stays as the fast-path guard in `_evaluate_all` (no behavior change for
  sensor triggers); `next_fire_time() -> None` is the scheduling-side equivalent.

**Resulting latency:** creation → visibility in milliseconds (pub/sub) → firing within
milliseconds of due time (exact alarm) → delivery via existing blocking stream reads (~0ms
added). A "5 second" reminder is bounded only by the LLM computing `run_at` mid-response.

## Part 2 — Client timezone awareness

### Transport

- `bus/schemas/events.py` — `UserRequest` gains `timezone: str | None = None` (IANA name,
  e.g. `America/Denver`).
- Web (`web/src/lib/chat-socket.ts`): outbound body gains
  `timezone: Intl.DateTimeFormat().resolvedOptions().timeZone`.
- iOS (`AlfredKit .../DTOs/ClientDTOs.swift`): `TextMessageDTO` and `AudioMessageDTO` gain
  `timezone` populated from `TimeZone.current.identifier`.
- Web channel WS handler (`core/channels/web_server.py`): reads optional `timezone` from the
  client payload into `UserRequest`. Signal bridge sends none.

### Storage & resolution

- Single Redis key `alfred:user:timezone` (constant in `shared/streams.py`). Single-user by
  design today.
- New helper module `shared/usertime.py`:
  - `set_user_timezone(redis, tz)` — validates via `zoneinfo.ZoneInfo` (invalid → ignored,
    logged); writes only when the value changed; on change also publishes
    `{"op": "tz-changed"}` on `TRIGGERS_CHANGED_CHANNEL` so long-sleeping cron alarms re-arm
    under the new zone instantly.
  - `get_user_timezone(redis) -> ZoneInfo` — resolution order: stored key → `ALFRED_TIMEZONE`
    env → UTC.
- The web channel calls `set_user_timezone` when an inbound message carries a timezone.
- Per-request resolution in the conscious engine: request `timezone` (if present) else
  `get_user_timezone`.

### LLM prompt

- `core/conscious/context_assembler.py`: `## Current Time` becomes local wall-clock with
  weekday, offset, and zone — e.g. `Tuesday 2026-07-15T14:05:32-06:00 (America/Denver)`.
- `core/conscious/prompts/personality.md` + `TriggerFeature.get_tools()` docs: instruct the
  model to emit `run_at` as ISO-8601 **with UTC offset**.

### Time semantics

- **`run_at` normalization at the boundary:** `create_trigger`/`update_trigger` convert the
  incoming `run_at` to timezone-aware at write time; a naive `run_at` is interpreted in the
  user's current timezone. Stored triggers are therefore always aware; evaluation needs no tz
  lookup for `run_at`.
- **Legacy data:** triggers stored before this change may hold naive `run_at` computed against a
  UTC prompt — the evaluation-time fallback (naive → UTC) stays, for exactly that data.
- **Cron:** evaluated dynamically in the user's **current** timezone (croniter over a
  `ZoneInfo`-aware datetime; DST-correct). "7am daily" follows the user across zones. The
  triggers process resolves tz via `get_user_timezone` at each recompute (recomputes are rare:
  mutations, fires, reconciliation, tz-changed pokes). `TriggerContext` gains the resolved tz.
- **Routine patterns:** callers of `match_trigger_pattern()` (conscious routine-suggestion loop,
  librarian pattern detection) convert `now` to the user's tz before matching.

## Error handling summary

- Invalid client timezone string → validation in `set_user_timezone`, ignored + logged, stored
  value kept.
- Pub/sub subscriber crash → resubscribe + full `refresh()`; worst-case staleness 60s
  (reconciliation), same as today's steady state.
- Per-trigger evaluation exceptions → logged, other triggers unaffected (existing behavior).
- `HGET` miss on `saved` → treat as delete (race with deletion).
- Scheduler wake/compute race → eliminated by clear-before-compute ordering (above).

## Testing

TDD; failing tests first. Key cases:

- **Cross-process visibility:** store A `save()` → store B (separate instance, shared Redis)
  sees the trigger without `refresh()` — the actual 5s-reminder regression test.
- Scheduler: re-arms on mutation mid-sleep; fires at due time; no busy-loop when idle;
  past-due `run_at` at creation fires immediately.
- Cron: next-fire computation across a DST transition; exactly-once catch-up after simulated
  sleep-through; dedupe by `last_fired`.
- `run_at` normalization: naive → user tz at creation; aware passes through; legacy naive → UTC
  at evaluation.
- Prompt: local rendering with weekday/offset/zone; UTC fallback when nothing stored.
- `set_user_timezone`: validation, write-on-change-only, tz-changed poke.
- Web: chat-socket emits `timezone` (frontend unit test). iOS: DTO encoding includes `timezone`
  (swift test).
- Gates: `ruff check`, `ruff format`, `mypy --strict`, `pytest`, `npm run lint && npm run test`,
  `swift test` (AlfredKit).

## Docs & QA

- Update trigger data-flow diagrams (`core/CLAUDE.md`, `docs/` feature docs) — tick loop →
  scheduled wakeup; add pub/sub coherence channel; document `alfred:user:timezone`.
- Manual QA tickets in `docs/qa-backlog/` — critical: live end-to-end "remind me in 5 seconds"
  (expect ≤ ~6s including LLM latency); "remind me at <local time>" from web and iOS; cron
  trigger across a simulated tz change.

## Rollout

- Feature-branch worktree inside `alfred/`; separate small branch in `alfred-ios/`. PRs per repo.
- No migrations: new `UserRequest` field is optional; legacy naive `run_at` keeps UTC fallback;
  absent tz key resolves to `ALFRED_TIMEZONE`/UTC (current behavior).
