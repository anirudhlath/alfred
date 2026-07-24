"""alfredctl — build and run the Alfred fat container on Docker/Apple container/Podman."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from alfredctl import runtime as rt
from alfredctl import staging

app = typer.Typer(help="Alfred container launcher", no_args_is_help=True)
console = Console()

RuntimeOpt = Annotated[
    str | None, typer.Option("--runtime", help="docker | container | podman (default: auto)")
]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    return subprocess.run(cmd, check=True)


@app.command()
def build(
    runtime: RuntimeOpt = None,
    tag: Annotated[str | None, typer.Option(help="Image tag (default alfred:<branch>)")] = None,
) -> None:
    """Build the fat image from a git-staged context (alfred + home-service)."""
    r = rt.detect(runtime)
    image = tag or rt.image_tag()
    with tempfile.TemporaryDirectory(prefix="alfred-ctx-") as tmp:
        ctx = staging.stage_context(Path(tmp) / "ctx")
        console.print(f"Building [bold]{image}[/bold] with {r.name}…")
        _run([r.exe, "build", "-t", image, "-f", str(ctx / "alfred" / "Containerfile"), str(ctx)])
    console.print(f"[green]Built {image}[/green]")


if __name__ == "__main__":
    app()
