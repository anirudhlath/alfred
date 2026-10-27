"""``python -m evals memory {run,show,runs,policies}``."""

from __future__ import annotations

import asyncio
import csv
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as loguru_logger

from core.memory.embedding_backend import build_embedding_provider
from evals.memory.dataset import DEFAULT_DAYS, DEFAULT_SEED, build_dataset
from evals.memory.models import MemoryEvalRun, RunSettings
from evals.memory.policies import POLICIES
from evals.memory.report import format_run, summarize
from evals.memory.runner import run_eval
from evals.memory.sandbox import DEFAULT_REDIS_IMAGE, RedisSandbox
from shared.config import AlfredConfig

if TYPE_CHECKING:
    import argparse

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs" / "memory"
DEFAULT_MEASURE_DAYS = "30,45,60"
CSV_NAME = "memory-decay.csv"

logger = logging.getLogger("evals.memory")


def add_memory_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    memory = sub.add_parser("memory", help="Memory-decay eval (throwaway Redis, no LLM)")
    commands = memory.add_subparsers(dest="memory_command", required=True)

    run = commands.add_parser("run", help="Simulate the house and compare decay policies")
    run.add_argument(
        "--policy",
        action="append",
        dest="policies",
        choices=list(POLICIES),
        help="Policy to run (repeatable; default: all registered)",
    )
    run.add_argument("--days", type=int, default=DEFAULT_DAYS)
    run.add_argument("--seed", type=int, default=DEFAULT_SEED)
    run.add_argument(
        "--measure-days", default=DEFAULT_MEASURE_DAYS, help="Comma-separated checkpoint days"
    )
    run.add_argument("--pass-every-hours", type=int, default=1)
    run.add_argument(
        "--threshold", type=float, default=None, help="Decay threshold (default: AlfredConfig)"
    )
    run.add_argument(
        "--redis-url",
        default=None,
        help="Use this EMPTY throwaway Redis instead of starting a container per policy",
    )
    run.add_argument("--redis-image", default=DEFAULT_REDIS_IMAGE)
    run.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    run.add_argument(
        "--research-csv",
        type=Path,
        default=None,
        help=f"Append checkpoint rows here (default: <research vault>/data/{CSV_NAME})",
    )
    run.add_argument("--no-csv", action="store_true", help="Do not append research rows")

    show = commands.add_parser("show", help="Print the tables for a saved run")
    show.add_argument("run_id")
    show.add_argument("--runs-dir", type=Path, default=RUNS_DIR)

    runs = commands.add_parser("runs", help="List saved memory runs")
    runs.add_argument("--runs-dir", type=Path, default=RUNS_DIR)

    commands.add_parser("policies", help="List registered decay policies")


def run_memory_command(args: argparse.Namespace) -> None:
    match args.memory_command:
        case "run":
            asyncio.run(_cmd_run(args))
        case "show":
            print(format_run(_load(args.runs_dir, args.run_id)))
        case "runs":
            for path in sorted(args.runs_dir.glob("*.json")):
                print(f"  {path.stem}")
        case "policies":
            for name, policy in POLICIES.items():
                print(f"  {name}: {policy.summary}")


def _load(runs_dir: Path, run_id: str) -> MemoryEvalRun:
    return MemoryEvalRun.model_validate_json((runs_dir / f"{run_id}.json").read_text())


async def _cmd_run(args: argparse.Namespace) -> None:
    # The stack under test logs every migration and index operation, and
    # sentence-transformers draws a progress bar per embed whenever the root logger is at
    # INFO — so only the eval's own logger speaks at INFO.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s", stream=sys.stderr)
    logger.setLevel(logging.INFO)
    loguru_logger.remove()  # the Memory Ingestor logs every write at DEBUG via loguru
    loguru_logger.add(sys.stderr, level="WARNING")
    config = AlfredConfig.from_env()
    measure_days = sorted({int(day) for day in args.measure_days.split(",") if day.strip()})
    settings = RunSettings(
        seed=args.seed,
        days=args.days,
        pass_every_hours=args.pass_every_hours,
        measure_days=[day for day in measure_days if day <= args.days],
        threshold=(
            args.threshold if args.threshold is not None else config.decay_migration_threshold
        ),
        embedding_model=config.embedding_model,
        embedding_backend=config.embedding_backend,
        embedding_dim=config.embedding_dim,
        involuntary_recall_limit=config.involuntary_recall_limit,
        involuntary_recall_threshold=config.involuntary_recall_threshold,
        redis_image=args.redis_image if args.redis_url is None else args.redis_url,
    )
    dataset = build_dataset(seed=args.seed, days=args.days)
    provider = build_embedding_provider(config)
    try:
        run = await run_eval(
            dataset,
            args.policies or list(POLICIES),
            provider,
            config,
            settings,
            lambda: RedisSandbox(
                args.redis_url, image=args.redis_image, forbidden_url=config.redis_url
            ),
            progress=logger.info,
        )
    finally:
        await provider.aclose()

    args.runs_dir.mkdir(parents=True, exist_ok=True)
    path = args.runs_dir / f"{run.run_id}.json"
    path.write_text(run.model_dump_json(indent=2))
    print(format_run(run))
    print(f"Run saved: {path}")
    if not args.no_csv:
        csv_path = args.research_csv or Path(config.research_vault_path) / "data" / CSV_NAME
        append_research_rows(run, csv_path)
        print(f"Research rows appended: {csv_path}")


CSV_HEADER = (
    "run_id",
    "timestamp",
    "embedding_model",
    "policy",
    "day",
    "involuntary_hits",
    "probes",
    "open_hits",
    "migrated_targets",
    "tool_reach_hits",
    "recall_reach_hits",
    "cold_reach_hits",
    "kept_significant",
    "significant",
    "kept_recalled",
    "recalled",
    "hot_episodic",
    "noise_older_14d_gone",
    "noise_older_14d",
    "stuck_1d",
    "stuck_7d",
    "migrated_total",
    "mean_pass_ms",
    "decay_embed_calls",
)


def append_research_rows(run: MemoryEvalRun, csv_path: Path) -> None:
    """One row per (policy, checkpoint), appended — research data is never rewritten."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="") as handle:
        writer = csv.writer(handle)
        if write_header:
            writer.writerow(CSV_HEADER)
        for policy in run.policies:
            passes = policy.passes
            migrated_total = sum(p.migrated for p in passes)
            mean_pass_ms = sum(p.wall_ms for p in passes) / len(passes) if passes else 0.0
            for point in policy.checkpoints:
                row = summarize(policy, point)
                writer.writerow(
                    (
                        run.run_id,
                        run.timestamp.isoformat(),
                        run.settings.embedding_model,
                        policy.name,
                        point.day,
                        row.involuntary.hits,
                        row.involuntary.n,
                        row.open.hits,
                        row.migrated_targets,
                        row.tool_reach.hits,
                        row.recall_reach.hits,
                        row.cold_reach.hits,
                        row.kept_significant.hits,
                        row.kept_significant.n,
                        row.kept_recalled.hits,
                        row.kept_recalled.n,
                        row.hot_episodic,
                        row.noise_gone.hits,
                        row.noise_gone.n,
                        row.stuck_1d,
                        row.stuck_7d,
                        migrated_total,
                        f"{mean_pass_ms:.1f}",
                        policy.decay_embed_calls,
                    )
                )
