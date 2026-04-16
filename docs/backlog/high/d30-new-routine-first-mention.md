# D30: New Routine First-Mention in Conversation

## Summary
When the Librarian detects a new routine, Alfred should bring it up naturally in the very next conversation — not wait for a time-matched window or a background notification cycle.

## Context
Currently, routine suggestions only surface via (a) time-matched hints injected during `process_request` when the trigger pattern matches the current time, or (b) proactive push notifications every 15 minutes. If the user chats with Alfred at 2pm and a new "evening dim" routine was just detected, Alfred has no way to mention it.

The real Alfred Pennyworth would say: *"Sir, I've been observing your evening habits. You appear to dim the living room lights around 8pm nearly every night. Would you like me to handle that automatically?"*

## Acceptance Criteria
- `_detect_patterns` sets a `needs_first_mention: true` flag on newly created RoutineSpec (or a separate store/Redis key)
- `_build_routine_hint` (or a new method) checks for routines with `needs_first_mention` regardless of time-match
- The hint is framed as a natural observation, not a system prompt tag
- After the first mention, the flag is cleared (idempotent — won't re-mention)
- If the user doesn't chat within 24 hours, the proactive notification path handles it instead
- Test: detect a routine, send a message at a non-matching time, verify the hint appears
