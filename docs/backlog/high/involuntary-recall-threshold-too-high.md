# Involuntary recall's 0.5 threshold filters out correct memories

## Summary

`INVOLUNTARY_RECALL_THRESHOLD` defaults to 0.5 and is passed as `min_similarity` to the
Conscious Engine's automatic context assembly. Measured against the live index with the
deployed embedding model, natural phrasings of questions Alfred *can* answer score below
it and return nothing — including cases where the correct entry is demonstrably present.

## Context / Motivation

Measured 2026-09-04 against the live `idx:context` (228 entries,
`EMBEDDING_MODEL=google/embeddinggemma-300m`, dim 768):

| query | hits above 0.5 | best score |
|---|---|---|
| `did someone arrive home` | **0** | 0.370 |
| `is anyone playing music` | **0** | 0.399 |
| `what happened with the lights` | 3 | 0.523 |
| `the rope light in the living room` | 3 | 0.549 |
| `is anirudh home` | 5 | — |
| `anirudh got home from work` | 4 | — |

`person.anirudh_lath: not_home → home` is in memory. `did someone arrive home` does not
retrieve it: the top three results are `media_player.macbook_pro: unavailable → idle` at
0.370, one of the 98 duplicates described in
`docs/backlog/high/passive-observations-are-75-percent-duplicates.md`.

The pattern is consistent — recall succeeds when the query names the entity and fails on
generic phrasing, which is the phrasing a person actually uses. Two defaults disagree with
each other, which is worth resolving either way: `shared/config.py:120` says 0.5 while
`core/conscious/engine.py:81` says 0.4, and the wiring in `core/conscious/__main__.py:288`
means the config value (0.5) is what runs.

Note the threshold has never fired in production — `alfred:user_requests` is 0 and no
entry has a non-zero `retrieval_count`. These numbers come from driving
`ContextIndexManager.search_text` directly.

## Acceptance Criteria

- [ ] The default threshold is chosen against measured scores from the deployed embedding
      model rather than assumed, and the two disagreeing defaults are reconciled.
- [ ] `did someone arrive home` retrieves the `person.*` home transition, or there is a
      recorded reason it should not.
- [ ] The choice is documented where an operator tuning recall will find it, with the
      trade named: too low floods the prompt with irrelevant context and costs tokens on
      every turn, too high silently returns nothing.

## Notes

Fixing the duplicate problem may move these numbers on its own, so measure again after
that lands rather than tuning against the current index. A similarity floor is also the
wrong instrument if scores are not comparable across query lengths — worth checking
whether a top-k with a relative cutoff behaves better than an absolute one.
