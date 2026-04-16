# Routine Indexing — Appears in Involuntary Recall

**Feature:** D3 Pattern Detection — Routine Context Indexing
**Priority:** high
**Type:** integration

## Prerequisites
- Alfred server running with Conscious Engine and Librarian
- At least one routine in "candidate" state with a recognizable name and trigger pattern
- Enough conversation history to trigger the two-stage context assembly (involuntary recall)

## Test Steps
1. Confirm a candidate routine exists in the RoutineStore (e.g., `evening_dim`, trigger `20:00 daily`)
2. Trigger a Librarian consolidation cycle — this is when routines are indexed into `idx:context`
3. Send a message to Alfred that semantically relates to the routine (e.g., "what do you usually do with the lights in the evening?")
4. Check the Conscious Engine logs for "Involuntary recall" entries that include the routine's name
5. Verify Alfred's response incorporates knowledge of the detected routine without requiring an explicit memory recall tool call

## Expected Result
- After Librarian runs, the routine appears in `idx:context` with `type="routine"`
- A semantically related query surfaces the routine in the involuntary recall stage (Stage 1 of two-stage context assembly)
- Alfred's response references the routine naturally (e.g., "I've noticed you tend to dim the lights around 8pm")
- The Conscious Engine does NOT need to call the `recall_memories` tool explicitly to surface this information

## Notes
- Routine entries are indexed with content: "Routine (candidate): {name} — {trigger_pattern}. Steps: {steps}. Confidence: {confidence:.2f}"
- On archive or dormancy, the routine is removed from the index via `_remove_routine_from_index()`
- If the Librarian has not run since the routine was created, indexing will not have happened yet — trigger a manual cycle
- The `ContextIndexManager.index_routine()` call is inside the pattern-detection loop — it only fires when a routine transitions to/stays in candidate/active state
