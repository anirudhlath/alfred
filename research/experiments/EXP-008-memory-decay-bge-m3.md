---
id: EXP-008
title: Memory Decay With bge-m3 on the GPU — and a Backlog Pass That Exhausted the Redis Pool
status: complete
start_date: 2026-10-04
end_date: 2026-10-04
---

# EXP-008: Memory Decay With bge-m3 on the GPU

[EXP-007](EXP-007-memory-decay-fixed.md) ran the fixed decay with the deployment's
model, `google/embeddinggemma-300m`, in-process on CPU. This run swaps in
`BAAI/bge-m3` (1024-dim) served by vLLM on the GPU, through the existing
`EMBEDDING_BACKEND=openai` seam — no model loaded into the eval process.

## Hypothesis

**H1.** bge-m3 recalls the probes' targets at least as well as EmbeddingGemma.

**H2.** Decay's trade-off holds under bge-m3: deliberate recall loses nothing, and
involuntary recall loses only the one-off details the formula retires.

**H3.** Embedding on the GPU makes the eval and the decay pass several times faster.

## Method

Identical to EXP-007 (seed 7, 60 days, Redis 8.2.10 per policy, probes at days 30,
45 and 60, threshold 0.2, involuntary floor 0.5), with
`EMBEDDING_BACKEND=openai EMBEDDING_HOST=<vLLM> EMBEDDING_MODEL=BAAI/bge-m3`. The
server is the box's shared vLLM embedding instance (`--runner pooling`, RTX 4090),
one input per request. The backlog pass was re-measured the same way as in EXP-007.

## Results

Run `2026-10-04T062412_memory-BAAI--bge-m3` on the branch head with the fix below; rows in
`research/data/memory-decay.csv`. An earlier run before the fix,
`2026-10-04T060430`, matched it on every recall number.

### Recall at day 60 (EmbeddingGemma from EXP-007 in brackets)

| policy | involuntary hit@10 | vs no_decay | noise in top-10 | significant | recalled | details | `memory_recall_memories` |
|---|---|---|---|---|---|---|---|
| no_decay | 91% (30/33) [76%] | — | 2.9 [1.8] | 20/20 [17/20] | 5/5 [4/5] | 5/8 [4/8] | 30/33 [28/33] |
| branch | 76% (25/33) [67%] | −15 [−9] | 3.1 [1.8] | 20/20 [18/20] | 5/5 [4/5] | 0/8 [0/8] | 30/33 [28/33] |
| oracle | 76% (25/33) [67%] | −15 [−9] | 3.1 [1.8] | 20/20 [18/20] | 5/5 [4/5] | 0/8 [0/8] | 30/33 [28/33] |

The branch again equals the oracle on every checkpoint, and the stores are the same
as EXP-007's (3,731 hot, 12,305 cold) — selection reads metadata, not vectors.

- **No target sits under the floor.** Hit@10 at floor 0 equals hit@10 at 0.5 for
  every policy and day (EmbeddingGemma: three targets just under it).
- **No search misses.** The exact ranking and the real queries agree on every target.
  EmbeddingGemma's two residuals are gone: the heating detail no longer ties with 235
  thermostat observations (bge-m3 misses it outright, under every policy), and the
  robot-vacuum rule ranks 2nd in hot instead of 10th, so no cold result displaces it.
- **More noise passes the floor**: 2.9–3.4 non-targets in the top 10 against 1.8–2.0.
  bge-m3 scores unrelated text higher, so the same absolute 0.5 admits more.

### Departed targets

| policy | day | left hot | tool | `EpisodicMemory.recall` | cold alone | mean cold score |
|---|---|---|---|---|---|---|
| branch / oracle | 30 | 3 | 0/3 [1/3] | 0/3 [1/3] | 0/3 [1/3] | — |
| branch / oracle | 45 | 6 | 3/6 [4/6] | 3/6 [4/6] | 3/6 [4/6] | 0.66 |
| branch / oracle | 60 | 8 | 5/8 [5/8] | 5/8 [5/8] | 5/8 [5/8] | 0.67 |

Both recall paths find everything the cold store finds. At day 60 they find the same
five details no decay's tool finds while hot (Christmas lights, washing machine, damp
basement, back-door lock, robot vacuum); the kitchen soundtrack, found by
EmbeddingGemma, is missed by bge-m3 hot or cold, and the damp basement the other way
round.

