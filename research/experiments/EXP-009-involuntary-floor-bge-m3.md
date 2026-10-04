---
id: EXP-009
title: An Involuntary-Recall Floor for bge-m3, and the Production Switch
status: complete
start_date: 2026-10-04
end_date: 2026-10-04
---

# EXP-009: An Involuntary-Recall Floor for bge-m3

[EXP-008](EXP-008-memory-decay-bge-m3.md) found that `BAAI/bge-m3` recalls more probe
targets than `google/embeddinggemma-300m` but lets more noise through the 0.5 floor,
because it scores unrelated text higher. Before production switched to bge-m3, this
experiment picked a floor for it. It also records the switch.

## Hypothesis

**H1.** There is a bge-m3 floor at which involuntary recall finds at least as many
targets as EmbeddingGemma does at 0.5, both with and without decay, while no off-topic
question matches anything.

**H2.** At that floor, real turns receive about as many injected memories as they do
today.

**H3.** The generic phrasings in #200, which scored below 0.5 with EmbeddingGemma, clear
that floor with bge-m3.

## Method

1. **Labelled probes.** This is the memory eval from EXP-007/008 (seed 7, 60 days), run
   under the `no_decay` and `branch` policies for both models. A wrapper around
   `metrics.probe` and `runner.checkpoint` records extra data at every checkpoint (days
   30, 45 and 60):
   - for each probe, the top 10 of its involuntary query at floor −1, with each result's
     score and category;
   - the same for 20 general-knowledge questions that nothing in the house should match.

   Floors are then swept offline over these recordings, so one run covers every floor.
   Hit@10 and noise use the same denominators as EXP-008: all 33 probes, with noise
   counting the routine and reflex observations above the floor. Involuntary recall
   searches only hot, so a departed target cannot count at any floor.
2. **Real traffic.** The 147 most recent requests in production's request stream were
   scored against a copy of its 6,464 hot memories, embedded by both models. As in
   `search_text`, a memory's score is the higher of its content and semantic-key
   similarities. For each floor I counted how many memories each turn would receive.
   These turns are unlabelled, so this measures volume, not relevance.
3. **#200's six queries**, run against live production after the switch.

The rule for choosing: take the highest floor on a 0.025 grid at which the labelled hit
count is at least EmbeddingGemma's at 0.5 in **both** stores and no off-topic question
matches anything.

## Results

Rows are in `research/data/involuntary-floor.csv` (one row per model, policy, day and
floor) and `research/data/involuntary-floor-traffic.csv`.

### Labelled probes, day 60

| model @ floor | hit@10, no decay | hit@10, decayed | noise / probe (no decay · decayed) | injected / probe (no decay · decayed) | off-topic questions matched |
|---|---|---|---|---|---|
| EmbeddingGemma @ 0.5 (until today) | 25/33 | 22/33 | 1.82 · 1.85 | 3.12 · 3.06 | 5% |
| bge-m3 @ 0.5 | 30/33 | 25/33 | 2.91 · 3.06 | 6.21 · 6.18 | 20% |
| bge-m3 @ 0.55 | 28/33 | 23/33 | 2.27 · 2.36 | 4.45 · 4.36 | 0% |
| **bge-m3 @ 0.575** | **27/33** | **22/33** | **2.09 · 2.15** | **3.85 · 3.76** | **0%** |
| bge-m3 @ 0.6 | 24/33 | 19/33 | 1.73 · 1.79 | 3.12 · 3.03 | 0% |

The highest off-topic score was 0.548 for bge-m3 and 0.513 for EmbeddingGemma. At 0.525,
bge-m3 still matches 10% of the off-topic questions.

### Real traffic (147 turns, 6,464 memories)

| model @ floor | injected / turn | turns with any | of which observations · other |
|---|---|---|---|
| EmbeddingGemma @ 0.5 | 1.98 | 43% | 0.92 · 1.06 |
| bge-m3 @ 0.5 | 7.52 | 89% | 2.78 · 4.75 |
| bge-m3 @ 0.55 | 4.25 | 66% | 1.63 · 2.62 |
| **bge-m3 @ 0.575** | **2.91** | **57%** | **1.23 · 1.68** |
| bge-m3 @ 0.6 | 2.08 | 46% | 0.90 · 1.18 |

