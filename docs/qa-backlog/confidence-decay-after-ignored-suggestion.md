# Confidence Decay After Ignored Routine Suggestion

**Feature:** D3 Pattern Detection — Confidence Decay on Ignored Suggestions
**Priority:** medium
**Type:** functional

## Prerequisites
- Alfred server running with Librarian enabled
- A candidate routine with `last_suggested` set to a timestamp more than 24 hours ago (past the suggestion cooldown)
- The routine has NOT been accepted (state remains "candidate", no corresponding trigger created)
- `_routine_decay_per_cycle` and `_routine_archive_threshold` configured (defaults in Librarian)

## Test Steps
1. Note the starting confidence value of the candidate routine in the RoutineStore
2. Trigger a Librarian consolidation cycle (or wait for the nightly run)
3. After the cycle, read the routine from the RoutineStore and check its `confidence` field
4. Verify it has decreased by exactly `_routine_decay_per_cycle`
5. Repeat across multiple cycles until confidence drops below `_routine_archive_threshold`
6. After the threshold-crossing cycle, verify the routine state is now "archived"
7. Verify the routine has been removed from `idx:context` (a search for its name should return no results)

## Expected Result
- Each Librarian cycle where the routine's `last_suggested` is past the 24h cooldown and no acceptance has occurred reduces confidence by `_routine_decay_per_cycle`
- Once confidence falls below `_routine_archive_threshold`, state transitions to "archived" in the same cycle
- Archived routines are removed from the context index immediately
- Librarian logs show: "Routine '{name}' archived (confidence=X.XX below threshold Y.YY)"

## Notes
- Decay only applies to routines in "candidate" state — "active" routines follow the consecutive-misses path instead
- If `last_suggested` is None (never suggested), no confidence decay occurs in that cycle
- The 24-hour cooldown check uses: `(now - last_suggested).total_seconds() / 3600 >= cooldown_hours`
- Routines suggested but immediately responded to (within cooldown) will NOT decay — the cooldown acts as a grace period
- This is distinct from the consecutive-misses path which transitions to "dormant" after 3 misses
