# Cold Migration Can Never Fire — Decay Pressure Is Bounded Below The Threshold

**Priority:** high
**Source:** Passive Observation code review, 2026-09-03 (adjacent finding — logged, not fixed on that branch)

## Summary
`Librarian._apply_decay` (`core/librarian/consolidator.py:684`) migrates an entry to cold
storage when `pressure > decay_migration_threshold`. `pressure` is bounded above by `1.0` and
the threshold defaults to exactly `1.0`, so the comparison is never true. **No episodic entry has
ever been migrated out of hot storage in production, and none ever will at the shipped defaults.**

## Context / Motivation

The formula (`core/librarian/consolidator.py:673-681`, docstring at 620-637):

```python
age_factor = min(age_days / 30.0, 1.0)
retrieval_recency = exp(-days_since_last_retrieved / 7.0)
retrieval_frequency = min(log2(retrieval_count + 1) / 5.0, 1.0)

pressure = (
    age_factor
    - significance * 2.0
    - retrieval_recency * 1.5
    - retrieval_frequency * 1.0
)

if pressure > decay_migration_threshold:
    to_migrate.append(result)
```

Term by term:

- `age_factor` is `min(…, 1.0)` — **at most 1.0**.
- `significance` is `SignificanceScore.overall`, a weighted sum of four non-negative dimensions
  with positive weights (`core/memory/significance.py:42-47`) — **never negative**, so the term is
  subtracted or zero.
- `retrieval_frequency` is `min(log2(count + 1) / 5.0, 1.0)` with `count >= 0` — **never negative**.
- `retrieval_recency` is `exp(-d / 7.0)`, which is **strictly positive** for every finite `d`
  (it only underflows to `0.0` past ~5215 days, i.e. ~14 years).

So `pressure <= 1.0 - 0 - (something > 0) - 0 < 1.0`, always. Measured, never-retrieved,
zero-significance entries — the most migratable case that exists:

| age | significance | retrieval_count | pressure | `> 1.0`? |
|---|---|---|---|---|
| 30 d | 0.0 | 0 | 0.97935431990042443 | no |
| 60 d | 0.0 | 0 | 0.99971583726215063 | no |
| 365 d | 0.0 | 0 | 1.00000000000000000 | no |
| 100 y | 0.0 | 0 | 1.00000000000000000 | no |

It converges on the threshold and never crosses it.

**The threshold really is 1.0 in production.** `decay_migration_threshold` defaults to `1.0` in
*both* places it is declared — `shared/config.py:116` and the `Librarian.__init__` signature
(`core/librarian/consolidator.py:137`) — and **neither production call site passes it**:
`core/librarian/__main__.py:71` and `core/conscious/__main__.py:311` construct `Librarian(...)`
without the argument. The `AlfredConfig` field is dead: `grep -rn decay_migration_threshold`
finds no reader outside the consolidator's own default and the test suite. So even setting it in
config today would change nothing — that is a second bug, and the fix has to wire the config
value through as well as lower it.

