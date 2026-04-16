# Librarian Detects Repeated Reflex Action Patterns

**Feature:** D8 — Librarian enhanced pattern detection for Reflex actions
**Priority:** high
**Type:** integration

## Prerequisites
- Alfred unified runner started with Conscious Engine (requires `OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY`)
- Redis Stack running with episodic memory populated
- At least 3 reflex observations for the same tool/entity combination across 7+ different calendar days seeded into episodic memory
- Librarian consolidation interval set low for testing (e.g., `LIBRARIAN_INTERVAL_SECS=60` in `.env`)

## Test Steps
1. Seed episodic memory with repeated reflex entries for the same pattern across multiple days. Use the following Redis command template to insert synthetic entries (or let the system accumulate real data over 7 days):
   - Entries should have `source=reflex`, same `tool_name`, same `entity_id`, spread across timestamps at least 7 days apart
2. Trigger a Librarian consolidation run by waiting for the scheduled interval or sending a drain action to `alfred:actions`
3. Monitor the Conscious Engine logs for `"Detected N pattern candidates"` or similar Librarian output
4. After consolidation completes, query the routine store for newly created `candidate` routines:
   - Check the semantic memory directory for a new routine YAML or check via the `/memory/routines` API endpoint if available
5. Verify the detected routine references the reflex entries in its `learned_from` field
6. Verify the routine's `trigger_pattern` corresponds to the timing of the seeded observations (e.g., `"20:00 daily"`)
7. Confirm the Librarian log message calls out `source: reflex` entries specifically (look for `"PAY SPECIAL ATTENTION"` context having produced a reflex-sourced routine)

## Expected Result
- The Librarian identifies reflex patterns occurring 3+ times over 7+ days and creates a `candidate` RoutineSpec
- The `learned_from` array in the routine contains IDs matching the seeded episodic entry IDs
- The `confidence` score on the pattern is >= 0.6
- No duplicate routines are created for the same pattern on subsequent consolidation runs
- Reflex entries that are sporadic (< 3 occurrences) do not produce candidate routines

## Notes
- This test requires a real Claude API call via LiteLLM — it cannot be fully validated with mocks
- The pattern detection prompt specifically instructs the LLM to flag reflex actions that are "unnecessary or counterproductive" — validate that the routine description field reflects reflex-aware analysis language
- If the API key is absent, Librarian skips pattern detection silently and returns empty candidates — confirm this fallback is working without errors
- Known limitation: the 7-day spread requirement makes this slow to accumulate organically; use time-backdated synthetic entries for testing
