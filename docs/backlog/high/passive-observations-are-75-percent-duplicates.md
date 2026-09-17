# Three quarters of episodic memory is the same few events repeated

## Summary

The per-entity observation debounce (`SET NX EX`, `OBSERVATION_DEBOUNCE_SECONDS`, default
300) suppresses rapid flapping but not the same state transition recurring hours apart.
On the live deployment that leaves episodic memory dominated by one unreliable device:
228 stored entries, **57 distinct contents**, and a single `media_player.macbook_pro:
unavailable → idle` event stored **98 times** — 43% of everything Alfred remembers.

## Context / Motivation

Measured 2026-09-04 against `idx:context` (228 docs, all `source: observation`):

| metric | value |
|---|---|
| entries | 228 |
| distinct contents | 57 |
| entries that duplicate another | 171 (75%) |
| most repeated | `media_player.macbook_pro: unavailable → idle` ×98 |
| next four | `spotify_scruuty` ×11, `bedroom_kitchen_echo_dot` ×8 (both directions), `anirudh_s_sonos_play` ×8 |
| distinct entities | 27 (152 `media_player`, 38 `light`, 32 `switch`, 6 `person`) |

`media_player` entities that drop to `unavailable` when a device sleeps and return to
`idle` when it wakes generate a permanent low-value oscillation. Each pass writes a fresh
episodic entry with its own embedding.

Two consequences, both observed:

1. **Recall precision.** The duplicates crowd the ranking. Searching `did someone arrive
   home` returns `media_player.macbook_pro: unavailable → idle` three times at 0.370,
   while `person.anirudh_lath: not_home → home` — which is in memory — does not surface.
   See `docs/backlog/high/involuntary-recall-threshold-too-high.md`.
2. **Novelty scoring.** Novelty is `1/count` over `ZINCRBY`'d entity frequency, so a
   flapping device drives its own novelty toward zero — working as designed — but it
   still costs an entry, an embedding and an index slot every time.

## Acceptance Criteria

- [ ] A repeated identical transition for the same entity does not accumulate unbounded
      duplicate episodic entries. Options to weigh: debounce on `(entity, old, new)`
      rather than entity alone; collapse on write when an identical summary exists within
      a window; or exclude `unavailable` transitions, which carry no behavioural signal.
- [ ] Whatever the mechanism, a genuinely repeating *behaviour* (lights off at 23:00 every
      night) must still be recorded often enough for pattern detection to see it — the fix
      must not suppress the signal the feature exists to capture.
- [ ] Backfill decision recorded: the existing 171 duplicate entries are either pruned or
      deliberately left.

## Notes

`unavailable` as a transition endpoint is the strongest single signal available for
"this is device noise, not behaviour" — 98 of the top 141 duplicates involve it. Filtering
it is the cheapest fix, but it is a heuristic about Home Assistant semantics rather than a
general rule, so it belongs behind a named constant with this ticket cited.
