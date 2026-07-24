"""alfredctl — build and run the Alfred fat container on Docker/Apple container/Podman."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from alfredctl import launch, staging
from alfredctl import runtime as rt
from alfredctl import smoke as smoke_mod

app = typer.Typer(help="Alfred container launcher", no_args_is_help=True)
console = Console()

RuntimeOpt = Annotated[
    str | None, typer.Option("--runtime", help="docker | container | podman (default: auto)")
]


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    return subprocess.run(cmd, check=check)


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


@app.command()
def up(
    runtime: RuntimeOpt = None,
    mode: Annotated[str, typer.Option(help="persistent | ephemeral | seed")] = "persistent",
    persist: Annotated[
        Path | None, typer.Option(help="Host dir for /data (persistent mode)")
    ] = None,
    models: Annotated[Path | None, typer.Option(help="Host dir for the model cache volume")] = None,
    hf_cache: Annotated[
        Path | None, typer.Option(help="Existing HF cache to mount at /models/hf")
    ] = None,
    expose_ha: Annotated[
        bool, typer.Option("--expose-ha", help="Publish :1883 (HA edge broker)")
    ] = False,
    expose_home: Annotated[
        bool, typer.Option("--expose-home", help="Publish :8000 (home-service)")
    ] = False,
    port: Annotated[int, typer.Option(help="Host port for the web UI (docker/podman)")] = 8081,
    env: Annotated[
        list[str], typer.Option("--env", "-e", help="Extra KEY=VALUE for the container")
    ] = [],  # noqa: B006
    do_build: Annotated[bool, typer.Option("--build/--no-build", help="Build the image first")] = (
        True
    ),
) -> None:
    """Start this branch's Alfred container (build first if needed)."""
    r = rt.detect(runtime)
    if mode not in ("persistent", "ephemeral", "seed"):
        raise typer.BadParameter("mode must be persistent | ephemeral | seed")
    if do_build:
        build(runtime=r.name, tag=None)
    repo = staging.repo_root()
    models_dir = models or Path.home() / ".cache" / "alfred" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    persist_dir = (persist or repo / "data").resolve() if mode == "persistent" else None
    if persist_dir is not None:
        persist_dir.mkdir(parents=True, exist_ok=True)
    env_file = repo / ".env"
    plan = launch.build_plan(
        r,
        mode=mode,
        persist=persist_dir,
        models=models_dir.resolve(),
        hf_cache=hf_cache.resolve() if hf_cache else None,
        expose_ha=expose_ha,
        expose_home=expose_home,
        port=port,
        extra_env=list(env),
        env_file=env_file if env_file.is_file() else None,
        passphrase=_passphrase(mode, persist_dir),
    )
    _run([r.exe, "rm", "-f", plan.name], check=False)
    _run([r.exe, *plan.run_args])
    console.print(f"[green]{plan.name} started[/green] → {_resolve_url(r, plan)}")


def _passphrase(mode: str, persist_dir: Path | None) -> str:
    """Secrets passphrase: env wins; persistent mode persists a generated one (0600)."""
    if os.getenv("ALFRED_SECRETS_PASSPHRASE"):
        return os.environ["ALFRED_SECRETS_PASSPHRASE"]
    if mode == "persistent" and persist_dir is not None:
        marker = persist_dir / ".secrets-passphrase"
        if marker.is_file():
            return marker.read_text().strip()
        value = secrets.token_urlsafe(32)
        fd = os.open(marker, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (value + "\n").encode())
        finally:
            os.close(fd)
        return value
    return secrets.token_urlsafe(32)  # ephemeral/seed: fresh per run


def _resolve_url(r: rt.Runtime, plan: launch.LaunchPlan) -> str:
    if plan.url_hint != "resolve-ip":
        return plan.url_hint
    try:
        out = subprocess.run(
            [r.exe, "inspect", plan.name], check=True, capture_output=True, text=True
        )
        payload = json.loads(out.stdout)
        entry = payload[0] if isinstance(payload, list) else payload
        status = entry.get("status") or {}
        networks = status.get("networks") or []
        address = str(networks[0].get("ipv4Address", "")) if networks else ""
        ip = address.split("/")[0]
        if ip:
            return f"http://{ip}:8081"
    except Exception:
        pass
    return "http://<container-ip>:8081 (container inspect failed — check `container ls`)"


@app.command()
def down(runtime: RuntimeOpt = None) -> None:
    """Stop and remove this branch's container."""
    r = rt.detect(runtime)
    _run([r.exe, "rm", "-f", rt.container_name()], check=False)
    console.print(f"[green]{rt.container_name()} removed[/green]")


@app.command()
def logs(
    runtime: RuntimeOpt = None,
    follow: Annotated[bool, typer.Option("--follow", "-f")] = False,
) -> None:
    """Stream container logs."""
    r = rt.detect(runtime)
    cmd = [r.exe, "logs"] + (["-f"] if follow else []) + [rt.container_name()]
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        return


@app.command()
def shell(runtime: RuntimeOpt = None) -> None:
    """Exec an interactive shell inside the container."""
    r = rt.detect(runtime)
    subprocess.run([r.exe, "exec", "-it", rt.container_name(), "bash"], check=False)


@app.command()
def urls(
    runtime: RuntimeOpt = None,
    port: Annotated[int, typer.Option(help="Host port for the web UI (docker/podman)")] = 8081,
) -> None:
    """Print the reachable URL(s) for the running container."""
    r = rt.detect(runtime)
    plan = launch.LaunchPlan(
        run_args=[],
        url_hint="resolve-ip" if r.name == "container" else f"http://localhost:{port}",
        name=rt.container_name(),
        image=rt.image_tag(),
    )
    console.print(_resolve_url(r, plan))


@app.command()
def smoke(
    runtime: RuntimeOpt = None,
    keep: Annotated[bool, typer.Option("--keep", help="Leave the container running")] = False,
    attach: Annotated[
        bool, typer.Option("--attach", help="Check the already-running container")
    ] = False,
    hf_cache: Annotated[
        Path | None, typer.Option(help="Existing HF cache to mount at /models/hf")
    ] = None,
    timeout: Annotated[float, typer.Option(help="Seconds to wait for /health")] = 300.0,
) -> None:
    """Boot (seed mode) + verify the containerized stack, then tear it down."""
    r = rt.detect(runtime)
    if not attach:
        up(runtime=r.name, mode="seed", hf_cache=hf_cache)
    plan = launch.LaunchPlan(
        run_args=[],
        url_hint="resolve-ip" if r.name == "container" else "http://localhost:8081",
        name=rt.container_name(),
        image=rt.image_tag(),
    )
    base_url = _resolve_url(r, plan)
    checks = smoke_mod.run_checks(r.exe, plan.name, base_url, timeout=timeout)
    table = Table(title=f"alfred smoke — {plan.name}")
    table.add_column("check")
    table.add_column("result")
    table.add_column("detail")
    for c in checks:
        table.add_row(c.name, "[green]PASS[/green]" if c.passed else "[red]FAIL[/red]", c.detail)
    console.print(table)
    if not keep and not attach:
        down(runtime=r.name)
    if not all(c.passed for c in checks):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
