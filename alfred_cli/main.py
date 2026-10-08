"""``alfred`` — the operator command line. Groups: ``evals``."""

from __future__ import annotations

import typer

from evals.cli import evals_app

app = typer.Typer(no_args_is_help=True, help="Alfred command line.")
app.add_typer(evals_app, name="evals")
