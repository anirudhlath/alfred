# D32: Routine Deviation Detection

## Summary
When an automated routine fires and the user manually overrides the result within a short window, Alfred should notice the deviation and offer to update the routine.

## Context
If `evening_dim` triggers and sets lights to 30%, but the user manually adjusts to 50% five minutes later, that's a signal. Alfred should say: *"I notice you've adjusted the lights to 50% this evening. Shall I update your preference?"*

This is the crystallized-to-fluid feedback loop. Crystallized behavior (automated trigger) encounters a novel situation (user deviation), which triggers fluid reasoning (ask about the change), which may update the crystallized knowledge.

## Acceptance Criteria
- After a trigger fires an action, record the expected state (entity + value + timestamp)
- Monitor the event bus for state changes on the same entity within a configurable window (default: 30 minutes)
- If a user-initiated change contradicts the trigger's action, write a `deviation` observation to the scratchpad
- The Librarian's next consolidation cycle detects deviation patterns and adjusts routine confidence or parameters
- If deviation is immediate (< 5 min) and happens 3+ times, Alfred proactively asks about updating the routine
- Test: fire a trigger, publish a contradicting state change, verify deviation is logged

## Dependencies
- Active routines with triggers (routine promotion must be working)
- Event bus monitoring for specific entities
