# D4: Librarian Decay Processing

## Summary
`_apply_decay()` currently returns 0. Need XTRIM + archival for episodic memory aging.

## Context
Hot store grows unbounded without decay. Need to move aged entries to cold store (sqlite-vec) and trim the Redis stream.

## Acceptance Criteria
- Decay scoring based on age + access frequency
- Hot → cold migration for decayed entries
- XTRIM on episodic stream after migration
