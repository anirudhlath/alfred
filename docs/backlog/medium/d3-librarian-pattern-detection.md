# D3: Librarian Pattern Detection → Procedural Memory

## Summary
Librarian detects repeated patterns in episodic memory and promotes them to procedural memory.

## Context
Needs 2+ weeks of episodic data. `consolidator.py:268-270` is TODO. Part of the fluid → crystallized intelligence lifecycle (Pillar 5).

## Acceptance Criteria
- Pattern detection runs during nightly consolidation
- Detected patterns written to procedural memory store
- Threshold configurable for promotion confidence
