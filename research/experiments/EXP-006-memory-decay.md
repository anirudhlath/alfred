---
id: EXP-006
title: Memory Decay — Does Moving Memories to Cold Help Alfred Recall What Matters?
status: complete
start_date: 2026-10-03
end_date: 2026-10-03
---

# EXP-006: Memory Decay

## Hypothesis

**H1.** With contextual decay running (PR #247's threshold 0.2), routine state changes
leave the hot store within days of becoming eligible, so involuntary recall — the 10
memories the Conscious Engine pulls into every prompt — returns the significant and
frequently recalled memories more often than with no decay (master).

**H2.** Whatever leaves hot stays reachable by deliberate recall, as the design says
(phase-3 spec §6.2, §7.4: archived memories remain recallable).

**H3.** The decay formula itself (§8.3) keeps what it should keep — alerts for weeks,
frequently recalled memories indefinitely — and forgets routine noise within one or
two cycles of eligibility.

**H0.** Decay as implemented on the branch changes nothing measurable about recall.

## Method

`python -m evals memory run` (see `docs/evals-memory.md`).

1. **House.** A seeded 60-day timeline (seed 7) of a simulated house: ~12,800 passive
   state changes, ~430 Reflex actions, 30 significant events (alerts, things sir said,
   appointments), 5 memories sir asks about every 2–7 days, 8 one-off details. Plus
   13 semantic sections and 6 routines in the same index.
2. **Real stack.** Each policy gets a throwaway Redis 8.2 container (RediSearch
   `idx:context`) and a temp sqlite-vec cold store. Memories go in through the real
   write paths (`ingest_observation`; `SignificanceScorer` + `EpisodicMemory.write`),
   embedded by the production model (`google/embeddinggemma-300m`, 768-dim, CPU).
3. **Clock.** Every simulated hour: write that hour's memories, apply that hour's
   retrievals (stats stamped at simulated time, only if still hot), run one decay
   pass with `now` = simulated time through the `_apply_decay(now=…)` seam.
4. **Policies.** `no_decay` (threshold 1.0, master); `branch` (threshold 0.2,
   candidates from the real search for `"general context memory event"`); `oracle`
   (threshold 0.2, candidates = every hot hash by SCAN — perfect selection, same
   formula).
5. **Probes.** 33 questions with known targets (20 significant, 5 recalled, 8
   detail), asked at days 30, 45 and 60 through involuntary recall (limit 10, floor
   0.5), `memory_recall_memories`, `EpisodicMemory.recall` (hot + cold), and an exact
   brute-force ranking of the hot store's own vectors.
6. **Daily.** SCAN the hot store; compute exact pressure for every episodic memory,
   eligibility streaks (stuck), sizes, and the share of noise older than 14 days that
   has left.

### Variables
- **Independent:** decay policy (candidate source × threshold)
- **Dependent:** involuntary hit@10, exact hit@10, noise in top-10, deliberate
  reachability, kept significant/recalled, hot size, noise forgotten, stuck,
  unpickable, HNSW misses, pass wall time and embed calls
- **Controlled:** dataset (seed 7, 60 days), embedding model, Redis image, write
  path, probes, involuntary limit and floor, one pass per simulated hour

## Results

Run `2026-10-04T012806_memory-google--embeddinggemma-300m` (rows in
`research/data/memory-decay.csv`). An earlier run with the same seed,
`2026-10-04T005455`, matched it on every number except the unfiltered-KNN ones
(see Determinism in `docs/evals-memory.md`). Redis 8.2.10, CPU embeddings,
1,440 passes per policy.

### Headline

| policy | day | involuntary hit@10 | vs no_decay | exact hit@10 | noise in top-10 | kept significant | kept recalled | hot episodic | routine >14d gone | stuck ≥7d |
|---|---|---|---|---|---|---|---|---|---|---|
| no_decay | 30 | 76% (19/25) | — | 80% | 2.0 | 17/17 | 5/5 | 6,656 | 0% | 1,357 |
| no_decay | 60 | 76% (25/33) | — | 79% | 1.8 | 30/30 | 5/5 | 13,316 | 0% | 7,988 |
| branch | 30 | 76% (19/25) | +0 | 80% | 2.0 | 17/17 | 5/5 | 6,605 | 1% | 1,336 |
| branch | 60 | 76% (25/33) | +0 | 79% | 1.8 | 30/30 | 5/5 | 13,150 | 2% | 7,848 |
| oracle | 30 | 76% (19/25) | +0 | 76% | 2.0 | 17/17 | 5/5 | 3,693 | 83% | 0 |
| oracle | 45 | 63% (19/30) | −10 | 63% | 2.0 | 24/24 | 5/5 | 3,725 | 91% | 0 |
| oracle | 60 | 67% (22/33) | −9 | 67% | 1.8 | 30/30 | 5/5 | 3,731 | 94% | 0 |

By target at day 60: significant 85% / 85% / 90% (no_decay / branch / oracle),
recalled 80% for all three, one-off details 50% / 50% / **0%**.

### Deliberate recall of targets that left hot (oracle only; nothing else left)

| day | left hot | `memory_recall_memories` | `EpisodicMemory.recall` (hot+cold) | cold store alone |
|---|---|---|---|---|
| 30 | 3 | 0/3 | 0/3 | 0/3 |
| 45 | 6 | 0/6 | 0/6 | 3/6 |
| 60 | 8 | 0/8 | 0/8 | 4/8 |

The four the cold store finds score 0.04–0.25 there: that store reports
`1 − L2 distance`, which for unit vectors is `1 − √(2 − 2·cos)` — cosine 0.54–0.72.

### Deliberate recall of targets still hot

`memory_recall_memories` found 6, 4 and 3 of the 33 targets at day 60 (no_decay,
branch, oracle), against 26, 26 and 22 found by an exact search with the floor
applied. Of the targets an exact ranking puts in the hot top 10, the tool's
unfiltered `*=>[KNN]` query missed 73–90% at every checkpoint, for every policy;
the involuntary query (filtered) missed 0–5%. Hot held 1,370 distinct contents
among 13,316 entries (no_decay) and 519 among 3,731 (oracle).

### Selection and cost

| policy | migrated (60 d) | candidates/pass | eligible seen/pass | unmovable slots | pass ms p50 / p95 / p99 / max | embeds/pass | embeds/migrated |
|---|---|---|---|---|---|---|---|
| no_decay | 0 | 18.8 (max 19) | 0.0 | 100% | 50 / 57 / 145 / 192 | 1.0 | — |
| branch | 166 | 18.8 (max 19) | 0.1 | 99% | 48 / 123 / 184 / 378 | 1.2 | 10.8 |
| oracle | 9,585 | 3,225 (max 4,018) | 6.7 | 100% | 711 / 2,189 / 2,817 / 3,840 | 17.1 | 2.57 |

No eligible memory, under either policy, had negative cosine to the decay query.

## Analysis

**H0 holds for the branch.** Its decay pass sees at most 19 candidates — `FT.SEARCH`
without `LIMIT` returns RediSearch's default 10 per vector field, not the 500 asked
for — and 99% of those slots go to semantic sections, routines and young memories
that are closest to the placeholder phrase. It moved 166 of ~9,600 eligible memories
in 60 days (1.7%); 7,848 sat eligible for a week or more. Every recall number equals
no decay's.

**H1 is rejected even with perfect selection.** The oracle — the same formula, every
hot memory considered — keeps hot at ~3,700 from day 20 on and clears 94% of routine
noise older than two weeks, yet involuntary hit@10 falls 9 points at day 60 and the
noise in the top 10 does not move (1.8–2.0 for all three). Of no decay's eight
misses, three are the top-ranked match sitting just under the 0.5 floor (0.47–0.50),
one is an approximate-search miss, and four rank 32nd to 300th. Clearing old noise
lifted two of those four (32nd → 10th, a hit; 123rd → 35th), so crowding by old noise
is real but small at 60 days. It is outweighed by what the formula retires: the
one-off details score 0.105–0.355 significance and leave hot at 17–28 days, as
designed, taking four involuntary hits with them (details 4/8 → 0/8).

**H3 is supported.** Under the oracle every significant (30/30) and recalled (5/5)
memory stayed hot — including the two observation-sourced recalled memories, whose
low significance leaves them relying on their retrievals (72 applied, none lost) —
and old routine noise left.

**H2 is rejected — nothing that leaves hot is reachable.** `memory_recall_memories`
searches hot only. `EpisodicMemory.recall` does search cold, and the cold store alone
finds half the departed targets, but the merge ranks the two stores' scores together
and cold's are on a different scale: its `vec0` tables are built with sqlite-vec's
default L2 metric, so `1 − distance` is not cosine. A cold match at cosine 0.72 scores
0.25 and loses to any hot result above that — 0 of 8 survived the merge. The repo's
own note that vec0 distance is cosine (CLAUDE.md gotcha) is wrong for these tables.

**A larger recall loss sits outside decay.** The tool's unfiltered KNN misses most
targets that are in hot right now (it found 3–6 of 33), because thousands of
identical routine vectors defeat HNSW's greedy search at the default `EF_RUNTIME`
of 10 (in a spot check, the same query with a filter, or with `EF_RUNTIME 200`,
found a target it had missed). Decay does not fix it: at 519 distinct contents among 3,731 entries the
oracle's hot store is still 7× duplicated. The same query is not reproducible run to
run.

**Cost is moderate.** At an hourly pass a perfect-selection decay costs 0.7 s median,
3.8 s worst, on CPU. Each migration re-embeds the content and key it already has
stored in hot (2.57 embeds per migrated memory, compression summaries included). The
eval decays from day 0; a deployment switching decay on mid-life faces one backlog
pass instead — about 9,600 memories at 60 days; at the oracle's ~0.13 s of pass
time per migrated memory, around 20 minutes on CPU.

### Problems, ranked by what they cost the user

| # | Problem | Matters? | Evidence |
|---|---|---|---|
| 6 | Unfiltered `*` KNN (tool, hot half of `recall`) | **Most** — deliberate recall misses targets in hot | 73–90% of exact top-10 missed; 3–6/33 found |
| 4 + 7 | Cold unreachable: tool is hot-only; `recall` merges L2-based cold scores with cosine hot scores | **Yes**, as soon as anything migrates | 0/8 by both paths; cold alone 4/8 |
| 1 | `FT.SEARCH` without `LIMIT` | **Yes** — branch decay is a no-op | ≤19 candidates, 166/9,600 migrated |
| 3 | Selection by similarity to a placeholder | **Yes** — compounds 1 | 99% of slots unmovable, 0.1 eligible seen/pass |
| 5 | Re-embedding on migration | Cost only | 2.57 embeds/migrated; ~20 min backlog pass |
| 2 | `min_similarity=0.0` drops negative cosine | **No**, with EmbeddingGemma | 0 of ~9,600 eligible |

### Threats to validity
- One house, one seed, 60 days. Crowding by old noise may appear over months; the
  hot store grows ~220/day without decay.
- Probes were written by the experimenter; similarity, not an LLM, decides hits.
- Compression summaries use the no-LLM fallback (concatenation). An LLM summary
  embeds differently, so cold reachability of compressed targets may differ.
- CPU embeddings: absolute pass times differ on the deployment's hardware; call
  counts do not.
