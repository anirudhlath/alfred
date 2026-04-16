# Decay Formula Preserves Important Memories

**Feature:** D4 Contextual Decay — Subtractive Pressure Formula
**Priority:** high
**Type:** functional

## Prerequisites
- Alfred server running with Redis Stack and Librarian enabled
- At least two groups of episodic memory entries in the hot store:
  - Group A: high-significance entries (significance >= 0.7) that are 35+ days old
  - Group B: low-significance entries (significance <= 0.2) that are 35+ days old, never retrieved
- Librarian configured with default `decay_migration_threshold=1.0`

## Test Steps
1. Seed the hot store with Group A entries (high significance, old, low retrieval count)
2. Seed the hot store with Group B entries (low significance, old, zero retrievals)
3. Trigger a Librarian consolidation cycle manually (or wait for the nightly run)
4. After consolidation completes, query the hot store for Group A entries
5. Query the cold store to confirm Group B entries were migrated
6. Optionally: seed a Group C with recently-retrieved entries (last_retrieved within 7 days) and confirm they stay in hot

## Expected Result
- Group A entries (high significance) remain in the hot store — their `significance * 2.0` term keeps pressure below 1.0
- Group B entries (low significance, never retrieved) are migrated to cold — pressure exceeds 1.0
- Group C entries (recently retrieved) remain in hot — the `retrieval_recency` term (exp decay over 7 days) reduces pressure below threshold
- Librarian logs show "Decay: queued entry" only for Group B / Group C-expired entries

## Notes
- The formula is: `pressure = age_factor - significance*2.0 - retrieval_recency*1.5 - retrieval_frequency*1.0`
- A 35-day-old entry with significance=0.7 yields: `1.0 - 1.4 - ~0 - 0 = -0.4` — should NOT migrate
- A 35-day-old entry with significance=0.1, zero retrievals yields: `1.0 - 0.2 - ~0 - 0 = 0.8` — should NOT migrate at default threshold 1.0 (threshold is > not >=)
- Use significance=0.0 and age=35+ days for a definitive positive migration test
- Check Redis for `alfred:context:{id}` key absence to confirm hot-store removal
