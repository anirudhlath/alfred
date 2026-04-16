# Proactive Notification Timing and 24-Hour Cooldown

**Feature:** D3 Pattern Detection — Proactive Routine Suggestion Background Loop
**Priority:** high
**Type:** functional

## Prerequisites
- Alfred server running with the conscious process active
- A candidate routine with a time-based trigger pattern (e.g., `20:00 daily`)
- `last_suggested` is either None or more than 24 hours in the past
- Notification channel configured (Signal or WebSocket; APNs not required for this test)

## Test Steps
1. Create a candidate routine with trigger_pattern `HH:MM daily` where HH:MM is 5–10 minutes in the future
2. Confirm `last_suggested` is None on the routine
3. Wait for the system clock to enter the trigger window
4. Observe that within the next 15-minute background loop tick, a notification is published with title "Routine Suggestion"
5. Note the `last_suggested` timestamp that is now written on the routine
6. Immediately trigger the background loop check again (or wait for the next 15-minute tick)
7. Verify NO second notification is published (cooldown active)
8. Advance system time by 25 hours (or wait) and trigger the loop again
9. Verify a new notification IS published after the cooldown expires

## Expected Result
- Notification arrives within 15 minutes of entering the trigger time window
- Notification body contains the routine name, trigger pattern, and asks for automation consent
- `last_suggested` is updated on the routine before the notification is published (prevents spam on publish failure)
- No duplicate notifications within the 24-hour cooldown window
- After cooldown expires, a new notification is sent if the time pattern still matches

## Notes
- The background loop runs every 900 seconds (15 minutes) — not event-driven
- `_eligible_candidates()` filters by both time pattern match AND cooldown simultaneously
- `last_suggested` is written BEFORE `notifier.publish()` to avoid duplicates if publish raises
- If `match_trigger_pattern()` returns False (outside the time window), no notification is sent regardless of cooldown
- Test with `trigger_pattern="daily"` (always matches) to isolate the cooldown logic from time-window logic
- The conscious process must have `engine.has_routine_store == True` for the loop task to be created at all
