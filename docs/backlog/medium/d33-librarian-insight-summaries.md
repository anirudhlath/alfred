# D33: Librarian Insight Summaries

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

## Dependencies
- D29 (reindex on startup) — same pattern applies to insights
