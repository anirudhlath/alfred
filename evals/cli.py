"""``alfred evals`` — the PRD eval suites (docs/evals.md) and the memory-decay simulation.

Imports of the harness stay inside the commands: ``inspect_ai`` ships in the ``evals``
extra, and ``alfred --help`` must work without it.
"""

from __future__ import annotations

from typing import Annotated

import typer

evals_app = typer.Typer(no_args_is_help=True, help="Evaluate Alfred against its PRD.")

DEFAULT_MODEL = "gemma-4-26b-a4b"
DEFAULT_VLLM_URL = "http://localhost:8000/v1"

ModelOpt = Annotated[str, typer.Option(help="vLLM served model, used in every role")]
VllmUrlOpt = Annotated[str, typer.Option(help="vLLM base URL, with /v1")]


@evals_app.command()
def calibrate(model: ModelOpt = DEFAULT_MODEL, vllm_url: VllmUrlOpt = DEFAULT_VLLM_URL) -> None:
    """Measure how often the judge agrees with the hand-labelled items."""
    import asyncio

    from evals.harness.judge import (
        CALIBRATION_FILE,
        TRUST_THRESHOLD,
        Judge,
        load_calibration_sets,
        make_judge_model,
        save_report,
    )
    from evals.harness.judge import (
        calibrate as run_calibration,
    )

    judge = Judge(make_judge_model(model, vllm_url))
    report = asyncio.run(run_calibration(judge, load_calibration_sets(), model))
    save_report(report)
    for category, result in sorted(report.categories.items()):
        mark = "trusted" if result.agreement >= TRUST_THRESHOLD else "UNTRUSTED"
        typer.echo(
            f"{category:13} {result.agreement:5.0%} of {result.n}  {mark}"
            + (f"  disagreed: {', '.join(result.disagreements)}" if result.disagreements else "")
        )
    typer.echo(f"saved {CALIBRATION_FILE}")


@evals_app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def memory(ctx: typer.Context) -> None:
    """Memory-decay simulation — same arguments as `python -m evals memory`."""
    from evals.__main__ import main as memory_main

    memory_main(["memory", *ctx.args], prog="alfred evals")