Top-1 scores over these turns (10th, 50th and 90th percentile) were 0.348, 0.470 and 0.666
for EmbeddingGemma, and 0.497, 0.588 and 0.741 for bge-m3. Across all query–memory
pairs the 99th percentile was 0.529 and 0.603. bge-m3 moves the whole distribution up
by roughly 0.07–0.12.

### #200's queries, live after the switch

All six now return relevant memories above 0.575:
- `did someone arrive home` ranks the `person.*` home transitions first, at 0.62–0.68.
  #200 measured a best of 0.370 with EmbeddingGemma, against a 228-entry index.
- `is anyone playing music` reaches the media players' `idle → playing` at 0.63.
- Questions that name an entity score 0.62–0.70.

A typical top 10 holds one observation several times over. These are the duplicate
passive observations that #200 and EXP-006 describe.

### Production switch

The switch was made on 2026-10-04 around 07:32 UTC:
1. `EMBEDDING_BACKEND=openai` pointing at the shared vLLM embedding server,
   `EMBEDDING_MODEL=BAAI/bge-m3` and `INVOLUNTARY_RECALL_THRESHOLD=0.575`.
2. The recreated container latched the width mismatch in every service, as designed, and
   no vector was written at the wrong width.
3. A one-off script dropped `idx:context` without `DD` and rewrote both vector fields of
   all 6,491 hot hashes from the configured provider. Each write was guarded so that a
   hash deleted mid-run is not recreated.
4. The cold store had no entries yet, so its `vec0` tables were rebuilt empty, following
   the guard's own recovery text.
5. After a restart: `idx:context` at dim 1024 with 6,491 documents and 0 indexing
   failures, the ingestor's PEL empty, and no warnings.

## Analysis

**H1 supported, with no margin to spare.** 0.575 is the highest floor that holds both
stores at or above today's hit count:
- Without decay it finds two more targets than today (27 vs 25).
- With decay it ties (22 vs 22).
- At 0.6 both stores drop three.

One probe is three points here, so the decayed-store tie rests on a single probe.

**H2 roughly supported.** Real turns get 2.91 memories against 1.98, about 1.5×. Keeping
0.5 would have meant 3.8× as many, and a fifth of off-topic questions would have pulled
memories in. Most of the extra volume is in "other", not observations. Those are mostly
the conversation-turn memories (`user='…' → N chars (actions=…)`): chit-chat matches them
because they *are* chit-chat. Raising the floor cannot fix that without losing targets.
At 0.65, where they mostly drop out, the labelled hit count falls to 21/33. Whether those
memories should be written, or written in that form, is a question about memory content,
not the floor.

**H3 supported.** Generic phrasing now finds the right memory, which was #200's first
complaint. The duplicates are what fill its top 10 now.

**The floor is a property of the model, not of the memory system.** bge-m3's scores sit
roughly 0.07–0.12 above EmbeddingGemma's across the whole distribution, so a floor tuned
for one model is wrong for the other. Any change of embedding model needs this
calibration again. The config default of 0.5 is unmeasured
for the default model (`all-MiniLM-L6-v2`).

### Threats to validity

- **Small labelled set.** There are 33 synthetic probes and 20 hand-written off-topic
  questions. The margin between the floor and the worst off-topic score is 0.027.
- **No relevance labels for real traffic.** The 147 turns come from one household, so
  that table measures how much is injected, not whether it is right.
- **Approximate production search.** Production's hot KNN is still the HNSW walk; the
  exact `ADHOC_BF` search is on this branch. EXP-006 showed that duplicates can trap the
  walk, so production's top 10 can differ from the exact ranking used here until #247
  merges.
- **A moving distribution.** The score distribution shifts as the store grows and as
  decay changes what is in it.
