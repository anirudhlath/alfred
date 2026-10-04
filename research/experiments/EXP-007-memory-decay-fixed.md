---
id: EXP-007
title: Memory Decay After the Fixes — Selection, Exact Search, Cosine Cold, Archive Recall
status: complete
start_date: 2026-10-03
end_date: 2026-10-03
---

# EXP-007: Memory Decay After the Fixes

Follows [EXP-006](EXP-006-memory-decay.md), which found that PR #247's decay barely
ran and that nothing it moved could be recalled. Every problem EXP-006 ranked was
fixed on the same branch before this run:

| # (EXP-006) | Problem | Fix |
|---|---|---|
| 1 | `FT.SEARCH` without `LIMIT` returned 10 per field | Every hot search sends `LIMIT 0 <limit>` |
| 3 + 2 | Candidates chosen by similarity to a placeholder phrase, with a 0.0 floor | `decay_candidate_ranges()` → `VectorStore.select()`: every episodic entry with significance below `(1 − threshold) / 2`, a range that holds everything the formula could move |
| 6 | HNSW's greedy walk trapped by duplicate vectors | Hot KNN runs `HYBRID_POLICY ADHOC_BF` (exact) |
| 4 | `memory_recall_memories` searched hot only | `ContextIndexManager.recall()` adds the cold archive |
| 7 | Cold `vec0` tables measured L2, read as cosine | Built `distance_metric=cosine`; schema v3 rebuilds older tables |
| 5 | Migration re-embedded vectors hot already held | `copy_to_cold_and_remove()` reuses them via `VectorStore.embeddings()` |

Two more defects surfaced while fixing these and were fixed too: a race on the cold
store's first open (`PRAGMA journal_mode=WAL` fails with "database is locked" when
several connections open a fresh file at once — about 12% of the time with four
openers), and compression grouping that depended on the order candidates arrived in
(a memory naming two entities joined whichever group it met first), which made the
branch and the oracle archive 12,305 and 12,307 entries from the same 9,585.

## Hypothesis

**H1.** The branch's decay now moves what the formula marks — the same memories as the
scan-everything oracle — at a lower cost per migrated memory.

**H2.** Memories that leave hot stay reachable by deliberate recall: both
`memory_recall_memories` and `EpisodicMemory.recall` find what the cold store alone
finds.

**H3.** With exact search, deliberate recall of hot targets matches an exact ranking.

**H4.** Involuntary hit@10 under decay is no worse than without it. (EXP-006 rejected
this for the oracle: −9 points at day 60, from one-off details the formula retires.)

## Method

Identical to EXP-006 (seed 7, 60 days, `google/embeddinggemma-300m` on CPU, Redis 8.2
per policy, probes at days 30, 45 and 60), on the branch head with the fixes. The
`branch` policy now selects by metadata; `no_decay` still runs threshold 1.0, where
the pass makes no query at all.

A second, scratch run measured the backlog pass: no decay for 59 days, then the
branch's pass hourly from day 60 — the first pass after deploying onto a hot store
that never decayed.

## Results

Run `2026-10-04T032859_memory-google--embeddinggemma-300m` (rows in
`research/data/memory-decay.csv`). The run before the grouping fix,
`2026-10-04T025923`, differed only in the oracle's archive: 12,307 entries instead of
12,305, and at day 30 its cold store alone found 2 of 3 departed targets instead of 1.
Redis 8.2.10, CPU embeddings, 1,440 passes per policy, 26,632 write-path embeds each.

### Headline (day 60, EXP-006 in brackets)

| policy | involuntary hit@10 | vs no_decay | hot episodic | routine >14d gone | stuck ≥7d | migrated | cold entries |
|---|---|---|---|---|---|---|---|
| no_decay | 76% (25/33) | — | 13,316 | 0% | 7,988 | 0 | 0 |
| branch | 67% (22/33) [76%] | −9 [+0] | 3,731 [13,150] | 94% [2%] | 0 [7,848] | 9,585 [166] | 12,305 |
| oracle | 67% (22/33) [67%] | −9 [−9] | 3,731 [3,731] | 94% [94%] | 0 [0] | 9,585 [9,585] | 12,305 |

The branch equals the oracle at every checkpoint on every field the eval records —
each probe's ranks, scores and hits, and every snapshot count. Involuntary hit@10 is
76% (19/25) at day 30, 63% (19/30) at day 45 and 67% (22/33) at day 60 under both;
significant kept 30/30, recalled kept 5/5, no memory eligible and left behind.

