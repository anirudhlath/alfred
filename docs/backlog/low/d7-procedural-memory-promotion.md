# D7: Procedural Memory Promotion + Hit Rate

## Summary
No state transitions (candidate → active → archived) for procedural memory. No hit tracking.

## Context
Procedural memories should have a lifecycle: promoted from patterns (D3), tracked for hit rate, and archived if unused.

## Acceptance Criteria
- State machine: candidate → active → archived
- Hit rate tracking per procedural memory
- Auto-archive after configurable inactivity period
