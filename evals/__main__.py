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
from evals.conscious.runner import run_conscious_evals
from evals.inference import BACKENDS
from evals.loader import load_scenario, load_scenarios
from evals.models import EvalRun, Scenario, Verdict
from evals.pipeline import EvalContext, run_scenario
from evals.report import format_aggregate, format_comparison, format_run, latency_stats
from evals.scorer import score
from evals.store import build_run_id, list_runs, load_run, save_run
from shared.config import AlfredConfig

_SCENARIOS_DIR = Path(__file__).parent / "scenarios"
_RUNS_DIR = Path(__file__).parent / "runs"
_CONTEXTS_DIR = Path(__file__).parent / "contexts"
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
        "--backend",
        choices=list(BACKENDS),
        default="ollama",
        help="Inference backend (default: ollama)",
    )
    run_parser.add_argument(
        "--preferences-dir", type=str, default=_PREFERENCES_DIR, help="Preferences directory"
    )
    run_parser.add_argument(
        "-n", "--repeat", type=int, default=1, help="Number of times to run the full suite"
    )

    # list
    sub.add_parser("list", help="List available scenarios")

    # compare
    cmp_parser = sub.add_parser("compare", help="Compare two runs")
    cmp_parser.add_argument("run_id_1", help="First (older) run ID")
    cmp_parser.add_argument("run_id_2", help="Second (newer) run ID")

    # runs
    sub.add_parser("runs", help="List saved runs")

    # regression
    sub.add_parser("regression", help="Run System 1 evals in regression mode (mocked Ollama)")

    # conscious
    sub.add_parser("conscious", help="Run System 2 evals with DeepEval metrics")

    # demo
    demo_parser = sub.add_parser("demo", help="Run Good Morning end-to-end demo")
    demo_parser.add_argument("--channel", default="web_pwa", choices=["web_pwa", "signal", "voice"])

    # capture-context
    cap_parser = sub.add_parser(
        "capture-context", help="Capture live context from Redis to a fixture file"
    )
    cap_parser.add_argument(
        "--output", default="default.json", help="Output fixture filename in evals/contexts/"
    )

    return parser.parse_args()


async def _load_tools(config: AlfredConfig) -> list[ToolInfo]:
    """Load tools from Redis registry."""
    import redis.asyncio as aioredis

    r = aioredis.from_url(config.redis_url)
    try:
        registry = ToolRegistry(r)
        return await registry.get_tools()
    finally:
        await r.close()


async def _execute_single_run(
    scenarios: list[Scenario],
    preferences_dir: str,
    model: str,
    ctx: EvalContext,
) -> EvalRun:
    """Execute all scenarios in parallel and return an EvalRun."""
    traces = await asyncio.gather(
        *(
            run_scenario(
                scenario=s,
                tools=ctx.tools,
                preferences_dir=preferences_dir,
                model=model,
                ctx=ctx,
            )
            for s in scenarios
        )
    )

    results = [score(trace, s) for trace, s in zip(traces, scenarios, strict=True)]
    timestamp = datetime.now(UTC)
    return EvalRun(
        run_id=build_run_id(timestamp, model),
        timestamp=timestamp,
        model=model,
        results=results,
    )


async def _cmd_run(args: argparse.Namespace) -> None:
    """Execute scenarios and produce eval runs."""
    config = AlfredConfig.from_env()
    model = args.model or config.ollama_model
    infer = BACKENDS[args.backend]

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

    repeat = args.repeat
    n = len(scenarios)
    label = f"{n} scenarios" if repeat == 1 else f"{n} scenarios x {repeat} runs"
    print(f"Running {label} with model {model} ({args.backend})...\n")

    # Pre-compute shared state
    ctx = EvalContext(tools, args.preferences_dir, model, infer=infer)

    # Execute runs sequentially (scenarios within each run are parallel)
    all_runs: list[EvalRun] = []
    for i in range(repeat):
        if repeat > 1:
            print(f"--- Run {i + 1}/{repeat} ---")
        run = await _execute_single_run(scenarios, args.preferences_dir, model, ctx)
        all_runs.append(run)

    # Report each run
    for run in all_runs:
        path = save_run(run, _RUNS_DIR)
        print(format_run(run))
        print(f"Run saved: {path}\n")
        _append_research_csv(run, config)

    # Aggregate report for multi-run
    if repeat > 1:
        print(format_aggregate(list(all_runs)))


def _append_research_csv(run: EvalRun, config: AlfredConfig) -> None:
    """Append summary stats to research/data/evals.csv."""
    csv_path = Path(config.research_vault_path) / "data" / "evals.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    write_header = not csv_path.exists()
    latencies = [r.trace.latency_ms for r in run.results]
    avg_latency, p95_latency = latency_stats(latencies)

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


async def _cmd_capture_context(args: argparse.Namespace) -> None:
    """Scan all alfred:context:* Redis keys and save a fixture file."""
    import json

    import redis.asyncio as aioredis

    from sdk.alfred_sdk.context import ContextSnapshot
    from shared.streams import CONTEXT_KEY_PREFIX

    config = AlfredConfig.from_env()
    r = aioredis.from_url(config.redis_url)
    try:
        pattern = f"{CONTEXT_KEY_PREFIX}*"
        keys: list[bytes] = await r.keys(pattern)
        if not keys:
            print(f"No keys matching {pattern} found in Redis.")
            sys.exit(1)

        sorted_keys = sorted(keys)
        values: list[bytes | None] = await r.mget(*sorted_keys)
        envelope: dict[str, object] = {}
        for key, raw in zip(sorted_keys, values, strict=True):
            if not raw:
                continue
            service_name = key.decode().removeprefix(CONTEXT_KEY_PREFIX)
            snapshot = ContextSnapshot.model_validate_json(raw)
            envelope[service_name] = snapshot.model_dump()
            print(f"  captured: {service_name}")

        _CONTEXTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = _CONTEXTS_DIR / args.output
        output_path.write_text(json.dumps(envelope, indent=2))
        print(f"\nFixture written: {output_path}")
    finally:
        await r.close()


def _cmd_regression() -> None:
    """Run System 1 evals in regression mode."""
    from evals.regression.runner import run_regression

    results = run_regression()
    print(f"Regression: {results['passed']} passed, {results['failed']} failed")
    for s in results["scenarios"]:
        status = "PASS" if s["passed"] else "FAIL"
        print(f"  [{status}] {s['file']}")


def _cmd_conscious() -> None:
    """Run System 2 evals (dry-run without live engine)."""
    import logging

    logging.basicConfig(level=logging.INFO)
    results = run_conscious_evals()
    print(f"\nSystem 2 evals: {len(results)} scenarios loaded")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        detail = f" ({r.details.get('status', '')})" if r.details else ""
        print(f"  [{status}] {r.scenario}{detail}")


def _cmd_demo(args: argparse.Namespace) -> None:
    """Run the Good Morning demo."""
    from evals.e2e.demo_good_morning import run_demo

    asyncio.run(run_demo(channel=args.channel))


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
        case "regression":
            _cmd_regression()
        case "conscious":
            _cmd_conscious()
        case "demo":
            _cmd_demo(args)
        case "compare":
            _cmd_compare(args)
        case "capture-context":
            asyncio.run(_cmd_capture_context(args))


if __name__ == "__main__":
    main()
