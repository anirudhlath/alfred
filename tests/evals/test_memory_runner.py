"""The replay loop's write and retrieval steps, the CLI's research rows, and (opt-in) the
whole simulation against a real throwaway Redis."""

from __future__ import annotations

import csv
import os
import shutil
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from core.memory.schemas import SignificanceScore
from evals.memory.cli import CSV_HEADER, append_research_rows
from evals.memory.dataset import Category, Retrieval, SimMemory, build_dataset
from evals.memory.models import (
    Checkpoint,
    DaySnapshot,
    MemoryEvalRun,
    PolicyResult,
    ProbeOutcome,
    Ratio,
    RunSettings,
)
from evals.memory.report import format_run
from evals.memory.runner import apply_retrieval, run_eval, write_memory
from evals.memory.sandbox import RedisSandbox
from shared.config import AlfredConfig
from tests.evals.memory_fakes import FakeRedis, HashEmbedder

if TYPE_CHECKING:
    from pathlib import Path

AT = datetime(2026, 1, 6, 19, 30, tzinfo=UTC)


async def test_entries_are_scored_then_written_like_the_librarian_writes_them() -> None:
    env: Any = SimpleNamespace(scorer=AsyncMock(), episodic=AsyncMock(), passive_scorer=AsyncMock())
    env.scorer.score = AsyncMock(return_value=SignificanceScore(overall=0.48))
    memory = SimMemory(
        id="sig-00",
        at=AT,
        category=Category.SIGNIFICANT,
        source="conversation",
        summary="Sir said the plumber is coming Thursday.",
        entities=["plumber"],
    )

    await write_memory(env, memory)

    entry, significance = env.episodic.write.await_args.args
    assert (entry.id, entry.timestamp, entry.entities) == ("sig-00", AT, ["plumber"])
    assert significance.overall == 0.48


async def test_observations_go_through_the_memory_ingestor() -> None:
    dataset = build_dataset(days=1)
    memory = next(m for m in dataset.memories if m.observation is not None)
    env: Any = SimpleNamespace(scorer=AsyncMock(), episodic=AsyncMock(), passive_scorer=AsyncMock())

    with patch("evals.memory.runner.ingest_observation", new=AsyncMock()) as ingest:
        await write_memory(env, memory)

    ingest.assert_awaited_once_with(
        memory.observation, env.episodic, env.scorer, env.passive_scorer
    )


async def test_a_retrieval_stamps_the_simulated_time_on_a_hot_memory() -> None:
    redis = FakeRedis({"ctx:rec-00": {"retrieval_count": 2}})
    env: Any = SimpleNamespace(redis=redis, hot=AsyncMock())
    env.hot.exists = AsyncMock(return_value=True)

    assert await apply_retrieval(env, Retrieval(at=AT, target_id="rec-00"))
    env.hot.update_metadata.assert_awaited_once_with(
        "rec-00", {"retrieval_count": 3, "last_retrieved": AT.timestamp()}
    )


async def test_a_retrieval_of_a_memory_that_left_hot_is_lost() -> None:
    env: Any = SimpleNamespace(redis=FakeRedis(), hot=AsyncMock())
    env.hot.exists = AsyncMock(return_value=False)

    assert not await apply_retrieval(env, Retrieval(at=AT, target_id="rec-00"))
    env.hot.update_metadata.assert_not_awaited()


def _settings() -> RunSettings:
    return RunSettings(
        seed=7,
        days=2,
        pass_every_hours=1,
        measure_days=[2],
        threshold=0.2,
        embedding_model="hash",
        embedding_backend="test",
        embedding_dim=16,
        involuntary_recall_limit=10,
        involuntary_recall_threshold=0.5,
        redis_image="redis:8-bookworm",
    )