By target at day 60 (no_decay / branch / oracle): significant 85% / 90% / 90%,
recalled 80% for all three, one-off details 50% / 0% / 0%.

### Deliberate recall of targets that left hot

| policy | day | left hot | `memory_recall_memories` | `EpisodicMemory.recall` | cold store alone | mean cold score |
|---|---|---|---|---|---|---|
| branch | 30 | 3 | 1/3 [—] | 1/3 [—] | 1/3 [—] | 0.50 |
| branch | 45 | 6 | 4/6 [—] | 4/6 [—] | 4/6 [—] | 0.56 |
| branch | 60 | 8 | 5/8 [—] | 5/8 [—] | 5/8 [—] | 0.59 |
| oracle | 30 | 3 | 1/3 [0/3] | 1/3 [0/3] | 1/3 [0/3] | 0.50 |
| oracle | 45 | 6 | 4/6 [0/6] | 4/6 [0/6] | 4/6 [3/6] | 0.56 |
| oracle | 60 | 8 | 5/8 [0/8] | 5/8 [0/8] | 5/8 [4/8] | 0.59 [0.13] |

EXP-006's branch moved none of the targets. Cold scores are cosine now: the five
found score 0.497–0.716. Under EXP-006's L2 tables four of them scored 0.04–0.25, and
the fifth — the kitchen soundtrack, at cosine 0.497 — fell to −0.003 and the cold
store's own 0.0 floor dropped it, which is why the cold store alone found four then
and five now.

No decay's tool finds the same five of these eight details while they are still hot:
nature documentary and damp basement are missed by every policy, and the heating
detail is the tie described below.

### Deliberate recall, all targets

| policy | `memory_recall_memories` hit@10, day 60 | of them still hot | EXP-006 |
|---|---|---|---|
| no_decay | 28/33 | 28/33 | 6/33 |
| branch | 28/33 | 23/25 | 4/33 |
| oracle | 28/33 | 23/25 | 3/33 |

Deliberate recall finds the same 28 of 33 targets with decay as without it.

Of the targets an exact ranking puts in the hot top 10, the real queries now miss one
each at day 60 (EXP-006: 73–90% for the tool):

- **no_decay, "Did someone turn the heating up really high?"** The target's semantic
  key, "Observed climate.thermostat change from heat to heat", is shared by 236 hot
  entries, the target among them, all scoring 0.511. The exact rank counts ties in
  the target's favour, so it ranks first; the search returns ten of the 235 others.
  This is a tie, not a search failure.
- **branch and oracle, "Is there a rule about when the robot vacuum can run?"** The
  target ranks 10th in hot; the tool merges hot and cold results and keeps ten, and a
  cold result outranks it.

### Selection and cost

| policy | candidates/pass (mean, max) | eligible seen/pass | pass ms p50 / p95 / p99 / max | embeds/pass | embeds/migrated |
|---|---|---|---|---|---|
| no_decay | 0 | 0 | 0 / 0 / 0 / 0 | 0 | — |
| branch | 3,188, 3,987 [18.8, 19] | 6.7 [0.1] | 418 / 888 / 1,273 / 1,689 [48 / 123 / 184 / 378] | 3.8 | 0.57 [10.8] |
| oracle | 3,225, 4,018 | 6.7 | 327 / 739 / 970 / 1,279 [711 / 2,189 / 2,817 / 3,840] | 3.8 | 0.57 [2.57] |

The embeds left are the compression summaries' (one content and one key vector per
group); migrating an entry no longer embeds anything.

### Backlog pass

Run `2026-10-04T033419` (scratch; not in the CSV). A policy subclassing `BranchDecay`
skipped every pass for 59 days, then ran the branch's pass hourly — what deploying
this PR onto a hot store that never decayed does on its first pass.

| | candidates read | migrated | embeds | wall time |
|---|---|---|---|---|
| first pass (day 60, hour 1) | 13,059 | 9,357 | 2,028 | 150 s |
| next 23 passes | ~3,700 each | 8–9 each | 4–6 each | 247–1,040 ms |

