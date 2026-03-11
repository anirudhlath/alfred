"""CLI entry point: python -m evals."""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

from core.reflex.tool_registry import ToolInfo, ToolRegistry
from evals.compare import compare_runs
from evals.loader import load_scenario, load_scenarios
from evals.models import EvalRun, Verdict
from evals.pipeline import EvalContext, run_scenario
from evals.report import format_comparison, format_run
from evals.scorer import score
from evals.store import build_run_id, list_runs, load_run, save_run
from shared.config import AlfredConfig

_SCENARIOS_DIR = Path(__file__).parent / "scenarios"
_RUNS_DIR = Path(__file__).parent / "runs"
_PREFERENCES_DIR = str(Path(__file__).parent.parent / "core" / "memory" / "preferences")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="evals",
        description="Alfred Evals Runner — scenario-based SLM evaluation",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    run_parser = sub.add_parser("run", help="Run eval scenarios against Ollama")
    run_parser.add_argument("--tag", action="append", dest="tags", help="Filter by tag")
    run_parser.add_argument("--scenario", type=Path, help="Run a single scenario file")
    run_parser.add_argument("--model", help="Override Ollama model")
    run_parser.add_argument(
        "--preferences-dir", type=str, default=_PREFERENCES_DIR, help="Preferences directory"
    )

    # list
    sub.add_parser("list", help="List available scenarios")

    # compare
    cmp_parser = sub.add_parser("compare", help="Compare two runs")
    cmp_parser.add_argument("run_id_1", help="First (older) run ID")
    cmp_parser.add_argument("run_id_2", help="Second (newer) run ID")

    # runs
    sub.add_parser("runs", help="List saved runs")

    return parser.parse_args()


async def _load_tools(config: AlfredConfig) -> list[ToolInfo]:
    """Load tools from Redis registry."""
    import redis.asyncio as aioredis

    r = aioredis.from_url(config.redis_url)
    try:
        registry = ToolRegistry(r)
        return await registry.get_tools()
    finally:
        await r.aclose()


async def _cmd_run(args: argparse.Namespace) -> None:
    """Execute scenarios and produce an eval run."""
    config = AlfredConfig.from_env()
    model = args.model or config.ollama_model

    # Load scenarios
    if args.scenario:
        scenarios = [load_scenario(args.scenario)]
    else:
        scenarios = load_scenarios(_SCENARIOS_DIR, tags=args.tags)

    if not scenarios:
        print("No scenarios found.")
        sys.exit(1)

    # Load tools from Redis
    tools = await _load_tools(config)
    if not tools:
        print("No tools registered in Redis. Is home-service running?")
        sys.exit(1)

    print(f"Running {len(scenarios)} scenarios with model {model}...\n")

    # Pre-compute shared state for the run
    ctx = EvalContext(tools, args.preferences_dir, model)

    # Run each scenario
    results = []
    for scenario in scenarios:
        trace = await run_scenario(
            scenario=scenario,
            tools=tools,
            preferences_dir=args.preferences_dir,
            model=model,
            ctx=ctx,
        )
        result = score(trace, scenario)
        results.append(result)

    timestamp = datetime.now(UTC)
    run = EvalRun(
        run_id=build_run_id(timestamp, model),
        timestamp=timestamp,
        model=model,
        results=results,
    )

    # Save and report
    path = save_run(run, _RUNS_DIR)
    print(format_run(run))
    print(f"Run saved: {path}")

    # Append to research CSV
    _append_research_csv(run, config)


def _append_research_csv(run: EvalRun, config: AlfredConfig) -> None:
    """Append summary stats to research/data/evals.csv."""
    csv_path = Path(config.research_vault_path) / "data" / "evals.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    write_header = not csv_path.exists()
    latencies = [r.trace.latency_ms for r in run.results]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    sorted_lat = sorted(latencies)
    p95_idx = max(0, int(len(sorted_lat) * 0.95) - 1)
    p95_latency = sorted_lat[p95_idx] if sorted_lat else 0

    with open(csv_path, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(
                [
                    "timestamp",
                    "model",
                    "scenarios",
                    "pass",
                    "partial",
                    "fail",
                    "avg_latency_ms",
                    "p95_latency_ms",
                ]
            )
        writer.writerow(
            [
                run.timestamp.isoformat(),
                run.model,
                run.scenario_count,
                run.summary.get(Verdict.PASS, 0),
                run.summary.get(Verdict.PARTIAL, 0),
                run.summary.get(Verdict.FAIL, 0),
                f"{avg_latency:.1f}",
                f"{p95_latency:.1f}",
            ]
        )


def _cmd_list() -> None:
    """List available scenarios."""
    scenarios = load_scenarios(_SCENARIOS_DIR)
    if not scenarios:
        print("No scenarios found.")
        return
    for s in scenarios:
        tags = f"  [{', '.join(s.tags)}]" if s.tags else ""
        desc = f"  — {s.description}" if s.description else ""
        print(f"  {s.name}{tags}{desc}")


def _cmd_runs() -> None:
    """List saved runs."""
    runs = list_runs(_RUNS_DIR)
    if not runs:
        print("No saved runs.")
        return
    for run_id in runs:
        print(f"  {run_id}")


def _cmd_compare(args: argparse.Namespace) -> None:
    """Compare two runs."""
    old = load_run(args.run_id_1, _RUNS_DIR)
    new = load_run(args.run_id_2, _RUNS_DIR)
    diff = compare_runs(old, new)
    print(format_comparison(diff))


def main() -> None:
    args = _parse_args()
    match args.command:
        case "run":
            asyncio.run(_cmd_run(args))
        case "list":
            _cmd_list()
        case "runs":
            _cmd_runs()
        case "compare":
            _cmd_compare(args)


if __name__ == "__main__":
    main()
