# D35: Crystallized Routine Autonomous Execution with Reporting

## Summary
Once a routine reaches high confidence and the user has explicitly accepted it, Alfred should execute it autonomously at the scheduled time and casually inform the user afterward.

## Context
Currently, promoted routines create triggers that fire actions silently. Alfred should close the loop by reporting what he did:

> *"I've dimmed the living room, sir. Right on schedule."*

This reporting should be:
- **Unobtrusive** — INFORMATIONAL urgency, not URGENT
- **Batched** — if 3 routines fire in the same hour, one summary notification, not three
- **Contextual** — if the user is actively chatting, mention it in conversation instead of pushing a notification
- **Skippable** — after the user has seen the same report 7+ times without comment, reduce to weekly summaries

## Acceptance Criteria
- When a promoted routine's trigger fires, publish an observation to scratchpad AND send an INFORMATIONAL notification
- If the user has an active WebSocket session, deliver as a chat message instead of push notification
- Batch multiple routine reports within a 1-hour window into a single message
- After 7 consecutive unremarked reports for the same routine, switch to weekly digest
- Test: promote a routine, fire its trigger, verify notification/chat message arrives

## Dependencies
- Routine promotion to triggers (active state + ActionPayload)
- Notification system (already working)
