"""``alfred evals`` — the PRD eval suites (docs/evals.md) and the memory-decay simulation.

Imports of the harness stay inside the commands: ``inspect_ai`` ships in the ``evals``
extra, and ``alfred --help`` must work without it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from evals.harness.display import Display
from evals.harness.net import DEFAULT_FAKE_HA_PORT, DEFAULT_PROXY_PORT

evals_app = typer.Typer(no_args_is_help=True, help="Evaluate Alfred against its PRD.")

DEFAULT_MODEL = "gemma-4-26b-a4b"
DEFAULT_VLLM_URL = "http://localhost:8000/v1"

ModelOpt = Annotated[str, typer.Option(help="vLLM served model, used in every role")]
VllmUrlOpt = Annotated[str, typer.Option(help="vLLM base URL, with /v1")]

DEFAULT_EMBED_URL = "http://localhost:8001"
DEFAULT_EMBED_MODEL = "BAAI/bge-m3"

SuitesArg = Annotated[list[str] | None, typer.Argument(help="Suites (default: all)")]
TagOpt = Annotated[list[str] | None, typer.Option("--tag", help="Only goldens with this tag")]
PendingOpt = Annotated[bool, typer.Option("--include-pending", help="Also pending goldens")]
DisplayOpt = Annotated[Display, typer.Option(help="Inspect's console display")]
FakeHaPortOpt = Annotated[
    int,
    typer.Option(min=0, max=65535, help="Port the fake Home Assistant listens on (0: any free)"),
]
ProxyPortOpt = Annotated[
    int, typer.Option(min=0, max=65535, help="Port the LLM proxy listens on (0: any free)")
]


@evals_app.command()
def calibrate(model: ModelOpt = DEFAULT_MODEL, vllm_url: VllmUrlOpt = DEFAULT_VLLM_URL) -> None:
    """Measure how often the judge agrees with the hand-labelled items."""
    import asyncio

    from evals.harness.judge import (
        CALIBRATION_FILE,
        Judge,
        load_calibration_sets,
        make_judge_model,
        save_report,
    )
    from evals.harness.judge import (
        calibrate as run_calibration,
    )

    sets = load_calibration_sets()
    judge = Judge(make_judge_model(model, vllm_url))
    try:
        report = asyncio.run(run_calibration(judge, sets, model))
    except Exception as exc:
        detail = " ".join(f"{type(exc).__name__}: {exc}".split())
        typer.echo(f"the judge at --vllm-url {vllm_url} did not answer: {detail}", err=True)
        raise typer.Exit(1) from exc
    save_report(report, CALIBRATION_FILE)
    trusted = report.trusted()
    for category, result in sorted(report.categories.items()):
        line = f"{category:13} {result.agreement:5.0%} of {result.n}  "
        line += "trusted" if category in trusted else "UNTRUSTED"
        wrong = [i for i in result.disagreements if i not in result.unparseable]
        if wrong:
            line += f"  disagreed: {', '.join(wrong)}"
        if result.unparseable:
            line += f"  unparseable: {', '.join(result.unparseable)}"
        typer.echo(line)
    typer.echo(f"saved {CALIBRATION_FILE}")


@evals_app.command("list")
def list_cmd(
    suites: SuitesArg = None, tag: TagOpt = None, include_pending: PendingOpt = False
) -> None:
    """List goldens: id, status, variants and PRD rows."""
    from evals.harness.scenario import ScenarioError, expand_variants, load_suites, select

    try:
        loaded = load_suites(suites or None)
    except ScenarioError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    for scenarios in loaded.values():
        for s in select(scenarios, tags=tag or [], include_pending=include_pending):
            variants = len(expand_variants(s))
            line = f"{s.id:55} {s.status:8} ×{variants}  {', '.join(s.prd)}"  # noqa: RUF001
            typer.echo(line)


@evals_app.command()
def run(
    suites: SuitesArg = None,
    tag: TagOpt = None,
    include_pending: PendingOpt = False,
    epochs: Annotated[int, typer.Option(min=1, help="Runs per golden")] = 3,
    model: ModelOpt = DEFAULT_MODEL,
    vllm_url: VllmUrlOpt = DEFAULT_VLLM_URL,
    embed_url: Annotated[str, typer.Option(help="Embedding server, without /v1")] = (
        DEFAULT_EMBED_URL
    ),
    embed_model: Annotated[str, typer.Option(help="Embedding model")] = DEFAULT_EMBED_MODEL,
    home_service: Annotated[
        Path | None,
        typer.Option(
            help="home-service checkout to bundle "
            "(default: $ALFRED_EVALS_HOME_SERVICE, else the sibling repo)"
        ),
    ] = None,
    allow_stale_home_service: Annotated[bool, typer.Option("--allow-stale-home-service")] = False,
    build: Annotated[bool, typer.Option("--build/--no-build", help="Build the image first")] = True,
    keep: Annotated[
        bool,
        typer.Option(
            "--keep",
            help="Leave the eval container and data dirs for debugging. Every suite's "
            "container has the same name, so only the last suite's container survives; "
            "a restart (an isolated golden, or a recovery) replaces its suite's data dir",
        ),
    ] = False,
    display: DisplayOpt = "rich",
    fake_ha_port: FakeHaPortOpt = DEFAULT_FAKE_HA_PORT,
    proxy_port: ProxyPortOpt = DEFAULT_PROXY_PORT,
) -> None:
    """Boot throwaway stacks and score the goldens. Prints the scorecard."""
    import asyncio
    import os

    from alfredctl import staging
    from evals.harness.orchestrate import LOG_ROOT, RunOptions, run_suites
    from evals.harness.preflight import PreflightError
    from evals.harness.scenario import ScenarioError
    from evals.harness.stack import StackError

    hs = home_service or Path(
        os.environ.get("ALFRED_EVALS_HOME_SERVICE") or staging.home_service_dir()
    )
    opts = RunOptions(
        suites=list(suites or []),
        tags=tag or [],
        include_pending=include_pending,
        epochs=epochs,
        model=model,
        vllm_url=vllm_url,
        embed_url=embed_url,
        embed_model=embed_model,
        home_service=hs,
        allow_stale_home_service=allow_stale_home_service,
        build=build,
        keep=keep,
        log_root=LOG_ROOT,
        display=display,
        fake_ha_port=fake_ha_port,
        proxy_port=proxy_port,
    )
    try:
        outcome = asyncio.run(run_suites(opts))
    except (ScenarioError, PreflightError, StackError) as exc:
        typer.echo(f"alfred evals: {exc}", err=True)
        raise typer.Exit(1) from exc
    run_dir = outcome.run_dir
    typer.echo(f"logs and report: {run_dir}  (inspect view --log-dir {run_dir})")
    if outcome.unstarted:
        typer.echo(
            f"alfred evals: the stack for {', '.join(outcome.unstarted)} failed to start; "
            "see Run problems in the scorecard",
            err=True,
        )
        raise typer.Exit(1)


@evals_app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def memory(ctx: typer.Context) -> None:
    """Memory-decay simulation — same arguments as `python -m evals memory`."""
    from evals.__main__ import main as memory_main

    memory_main(["memory", *ctx.args], prog="alfred evals")
