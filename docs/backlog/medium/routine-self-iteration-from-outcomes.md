# Routine Self-Iteration from Execution Outcomes

## Summary
Crystallized routines should improve themselves based on execution outcomes. When a promoted routine fires, track whether it succeeded, failed, or was overridden by the user — and let the Librarian revise the RoutineSpec (conditions, actions, timing) from that outcome history.

## Context
Hermes Agent (Nous Research) self-iterates its skill documents when it discovers better approaches during use. Alfred has the fluid→crystallized lifecycle (pattern detection → RoutineSpec promotion) but crystallized routines are static after promotion — the only feedback loop today is confidence decay on *ignored suggestions*, not on *executed routines*.

Closing this loop completes the Fluid Intelligence pillar: System 1 executes, outcomes flow back through episodic memory, and the Librarian refines procedural memory the same way it created it. Per Pillar 4, revisions must go through the Librarian consolidation pipeline — routines are never edited inline at runtime.

Examples of outcome signals:
- **Success** — action completed, no user correction within a window
- **Override** — user reverses the action shortly after (e.g. turns the lights back up after evening_dim fires)
- **Failure** — ActionRequest errored or the target entity was unavailable

## Acceptance Criteria
- Execution outcomes for promoted routines are recorded (episodic memory entries tagged with routine ID + outcome, or a dedicated outcome log)
- User overrides are detected: a contradicting state change on the same entity within a configurable window after a routine fires counts as an override
- Librarian consolidation reads outcome history per routine and can: adjust confidence, tweak trigger conditions/timing, or demote the routine back to suggestion status after repeated overrides
- Revised routines are re-indexed in `idx:context` and their YAML rewritten only by the Librarian (never mid-execution)
- Test: simulate a routine firing followed by a user override N times → verify the Librarian demotes or revises it

## Dependencies
- Routine promotion to triggers (active state + ActionPayload)
- TriggerFired provenance (`fired_by`) — merged in web-app-rebuild PR #21
- Relates to: D31 (routine-aware contextual actions), D32 (deviation detection), D35 (crystallized autonomous execution)