The first pass costs about 16 ms per migrated memory on this CPU; the following ones
are back to normal at once. A whole day's entries for an entity move together, so
compression groups are larger and the archive ends at 10,667 entries instead of
12,305. By the day-60 checkpoint hot held the same 3,731 entries as the incremental
run, and recall matched it: involuntary 22/33, the tool 28/33, departed targets 5/8 by
the tool, by `EpisodicMemory.recall` and by the cold store alone.

## Analysis

**H1 is supported.** The branch moves the same 9,585 memories as the oracle and ends
every checkpoint with the same hot store; with grouping order fixed, the cold stores
match too (12,305 entries each). No eligible memory was left in hot at any checkpoint
(stuck ≥1d: 0). Cost per migrated memory fell from 2.57 to 0.57 embeds, and an
hourly pass takes 418 ms median and 1.7 s at worst, against the EXP-006 oracle's
711 ms and 3.8 s. The metadata range is a superset: a pass reads ~3,200 low-significance entries to find
6.7 eligible ones, because most are simply too young. Pressure is at most `age / 30`,
so an age bound (`timestamp < now − 30·threshold days`, six days at 0.2) would skip
the ~1,300 entries under six days old (about 220 arrive a day); at under a second per
hourly pass it is not worth the extra range today.

**H2 is supported.** At every checkpoint both deliberate paths find every departed
target the cold store alone finds (5/8 at day 60, against 0/8 in EXP-006), and they
find the same details no decay's tool finds while those details are still hot, so
deliberate recall loses nothing to decay: 28/33 under every policy. The three never
found are hard probes in any store: two are missed by every policy, hot or cold, and
the third — the heating detail — is found even by no decay only because the exact
rank counts a 236-way tie in its favour.

**H3 is supported, with two residuals that are not search errors.** Exact hot search
took the tool from 3–6 to 28 of 33 targets and made runs reproducible. What
remains is a 236-way tie on a shared semantic key, which no top-10 search can
resolve, and the merge: a hot target at rank 10 can lose its place to a cold result.
That is the merge working as designed — one ranked list across both stores — and the
cost of making cold reachable.

**H4 is rejected, as in EXP-006, and for the same reason.** Involuntary recall reads
hot only, and the formula retires the eight one-off details (significance
0.105–0.355) at 17–28 days, taking four involuntary hits with them; clearing old
noise lifts one significant target (the robot-vacuum rule, unranked → 10th). This is
the formula's trade-off, not selection's — the branch and the oracle are identical —
and with H2 the retired details are still a deliberate recall away. Whether to keep
one-off details hot longer is a policy question for the owner: a lower
`decay_migration_threshold` or a significance boost for details users ask about
would change it, at the cost of a larger hot store.

### Problems from EXP-006, after the fixes

| # | Problem | EXP-006 | Now |
|---|---|---|---|
| 6 | Unfiltered HNSW KNN | 3–6/33 found, not reproducible | 28/33 under every policy; misses are a tie and the merge |
| 4 + 7 | Cold unreachable, L2 scores | 0/8 by both paths | 5/8 by both = everything cold finds |
| 1 | `FT.SEARCH` without `LIMIT` | ≤19 candidates, 166 migrated | every candidate, 9,585 migrated |
| 3 | Selection by similarity to a placeholder | 0.1 eligible seen/pass | 6.7 = the oracle |
| 5 | Re-embedding on migration | 2.57 embeds/migrated | 0.57 (summaries only) |
| 2 | `min_similarity=0.0` drops negative cosine | not observed | impossible: selection has no similarity |

### Threats to validity
- EXP-006's threats stand: one house, one seed, 60 days; experimenter-written probes;
  concatenation summaries instead of an LLM's; CPU timings.
- The tool's merge was measured with the eval's archive (a fresh cold store per
  policy). A production archive holding months of compressed summaries competes
  harder for the ten slots.
- The backlog pass was measured at 60 days. The deployment's backlog is everything
  passive observation has written since it shipped, so its first pass will take longer
  — at ~16 ms per memory on this CPU, about 4 minutes per 15,000 — and that
  Librarian cycle with it. Embedding speed on the deployment's hardware changes the
  figure; the 0.22 summary embeds per migrated memory do not.
- Exact hot KNN scores every entry the filter admits, so its cost grows linearly
  with the hot store: about 7 ms for both fields over 13,000 entries in the EXP-006
  spot check. Decay holds hot near 3,700 here; a much larger hot store would want
  the HNSW index back, with an `EF_RUNTIME` high enough to escape the duplicates.