def _run() -> MemoryEvalRun:
    outcome = ProbeOutcome(
        question="When is the plumber coming?",
        target_id="sig-00",
        category=Category.SIGNIFICANT,
        location="cold",
        involuntary_hit=False,
        involuntary_rank=None,
        involuntary_returned=3,
        involuntary_noise=2,
        exact_rank=None,
        exact_score=None,
        exact_hit=False,
        open_hit=False,
        open_rank=None,
        target_score=None,
        tool_hit=False,
        recall_hit=True,
        cold_hit=True,
        cold_score=0.16,
    )
    snapshot = DaySnapshot(
        day=2,
        hot_episodic=10,
        hot_by_category={"routine": 10},
        hot_distinct_content=4,
        hot_distinct_semantic_keys=3,
        cold_entries=5,
        eligible_in_hot=1,
        stuck_1d=1,
        stuck_7d=0,
        noise_older_14d=8,
        noise_older_14d_gone=6,
    )
    point = Checkpoint(
        day=2,
        probes=[outcome],
        kept_significant=Ratio(hits=0, n=1),
        kept_recalled=Ratio(hits=0, n=0),
        snapshot=snapshot,
        eligible_unpickable=Ratio(hits=0, n=1),
        stuck_unpickable=Ratio(hits=0, n=1),
        retrievals_applied=0,
        retrievals_lost=0,
    )
    policies = [
        PolicyResult(
            name=name,
            summary=name,
            threshold=0.2,
            wall_seconds=1.0,
            write_embed_calls=10,
            decay_embed_calls=4,
            decay_embed_seconds=0.01,
            suppressed_stat_writes=1,
            passes=[],
            days=[snapshot],
            checkpoints=[point],
        )
        for name in ("no_decay", "branch")
    ]
    return MemoryEvalRun(
        run_id="r1",
        timestamp=AT,
        settings=_settings(),
        dataset_counts={"routine": 10},
        probe_count=1,
        policies=policies,
    )


def test_research_rows_append_under_a_single_header(tmp_path: Path) -> None:
    path = tmp_path / "data" / "memory-decay.csv"
    append_research_rows(_run(), path)
    append_research_rows(_run(), path)

    rows = list(csv.reader(path.open()))
    assert rows[0] == list(CSV_HEADER)
    assert len(rows) == 1 + 2 * 2  # two runs x two policies x one checkpoint
    assert rows[1][CSV_HEADER.index("recall_reach_hits")] == "1"
    assert rows[1][CSV_HEADER.index("cold_reach_hits")] == "1"


def test_report_renders_every_section() -> None:
    text = format_run(_run())
    for heading in (
        "## Headline",
        "## Involuntary hit@10 by target",
        "## Deliberate recall of targets that left hot",
        "## Approximate search (HNSW) misses",
        "## Selection and cost per pass",
        "## Every probe on day 2",
    ):
        assert heading in text
    assert "+0 pts" in text  # branch against the no_decay baseline
    assert "R C (cold)" in text
    assert "| 0.16 |" in text  # the target's score on the cold store's own scale


# ---------------------------------------------------------------------------
# Opt-in: the whole loop against a real throwaway Redis 8 container
# ---------------------------------------------------------------------------

needs_docker = pytest.mark.skipif(
    os.getenv("ALFRED_MEMORY_EVAL_DOCKER") != "1" or shutil.which("docker") is None,
    reason="set ALFRED_MEMORY_EVAL_DOCKER=1 to run the memory eval against a real Redis",
)


@needs_docker
async def test_twenty_days_against_real_redisearch() -> None:
    days = 20
    dataset = build_dataset(days=days)
    settings = _settings().model_copy(update={"days": days, "measure_days": [days]})

    run = await run_eval(
        dataset,
        ["no_decay", "branch", "oracle"],
        HashEmbedder(),
        AlfredConfig(),
        settings,
        RedisSandbox,
        progress=lambda _line: None,
    )

    migrated = {p.name: sum(p.migrated for p in p.passes) for p in run.policies}
    assert migrated["no_decay"] == 0
    assert migrated["oracle"] > migrated["branch"] > 0
    for policy in run.policies:
        assert len(policy.passes) == days * 24
        assert [c.day for c in policy.checkpoints] == [days]
        assert policy.checkpoints[0].probes
        # Problem 1: FT.SEARCH without LIMIT returns RediSearch's default 10 per
        # vector field, so a search-selected pass never sees more than 20.
        if policy.name != "oracle":
            assert max(p.candidates for p in policy.passes) <= 20
