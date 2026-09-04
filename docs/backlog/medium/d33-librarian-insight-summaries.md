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

**There is no data to draw insights from.** As of 2026-09-03 episodic memory
holds 0 entries. Alfred has consumed 404,000 `state_changed` events and recorded
none of them: `ReflexObservation` requires `action` and `result`, so an event it
saw but did not act on is unrepresentable and gets dropped. Every insight
example above ("works from home on Mondays", "jazz correlates with cooking")
requires exactly the observations that are being discarded.

Blocked on `docs/superpowers/specs/2026-09-03-passive-observation-design.md`.
Building the interpretive pass first would repeat the mistake that produced the
rest of the unused memory machinery — a decay formula tuned for recall counts
that were structurally frozen at 0, compression that has never compressed an
entry, a routine lifecycle whose `active` state is unreachable. All built ahead
of their data, all correct-looking, none load-bearing.

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
  prerequisite; without it the consolidation window is empty
