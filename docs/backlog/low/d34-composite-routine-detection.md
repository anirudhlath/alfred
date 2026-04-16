# D34: Composite Routine Detection (Meta-Patterns)

## Summary
The Librarian should detect when multiple routines co-occur temporally and group them into composite routines that Alfred can reference holistically.

## Context
Individual routines:
- `evening_dim` fires at 20:00
- `lock_front_door` fires at 22:30
- `set_bedroom_temp` fires at 22:00

These share a "wind-down" temporal context. The Librarian should detect this cluster and create a composite: "evening wind-down" = dim + temp + lock. Alfred can then reference it as a unit: *"Shall I start your evening wind-down, sir?"*

This is a second-order pattern — patterns of patterns. It requires the individual routines to be detected first (D3), then their temporal co-occurrence analyzed.

## Acceptance Criteria
- After routine detection, run a clustering pass on active/candidate routines
- Routines within a configurable temporal window (default: 3 hours) on the same days form a composite candidate
- Composite stored as a `CompositeRoutineSpec` (or RoutineSpec with sub-steps referencing other routines)
- Composite indexed in `idx:context` for involuntary recall
- Conscious Engine can trigger all sub-routines with a single user confirmation
- Test: detect 3+ routines in the same evening window, verify composite is created

## Dependencies
- D3 (pattern detection) — individual routines must exist first
- D31 (contextual composition) — the execution model for composites
