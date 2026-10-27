"""Markdown tables for a memory-decay run (``python -m evals memory show``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from evals.memory.dataset import Category
from evals.memory.models import Ratio

if TYPE_CHECKING:
    from evals.memory.models import Checkpoint, MemoryEvalRun, PolicyResult, ProbeOutcome

BASELINE = "no_decay"


@dataclass(frozen=True)
class CheckpointSummary:
    policy: str
    day: int
    involuntary: Ratio
    involuntary_by_category: dict[Category, Ratio]
    exact: Ratio
    open: Ratio
    # Targets an exact ranking puts in the top 10 that the real search did not return.
    knn_miss_involuntary: Ratio
    knn_miss_tool: Ratio
    mean_noise: float
    migrated_targets: int
    tool_reach: Ratio
    recall_reach: Ratio
    cold_reach: Ratio
    kept_significant: Ratio
    kept_recalled: Ratio
    hot_episodic: int
    noise_gone: Ratio
    stuck_1d: int
    stuck_7d: int
    eligible_unselected: Ratio


def _ratio(outcomes: list[ProbeOutcome], hit: str) -> Ratio:
    return Ratio(hits=sum(1 for o in outcomes if getattr(o, hit)), n=len(outcomes))


def summarize(policy: PolicyResult, point: Checkpoint) -> CheckpointSummary:
    probes = point.probes
    migrated = [o for o in probes if o.location != "hot"]
    in_top10 = [o for o in probes if o.exact_rank is not None and o.exact_rank <= 10]
    return CheckpointSummary(
        policy=policy.name,
        day=point.day,
        involuntary=_ratio(probes, "involuntary_hit"),
        involuntary_by_category={
            category: _ratio([o for o in probes if o.category == category], "involuntary_hit")
            for category in (Category.SIGNIFICANT, Category.RECALLED, Category.DETAIL)
        },
        exact=_ratio(probes, "exact_hit"),
        open=_ratio(probes, "open_hit"),
        knn_miss_involuntary=Ratio(
            hits=sum(1 for o in probes if o.exact_hit and not o.involuntary_hit),
            n=sum(1 for o in probes if o.exact_hit),
        ),
        knn_miss_tool=Ratio(
            hits=sum(1 for o in in_top10 if not o.tool_hit),
            n=len(in_top10),
        ),
        mean_noise=sum(o.involuntary_noise for o in probes) / len(probes) if probes else 0.0,
        migrated_targets=len(migrated),
        tool_reach=_ratio(migrated, "tool_hit"),
        recall_reach=_ratio(migrated, "recall_hit"),
        cold_reach=_ratio(migrated, "cold_hit"),
        kept_significant=point.kept_significant,
        kept_recalled=point.kept_recalled,
        hot_episodic=point.snapshot.hot_episodic,
        noise_gone=Ratio(
            hits=point.snapshot.noise_older_14d_gone, n=point.snapshot.noise_older_14d
        ),
        stuck_1d=point.snapshot.stuck_1d,
        stuck_7d=point.snapshot.stuck_7d,
        eligible_unselected=point.eligible_unselected,
    )


def _pct(ratio: Ratio, *, counts: bool = True) -> str:
    if ratio.rate is None:
        return "n/a"
    text = f"{ratio.rate:.0%}"
    return f"{text} ({ratio.hits}/{ratio.n})" if counts else text


def _delta(value: Ratio, baseline: Ratio | None) -> str:
    if baseline is None or value.rate is None or baseline.rate is None:
        return ""
    points = (value.rate - baseline.rate) * 100
    return f"{points:+.0f} pts"


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def format_run(run: MemoryEvalRun) -> str:
    s = run.settings
    lines = [
        f"# Memory decay eval — {run.run_id}",
        "",
        f"- {s.days} simulated days, seed {s.seed}, one decay pass every "
        f"{s.pass_every_hours}h, threshold {s.threshold}",
        f"- embeddings: {s.embedding_model} ({s.embedding_backend}, dim {s.embedding_dim}); "
        f"Redis {s.redis_version or '?'} ({s.redis_image})",
        f"- involuntary recall: limit {s.involuntary_recall_limit}, "
        f"min similarity {s.involuntary_recall_threshold}",
        f"- memories: {', '.join(f'{k} {v}' for k, v in run.dataset_counts.items())}; "
        f"{run.probe_count} probes",
        "",
    ]
    summaries = {(p.name, c.day): summarize(p, c) for p in run.policies for c in p.checkpoints}

    lines += [
        "## Headline",
        "",
        "| policy | day | involuntary hit@10 | vs no_decay | exact-search hit@10 | "
        "hit@10 at floor 0 | noise in top-10 | kept significant | kept recalled | "
        "hot episodic | routine >14d gone | stuck ≥1d | stuck ≥7d |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (name, day), row in summaries.items():
        baseline = summaries.get((BASELINE, day))
        lines.append(
            f"| {name} | {day} | {_pct(row.involuntary)} | "
            f"{_delta(row.involuntary, baseline.involuntary if baseline else None)} | "
            f"{_pct(row.exact)} | "
            f"{_pct(row.open)} | {row.mean_noise:.1f} | {_pct(row.kept_significant)} | "
            f"{_pct(row.kept_recalled)} | {row.hot_episodic} | {_pct(row.noise_gone)} | "
            f"{row.stuck_1d} | {row.stuck_7d} |"
        )

    lines += [
        "",
        "## Involuntary hit@10 by target",
        "",
        "| policy | day | significant | recalled | detail (one-off, low significance) |",
        "|---|---|---|---|---|",
    ]
    for (name, day), row in summaries.items():
        cats = row.involuntary_by_category
        lines.append(
            f"| {name} | {day} | {_pct(cats[Category.SIGNIFICANT])} | "
            f"{_pct(cats[Category.RECALLED])} | {_pct(cats[Category.DETAIL])} |"
        )

    lines += [
        "",
        "## Deliberate recall of targets that left hot",
        "",
        "The cold store searched alone says whether the archive holds the answer; "
        "`EpisodicMemory.recall` says whether it survives the merge with hot results.",
        "",
        "| policy | day | targets no longer hot | memory_recall_memories | "
        "EpisodicMemory.recall (hot+cold) | cold store alone | target's cold score (mean) |",
        "|---|---|---|---|---|---|---|",
    ]
    for policy in run.policies:
        for point in policy.checkpoints:
            row = summaries[(policy.name, point.day)]
            cold_scores = [o.cold_score for o in point.probes if o.cold_score is not None]
            mean_cold = f"{sum(cold_scores) / len(cold_scores):.2f}" if cold_scores else "n/a"
            lines.append(
                f"| {policy.name} | {point.day} | {row.migrated_targets} | "
                f"{_pct(row.tool_reach)} | {_pct(row.recall_reach)} | "
                f"{_pct(row.cold_reach)} | {mean_cold} |"
            )

    lines += [
        "",
        "## Search misses",
        "",
        "Targets an exact brute-force KNN over the hot store ranks in the top 10 (and, for "
        "involuntary, above the floor) that the real RediSearch query did not return. Hot "
        "search is exact, so a miss here is a tie — the target's best vector shared with "
        "other entries, which the exact rank does not count against it — or, for the tool, "
        "cold results outranking a hot target in the merge.",
        "",
        "| policy | day | involuntary (filtered KNN) | memory_recall_memories (unfiltered) | "
        "distinct contents / semantic keys in hot |",
        "|---|---|---|---|---|",
    ]
    for policy in run.policies:
        for point in policy.checkpoints:
            row = summaries[(policy.name, point.day)]
            lines.append(
                f"| {policy.name} | {point.day} | {_pct(row.knn_miss_involuntary)} | "
                f"{_pct(row.knn_miss_tool)} | {point.snapshot.hot_distinct_content} / "
                f"{point.snapshot.hot_distinct_semantic_keys} of "
                f"{point.snapshot.hot_episodic} |"
            )

    lines += [
        "",
        "## Selection and cost per pass",
        "",
        "| policy | passes | migrated | candidates/pass (mean, max) | eligible seen/pass | "
        "unmovable slots | eligible but unselected (final) | pass ms (mean, p95) | "
        "embeds/pass | embeds/migrated | embed ms/pass (summed) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for policy in run.policies:
        lines.append(_cost_row(policy))
    lines += [
        "",
        "A pass embeds concurrently, so summed embed time can exceed the pass's wall time.",
    ]

    final_day = max((c.day for p in run.policies for c in p.checkpoints), default=None)
    if final_day is not None:
        lines += _probe_table(run, final_day)
    return "\n".join(lines) + "\n"


def _cost_row(policy: PolicyResult) -> str:
    passes = policy.passes
    n = len(passes) or 1
    migrated = sum(p.migrated for p in passes)
    candidates = [p.candidates for p in passes]
    slots = sum(candidates)
    unmovable = slots - sum(p.candidates_eligible for p in passes)
    walls = [p.wall_ms for p in passes]
    final = policy.checkpoints[-1].eligible_unselected if policy.checkpoints else Ratio(hits=0, n=0)
    unmovable_share = f"{unmovable / slots:.0%}" if slots else "n/a"
    per_migrated = f"{policy.decay_embed_calls / migrated:.2f}" if migrated else "n/a"
    return (
        f"| {policy.name} | {len(passes)} | {migrated} | "
        f"{slots / n:.1f}, {max(candidates, default=0)} | "
        f"{sum(p.candidates_eligible for p in passes) / n:.1f} | "
        f"{unmovable_share} | {_pct(final)} | "
        f"{sum(walls) / n:.0f}, {_percentile(walls, 0.95):.0f} | "
        f"{policy.decay_embed_calls / n:.1f} | {per_migrated} | "
        f"{policy.decay_embed_seconds * 1000 / n:.0f} |"
    )


def _probe_table(run: MemoryEvalRun, day: int) -> list[str]:
    names = [p.name for p in run.policies]
    by_policy = {
        p.name: {o.target_id: o for c in p.checkpoints if c.day == day for o in c.probes}
        for p in run.policies
    }
    first = next(iter(by_policy.values()), {})
    lines = [
        "",
        f"## Every probe on day {day}",
        "",
        "I = involuntary hit (rank), E = exact-search hit (rank), T = memory_recall_memories, "
        "R = EpisodicMemory.recall, C = cold store alone; where = hot/cold/missing.",
        "",
        "| question | target | " + " | ".join(names) + " |",
        "|---|---|" + "---|" * len(names),
    ]
    for target_id, outcome in first.items():
        cells = []
        for name in names:
            o = by_policy[name].get(target_id)
            if o is None:
                cells.append("")
                continue
            marks = [
                f"I{o.involuntary_rank}" if o.involuntary_hit else "",
                f"E{o.exact_rank}" if o.exact_hit else "",
                "T" if o.tool_hit else "",
                "R" if o.recall_hit else "",
                "C" if o.cold_hit else "",
            ]
            cells.append(f"{' '.join(m for m in marks if m) or '—'} ({o.location})")
        lines.append(f"| {outcome.question} | {outcome.category} | " + " | ".join(cells) + " |")
    return lines
