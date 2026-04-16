# Compression Summary Quality at Cold Migration

**Feature:** D4 Contextual Decay — LLM Compression at Cold Migration
**Priority:** medium
**Type:** functional

## Prerequisites
- Alfred server running with a valid LLM API key configured (for LiteLLM/OpenRouter)
- At least two decayable episodic entries sharing the same entity (e.g., "kitchen lights") on the same calendar date
- Librarian configured and able to run a consolidation cycle

## Test Steps
1. Seed the hot store with 3 or more entries for "kitchen lights" on the same day, all with low significance and age > 30 days
2. Trigger a Librarian consolidation cycle
3. After the cycle completes, query the cold store (SqliteVecStore) for entries with `source="librarian_compressed"`
4. Read the `content` field of the summary entry — verify it is a coherent, human-readable sentence
5. Query the cold store for the original entries by their IDs — verify they are present and have `compressed="yes"` in metadata
6. Verify the original entries are absent from the hot store (Redis)
7. Repeat the test with the LLM API key unset to verify the fallback (pipe-delimited concatenation) also produces a present and non-empty summary

## Expected Result
- One summary entry exists in cold store with a readable prose summary (e.g., "Kitchen lights were toggled on and off multiple times during the evening")
- All original entries are migrated to cold with `compressed="yes"` marker
- No original entries remain in the hot store after migration
- The `semantic_key` field of the summary entry is a short phrase (under 100 characters)
- Fallback summary (when no API key) is the original contents joined by ` | ` and is non-empty

## Notes
- The `_group_by_entity_date()` function groups entries sharing at least one entity on the same UTC date — grouping only occurs when 2+ entries share a bucket
- Ungrouped entries (no entities, or only one in their bucket) migrate directly without compression
- If the LLM returns malformed JSON, the fallback kicks in silently — check Librarian WARNING logs
- Cold store is at `core/memory/sqlite_vec.db` (configurable) — can be inspected directly with sqlite3
