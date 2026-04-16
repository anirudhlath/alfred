# D29: Re-index Routines from YAML Store on Startup

## Summary
Routines loaded from the YAML store at Librarian init are not indexed into `idx:context`. Only newly detected routines (from `_detect_patterns`) get indexed. This means routines survive restarts in the YAML store but become invisible to involuntary recall until re-detected.

## Context
Task 5 of D3+D4 added `context_index.index_routine()` in `_detect_patterns`, but `_detect_patterns` skips existing routine names (dedup check). After a restart, the YAML store has routines but the Redis context index is empty — involuntary recall can't surface them.

This is the root cause of the demo issue where Alfred improvised from raw episodic entries instead of referencing the Librarian's structured routine knowledge.

## Acceptance Criteria
- On Librarian init (or first consolidation cycle), iterate `routine_store.list_all()` and call `context_index.index_routine()` for each non-archived routine
- Archived routines are NOT re-indexed
- Idempotent — re-indexing an already-indexed routine is a no-op (upsert)
- Test: restart the system with YAML routines, verify involuntary recall surfaces them
