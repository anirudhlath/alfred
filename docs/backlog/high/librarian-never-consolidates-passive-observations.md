# The Librarian never runs, so nothing is learned and nothing decays

## Summary

`consolidate()` returns early when the scratchpad queue is empty. Since the Reflex
Runner stopped writing to the scratchpad, the queue's only producers are the Conscious
Engine (per user request) and the Trigger Engine (per fire). On a house where nobody has
spoken to Alfred yet, both are zero, so **every** Librarian cycle exits at step 1 —
and pattern detection, routine lifecycle, conflict resolution and contextual decay all
sit behind that return. Passive observations reach episodic memory and are never
consolidated into anything.

## Context / Motivation

Measured on the live deployment 2026-09-04, ~21h uptime:

- `alfred:reflex:observations` — 246 entries, ingestor group `pending 0, lag 0`.
  `idx:context` holds 228 documents, `hash_indexing_failures 0`. The recording half of
  the passive-observation feature (PR #192) works.
- `alfred:librarian:queue` — `LLEN 0`. `alfred:scratchpad:queue` — `LLEN 0`.
- Every cycle logs `Scratchpad empty — nothing to consolidate` /
  `{'entries_processed': 0, 'routines_reindexed': 0}`.

The early return is in `core/librarian/consolidator.py`, `consolidate()`:

```python
lines = await self._drain_scratchpad()
if not lines:
    logger.info("Scratchpad empty — nothing to consolidate")
    return {"entries_processed": 0, "routines_reindexed": routines_reindexed}
```

Everything downstream — `_detect_patterns`, `_update_routine_lifecycle`,
`_resolve_conflicts`, `_apply_decay` / `_compress_and_migrate` — is unreachable on this
path. Decay is the quiet casualty: cold migration never runs either, so the hot store
grows without bound independently of the learning question.

Only two non-test producers write the queue: `core/conscious/engine.py:791` and
`core/triggers/engine.py:85`. The Reflex Runner publishes `ReflexObservation` to
`alfred:reflex:observations` instead (see the CLAUDE.md gotcha "Reflex Runner no longer
writes to scratchpad"), and the Memory Ingestor writes those to episodic memory —
bypassing the scratchpad entirely.

`docs/architecture.md` §3.7.1 states passive observations exist because "the Librarian's
pattern detection, which reads episodic entries, had nothing to run over". The
consolidator has **zero** references to `recall(`; it touches `_episodic_memory` only for
`copy_to_cold_and_remove`. The premise the feature was justified on is not implemented.

## Acceptance Criteria

- [ ] A Librarian cycle detects patterns over passive observations without requiring a
      user conversation to have happened first.
- [ ] Decay / cold migration runs on a cycle where the scratchpad is empty but episodic
      memory is not — the two inputs are decoupled.
- [ ] `docs/architecture.md` §3.7.1 either describes the implemented input or is
      corrected; the claim that pattern detection reads episodic entries must be true or
      removed.
- [ ] A test drives a consolidation cycle with an empty scratchpad and a populated
      episodic store, and asserts the downstream steps ran.

## Notes

Related but distinct: `docs/backlog/high/librarian-decay-threshold-unreachable.md` shows
the decay *formula* can never exceed its threshold. This ticket is upstream of that — the
decay pass is not reached at all. Fixing either alone leaves cold migration dead.

Decide deliberately whether the Librarian should read episodic memory directly or whether
the ingestor should also fan out to the queue. The second is a smaller change but
reintroduces the second-consumer pattern `.claude/rules/architecture.md` warns about
("never add a second consumer to `alfred:scratchpad:queue`"), so the first is likely
correct — but it changes what "drain" means for crash recovery, which the current
`RENAME`-to-processing-key design depends on.
