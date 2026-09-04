# D33: Librarian Insight Summaries

**Status:** blocked — deferred deliberately, see "Blocked on" below.

## Summary
The Librarian should produce non-actionable behavioral insights alongside routine detection — observations about the user's patterns that aren't automatable but help Alfred understand and converse more naturally.

## Context
Currently `_detect_patterns` only outputs `RoutineSpec` objects (repeating actions on a schedule). But the Librarian has access to all episodic data and can observe higher-level patterns:

- "User appears to work from home on Mondays and Fridays"
- "Security awareness is high — front door lock consistency is 82%"
- "Jazz in the kitchen correlates with cooking between 17:00-19:00"
- "User's activity drops significantly on Sundays — likely a rest day"

These aren't routines to automate. They're understanding — the kind of knowledge that makes Alfred feel *knowing* rather than merely *responsive*.

## Acceptance Criteria
- Add a second LLM call (or extend the existing pattern detection call) that produces `InsightSpec` objects
- InsightSpec: `name`, `observation` (natural language), `confidence`, `learned_from` (episode IDs), `category` (lifestyle/preference/schedule/security)
- Insights indexed into `idx:context` with `type="insight"` for involuntary recall
- Insights have a staleness window — re-evaluated each consolidation cycle, updated or archived
- The Conscious Engine can reference insights naturally in conversation
- Test: consolidate episodic data, verify insights are produced and surface in recall

## Blocked on

**Originally: there was no data to draw insights from.** As of 2026-09-03
episodic memory held 0 entries. The spec puts the count of `state_changed` events
Alfred had consumed and recorded none of at 404,000. The cause was structural:
`ReflexObservation` required `action` and `result`, so an event Alfred saw but did
not act on was unrepresentable and was dropped on the early return in
`process_stream_entry()`. Every insight example above ("works from home on
Mondays", "jazz correlates with cooking") required exactly the observations that
were being discarded.

**That blocker is cleared.** Passive observation
(`docs/superpowers/specs/2026-09-03-passive-observation-design.md`, branch
`feat/passive-observation`) made `action`/`result` optional, and
`observe_passively()` now publishes a per-entity-debounced `ReflexObservation` on
the no-action path. The Memory Ingestor writes those as `source="observation"`
with a `[observation] {entity}: {old} → {new}` summary. Expected steady state is
~200–300 entries/day, so the consolidation window stops being empty as soon as
that lands.

Building the interpretive pass first would still repeat the mistake that produced
the rest of the unused memory machinery — a decay formula tuned for recall counts
that were structurally frozen at 0, compression that has never compressed an
entry, a routine lifecycle whose `active` state is unreachable. All built ahead of
their data, all correct-looking, none load-bearing. So this stays **blocked, on
evidence rather than on plumbing**:

- **~7 days of accumulated observations**, then the Review point below answered
  from real entries on the Memory page. Recording started only when passive
  observation merged; the clock starts there, not at ticket-filing.
- **Confirmation that the entries are worth interpreting.** The debounce is
  per-entity and five minutes wide, and one flapping device was 64% of qualifying
  events in the measured window — if the episodic tab turns out to be 200 lines a
  day of `media_player.macbook_pro`, the fix is `OBSERVATION_DEBOUNCE_SECONDS` or
  the attention set, not a second LLM pass over noise.
- **`docs/backlog/high/librarian-decay-threshold-unreachable.md`.** Nothing has
  ever migrated out of hot storage, so the volume this ticket now depends on also
  accumulates without an eviction path. Worth resolving before adding a consumer
  that reads the whole window each cycle.

What D33 still needs beyond that is unchanged: the `InsightSpec` model, the LLM
pass (extended or second — question 2 below), `type="insight"` indexing in
`idx:context`, the staleness/re-evaluation loop, and the surfacing described further
down. Passive observation supplies the *input*; none of that machinery exists yet.

## Review point — what decides whether this gets built

After ~7 days of passive observation, open the Memory page (`MemoryPage.tsx`,
`GET /api/admin/memory/episodic`) and answer three questions with real entries in
front of you:

1. Are the conclusions you want **already obvious** from reading the raw
   episodic entries? If so, this ticket is unnecessary — the value was in
   recording, not interpreting.
2. Does the existing `_detect_patterns` output already capture them as routine
   candidates? If so, extend that call rather than adding a second one.
3. If an interpretive pass *is* needed, which categories actually matter in this
   house? The four guessed above (lifestyle/preference/schedule/security) were
   written with no data behind them and should be re-derived, not inherited.

Answering these from evidence is the entire point of deferring.

## Surfacing (decided 2026-09-03)

When built, insights surface three ways — on-demand recall, a proactive
INFORMATIONAL notification above a confidence threshold, and an admin endpoint
(`GET /api/admin/memory/insights`) for the web UI. A flat-file digest was
considered and rejected.

## Dependencies
- D29 (reindex on startup) — same pattern applies to insights
- Passive observation (spec `2026-09-03-passive-observation-design.md`) — hard
  prerequisite, **satisfied** on branch `feat/passive-observation`; the
  consolidation window now fills at ~200–300 entries/day
- `docs/backlog/high/librarian-decay-threshold-unreachable.md` — cold migration
  has never fired, so that window only grows
