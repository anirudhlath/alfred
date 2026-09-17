# Reflex's TriggerFired Loop Never Reclaims Its PEL

**Priority:** high
**Source:** Passive Observation code review, 2026-09-03 (adjacent finding — logged, not fixed on that branch)

## Summary
`_consume_trigger_fired` (`core/reflex/__main__.py`) reads `EVENTS_STREAM` with
`XREADGROUP '>'`, ACKs only on success, and logs `"Error processing trigger_fired %s: %s — will
retry"` on failure — but nothing anywhere reclaims consumer group `reflex-trigger-fired`. There
is no retry. Every TriggerFired whose handling raises is silently lost and leaks a pending entry
forever.

This is the identical defect just fixed in the Memory Ingestor
(`fix(memory): reclaim and reprocess the ingestor's pending entries`), in a different subsystem.

## Context / Motivation
It is a direct violation of the rule in `CLAUDE.md`: *"A consumer loop that ACKs only on success
MUST reclaim its PEL, and must process what it reclaims. `XREADGROUP '>'` only delivers *new*
messages, so an un-ACKed entry is never redelivered on its own."* That rule exists because this
exact failure has already shipped twice — reflex's `HOME_STATE_STREAM` loop never reclaimed at
all (199 events lost), and the conscious engine reclaimed but only logged the count, re-claiming
the same 4 entries every 60s forever.

The same file already does it correctly for its *other* loop: `run()` reclaims `STREAM` /
`reflex-engine` via `reclaim_replayable()` every `_PEL_RECLAIM_EVERY` iterations. Only the
`EVENTS_STREAM` / `EVENTS_GROUP` loop was left out. The blast radius is small per-event but
permanent: a TriggerFired that fails (SLM unavailable, domain-service timeout, notification
dispatch error) is a proactive action the user asked for and never gets.

Note that `_handle_trigger_fired` deliberately isolates Path B (SLM reasoning) inside its own
`try`, so most SLM failures never reach the outer handler. The losses are the ones that do:
`json.loads` on a malformed payload, `TriggerFired.model_validate`, and any failure inside Path A
(`publisher.publish`).

Scope note: `EVENTS_STREAM` is a shared stream with several consumer groups. This ticket is about
`reflex-trigger-fired` only; audit the others separately.

## Acceptance Criteria
- [ ] `_consume_trigger_fired` reclaims `EVENTS_GROUP` on a cadence (mirror the `_PEL_RECLAIM_EVERY`
      pattern already in `run()`), and **processes** what it reclaims rather than only logging a count.
- [ ] `reclaim_replayable()` (not `reclaim_stale()`) — a TriggerFired is an instruction to *act*,
      and acting on an hours-old one drives the system from history. This is the opposite of the
      Memory Ingestor's deliberate exception.
- [ ] A payload that can never parse (bad JSON, failing `model_validate`) is ACKed and dropped, or
      it is reclaimed for eternity. Today `_handle_trigger_fired` also returns early without ACKing
      when `event` is missing or `event_type != "trigger_fired"` — the outer loop ACKs those, which
      is correct; keep it that way.
- [ ] Consider the delivery-attempt cap the Memory Ingestor now carries
      (`_MAX_DELIVERY_ATTEMPTS` + `INGEST_ATTEMPTS_KEY`, `core/memory/ingestor.py`): an entry that
      parses but fails deterministically otherwise consumes the whole reclaim budget every pass and
      starves everything behind it in the PEL.
- [ ] Test: an entry whose handling raises is redelivered by a later reclaim pass and eventually
      ACKed — the double in `tests/core/memory/test_ingestor_reclaim.py` (`FakeRedis`) is the model.

## Related
- `docs/superpowers/specs/2026-09-03-passive-observation-design.md` — the review that surfaced this.
- `core/memory/ingestor.py` + `tests/core/memory/test_ingestor_reclaim.py` — the fixed shape to copy.
- `shared/redis_streams.py` — `reclaim_stale()` / `reclaim_replayable()`.
