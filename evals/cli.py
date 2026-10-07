"""``alfred evals`` — the PRD eval suites (docs/evals.md) and the memory-decay simulation.

Imports of the harness stay inside the commands: ``inspect_ai`` ships in the ``evals``
extra, and ``alfred --help`` must work without it.
"""

from __future__ import annotations

import typer

evals_app = typer.Typer(no_args_is_help=True, help="Evaluate Alfred against its PRD.")


@evals_app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def memory(ctx: typer.Context) -> None:
    """Memory-decay simulation — same arguments as `python -m evals memory`."""
    from evals.__main__ import main as memory_main

    memory_main(["memory", *ctx.args], prog="alfred evals")