### Speed

| | EmbeddingGemma, CPU (EXP-007) | bge-m3, GPU |
|---|---|---|
| wall time per policy (no_decay / branch / oracle) | 175 / 653 / 519 s | 57 / 386 / 276 s |
| branch pass ms p50 / p95 / p99 / max | 418 / 888 / 1,273 / 1,689 | 269 / 341 / 370 / 422 |
| oracle pass ms p50 / p95 / p99 / max | 327 / 739 / 970 / 1,279 | 180 / 256 / 284 / 319 |
| backlog pass (9,357 memories, with the fix below) | 145 s | 16.6 s |

### The backlog pass exhausted the Redis pool

The first bge-m3 backlog run moved only 1,890 of 9,357 eligible memories on its first
pass, and most of the rest over the next four (up to 6.8 s each). A re-run with the
core log captured logged 14,546 `Too many connections` warnings. Before the fix,
`_apply_decay` started every migration at once with
`asyncio.gather`; each holds a hot-store Redis connection while it moves, and redis-py
8 caps a client's pool at 100 and raises past that instead of waiting. Each group
embeds its summary before it touches Redis. On CPU those embeds queue for a small
thread pool (`asyncio.to_thread`) at tens of milliseconds each, which spaced the
groups out — why EXP-007's CPU backlog never hit the cap. vLLM batches concurrent
requests and answers them all within milliseconds. Production builds its client the same way
(`aioredis.from_url`), and the pool is shared by the whole service, so such a pass
would also have failed unrelated Redis calls while it ran.

A second defect rode on the first: compression wrote a group's summary before moving
its originals, so an original that failed stayed hot, was regrouped next pass and
summarised again. The archive held 12,237 entries instead of 10,667.

Fixed on the branch (`fix(librarian): cap concurrent decay migrations and summarise
only what moved`): a pass runs at most `DECAY_MIGRATION_CONCURRENCY` (8) migrations
at once, and compression moves the originals first and summarises only those that
moved. Re-measured: the first pass moves all 9,357 in 16.6 s with no warnings, the
archive holds 10,667 entries, and the next 23 passes take 240–339 ms.

## Analysis

**H1 is supported.** bge-m3 finds every significant and recalled target
involuntarily (25/25 against 21–22), 30 of 33 deliberately against 28, and has no
target under the floor and no search residuals. Its cost is noise: about one more
non-target in every top 10 at the same 0.5 floor. A bge-m3 deployment would want its
own floor; this run does not tune one.

**H2 is supported for deliberate recall and holds for involuntary recall, with a
larger loss.** Deliberate recall is 30/33 under every policy. Involuntary recall
loses 15 points instead of 9 because bge-m3 finds five of the eight one-off details
while they are hot (EmbeddingGemma: four), and decay retires all eight. The trade-off
is the same one EXP-007 describes, with more at stake when the model is better at
details.

**H3 is partly supported.** The write path, which is all embedding, runs 3.1× faster
(no_decay: 57 s against 175 s); the eval as a whole 1.9× (12 minutes against 22). A
decay pass gains less — median 269 ms against 418 — because most of it is reading
~3,200 candidates from Redis, which the model does not touch. The backlog pass, which
embeds ~2,000 summaries, is where the GPU pays: 16.6 s against 145 s.

**The speed exposed a real bug.** Fast embeddings removed the accidental throttle that
CPU embedding provided, and the backlog pass then tried to hold thousands of Redis
connections. The fix caps the pass, not the model, so it holds for either backend.

### What switching production would take

Not done here. bge-m3 vectors are 1024-dim against EmbeddingGemma's 768, so the hot
index and the cold `vec0` tables would need rebuilding and every stored memory
re-embedding; dropping the RediSearch index with `DD` deletes the hot memories with
it. Involuntary recall would also embed over HTTP inline in the reply path
(~15 ms a request here) and depend on the shared vLLM instance being up.

### Threats to validity
- EXP-006/007's threats stand: one house, one seed, 60 days; experimenter-written
  probes; concatenation summaries.
- The probes were written against EmbeddingGemma's behaviour; a model-neutral probe
  set might move both numbers.
- The 0.5 floor was not re-tuned for bge-m3; a higher floor trades noise for hits.
- GPU timings are on a vLLM instance shared with other development traffic.
