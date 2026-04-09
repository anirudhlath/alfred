# D6: Semantic Memory Conflict Resolution

## Summary
Atomic overwrite only. No merge/conflict detection for semantic memory updates.

## Context
When two sources update the same semantic memory entry, the last write wins silently. Need conflict detection and merge strategy.

## Acceptance Criteria
- Detect conflicting updates to same memory entry
- Merge strategy (or user prompt) for conflicts
- Audit trail for overwrites