**The design spec contradicts itself on exactly this point.**
`docs/superpowers/specs/2026-04-16-d3-d4-pattern-detection-decay-design.md` line 116 gives the
canonical migrating example — *"30d old, low sig (0.1), never retrieved … pressure ~0.8 …
**Migrates (threshold < 1.0)**"* — while line 127 says *"`decay_migration_threshold` remains
configurable in `AlfredConfig` (default `1.0`)"*. The formula was designed for a threshold below
1.0 and shipped with one equal to the unreachable ceiling. (Recomputing the spec's row: `1.0 -
0.2 - 1.5*exp(-30/7) = 0.7794` — the spec's arithmetic is right; only the default is wrong.)

**The tests do not catch it** because every test that asserts a *migration* passes
`decay_migration_threshold=0.5` explicitly (`tests/core/librarian/test_consolidator_v2.py:613,
725, 1224, 1239, 1261, 1282, 1297`). The three that pass `1.0` (`:659, :684, :744`) all assert
`migrated == 0` — they are "spared" cases and pass for the wrong reason, as does the one that
takes the default (`:701`). The migration test at `:613` even carries the comment *"pressure ≈
1.0 - 0.2 - 0.02 - 0.0 = 0.78 > threshold=0.5"*: the arithmetic that proves the ceiling is
written down in the suite, and only the production default was never compared against it. `D4` is
recorded as `docs/backlog/medium/d4-librarian-decay-processing.md` — **COMPLETED**.

**Why it matters now.** Passive observation
(`docs/superpowers/specs/2026-09-03-passive-observation-design.md`) begins writing ~200–300
episodic entries a day. Its Risks section states, of that volume: *"Existing decay and
cold-migration handle it."* They do not. At ~250/day roughly 7,500 entries a month accumulate in
the Redis hot store with no eviction path — every one of them a HNSW vector in `idx:context`,
searched on every involuntary context assembly. Before this branch the hot store grew slowly
enough that nobody noticed; after it, the growth is the dominant memory cost in the system.
Cold storage (`SqliteVecStore`) exists, is tested, and has never received an entry from decay.

Compression at cold migration (`_compress_and_migrate`, `_group_by_entity_date`) is downstream of
the same branch, so it has also never run in production.

## Acceptance Criteria
- [ ] `decay_migration_threshold` has a default that the formula can actually reach. Derive it
      from the spec's behaviour table rather than picking a round number: the spec intends a 30-day,
      `significance=0.1`, never-retrieved entry to migrate (`pressure ≈ 0.78`) and a 60-day,
      `significance=0.3`, count=1 entry to be borderline (`pressure ≈ 0.18`). Change it in
      **both** `shared/config.py` and the `Librarian.__init__` default, or they drift again.
- [ ] `Librarian` is constructed with `decay_migration_threshold=config.decay_migration_threshold`
      at both production call sites (`core/librarian/__main__.py`, `core/conscious/__main__.py`),
      so the config field stops being dead. Audit the sibling knobs while there —
      `pattern_min_occurrences`, `pattern_min_days`, `pattern_confidence_threshold`,
      `routine_decay_per_cycle`, `routine_archive_threshold`,
      `routine_suggestion_cooldown_hours` are declared in `AlfredConfig` and passed at neither
      call site either.
- [ ] A test asserts the invariant directly, not just an example: with the **production default**,
      an old, zero-significance, never-retrieved entry migrates. A property/parametrized test over
      the four inputs that pins `max(pressure) < 1.0` would document why `1.0` was wrong.
- [ ] `source="observation"` entries are checked specifically. They score `safety=0.0`,
      `personal=0.3` (the `case _` fallback), `emotional=0.2`, and a novelty that decays toward 0,
      putting `overall` between **0.355** on a first sighting and **0.105** once novelty is
      saturated — so `significance * 2.0` costs them at most 0.71 and usually ~0.21. They should be
      the first thing a working threshold sweeps out.
- [ ] Decide what to do about the entries already stranded in hot storage (a one-off backfill pass,
      or just let the corrected threshold sweep them on the next consolidation cycle).
- [ ] Re-check the "Volume growth" risk in the passive-observation spec once this lands — that
      spec's mitigation is this mechanism, and it currently has an as-built note saying so.

## Related
- `core/librarian/consolidator.py` — `_apply_decay` (612-724), constructor default (137),
  call site (1057).
- `shared/config.py:116` — the unread `decay_migration_threshold` field.
- `core/librarian/__main__.py:71`, `core/conscious/__main__.py:311` — the two production
  constructions that omit it.
- `docs/superpowers/specs/2026-04-16-d3-d4-pattern-detection-decay-design.md` — the formula's
  design, including the behaviour table that requires `threshold < 1.0`.
- `docs/superpowers/specs/2026-09-03-passive-observation-design.md` — Risks, "Volume growth";
  relies on this mitigation.
- `docs/backlog/medium/d4-librarian-decay-processing.md` — marked COMPLETED; update or close it
  when this lands.
- `docs/backlog/high/reflex-events-group-pel-reclaim.md` — the other finding from the same review.
