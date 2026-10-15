"""``alfred prompts``: keep the repo defaults and Langfuse in step.

``push`` uploads a default as a new version where its text differs from Langfuse's
``latest`` (``--promote`` also labels it ``production``); ``diff`` shows drift against a
label and exits 1 when there is any; ``pull`` writes a label's versions back to the
defaults. Reads ``LANGFUSE_HOST`` and the project keys from the environment.
"""

from __future__ import annotations

import asyncio
import difflib
import os
from typing import TYPE_CHECKING, Annotated, Any, Final

import typer

from shared.config import DEFAULT_PROMPT_LABEL
from shared.prompts.langfuse import LangfuseClient, LangfuseError
from shared.prompts.names import PROMPTS
from shared.prompts.template import DEFAULTS_DIR, repo_text, unusable_reason

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence
    from pathlib import Path

    from shared.prompts.langfuse import PromptAdmin, PromptFetcher

CLI_TIMEOUT_S: Final = 10.0
PRODUCTION: Final = DEFAULT_PROMPT_LABEL
LATEST: Final = "latest"
PUSH_COMMIT_MESSAGE: Final = "alfred prompts push"

prompts_app = typer.Typer(
    no_args_is_help=True, help="Alfred's prompts in Langfuse: push, diff and pull."
)

NamesArg = Annotated[list[str] | None, typer.Argument(help="Prompt names (default: every prompt)")]
LabelOpt = Annotated[str, typer.Option(help="The Langfuse label to compare with")]


async def push_prompts(client: PromptAdmin, names: Sequence[str], *, promote: bool) -> list[str]:
    """Create a version of each prompt whose default differs from ``latest``."""
    lines: list[str] = []
    for name in names:
        text = repo_text(name)
        latest = await client.get(name, label=LATEST)
        if latest is not None and latest.text == text:
            if promote and PRODUCTION not in latest.labels:
                kept = [label for label in latest.labels if label != LATEST]
                await client.set_labels(name, latest.version, [*kept, PRODUCTION])
                lines.append(f"{name}: v{latest.version} unchanged, promoted")
            else:
                lines.append(f"{name}: v{latest.version} unchanged")
            continue
        created = await client.create(
            name,
            text,
            labels=[PRODUCTION] if promote else [],
            commit_message=PUSH_COMMIT_MESSAGE,
        )
        lines.append(f"{name}: pushed v{created.version}" + (" (production)" if promote else ""))
    return lines


async def diff_prompts(
    client: PromptFetcher, names: Sequence[str], *, label: str
) -> tuple[list[str], bool]:
    """Unified diffs from each default to *label*'s version, and whether any differ."""
    lines: list[str] = []
    drifted = False
    for name in names:
        remote = await client.get(name, label=label)
        if remote is None:
            lines.append(f"{name}: not in Langfuse at label {label}")
            drifted = True
            continue
        if remote.text is None:
            lines.append(f"{name}: v{remote.version} is not a text prompt")
            drifted = True
            continue
        text = repo_text(name)
        if remote.text == text:
            continue
        drifted = True
        lines.extend(
            difflib.unified_diff(
                text.splitlines(),
                remote.text.splitlines(),
                fromfile=f"repo/{name}",
                tofile=f"langfuse/{name}@{label} (v{remote.version})",
                lineterm="",
            )
        )
    return lines, drifted


async def pull_prompts(
    client: PromptFetcher,
    names: Sequence[str],
    *,
    label: str,
    defaults_dir: Path = DEFAULTS_DIR,
) -> list[str]:
    """Write *label*'s version of each prompt over its default, unless it is unusable."""
    lines: list[str] = []
    for name in names:
        remote = await client.get(name, label=label)
        if remote is None:
            lines.append(f"{name}: not in Langfuse at label {label}; kept")
            continue
        reason = unusable_reason(name, remote.type, remote.text)
        if reason is not None or remote.text is None:
            lines.append(f"{name}: v{remote.version} {reason}; kept")
            continue
        path = defaults_dir / f"{name}.md"
        if path.exists() and path.read_text(encoding="utf-8") == remote.text + "\n":
            lines.append(f"{name}: v{remote.version} unchanged")
            continue
        path.write_text(remote.text + "\n", encoding="utf-8")
        lines.append(f"{name}: wrote v{remote.version}")
    repo_text.cache_clear()
    return lines


def _client() -> LangfuseClient:
    values = {
        name: os.environ.get(name, "").strip()
        for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        typer.echo(f"Set {', '.join(missing)} first.", err=True)
        raise typer.Exit(2)
    return LangfuseClient(
        values["LANGFUSE_HOST"],
        public_key=values["LANGFUSE_PUBLIC_KEY"],
        secret_key=values["LANGFUSE_SECRET_KEY"],
        timeout_s=CLI_TIMEOUT_S,
    )


def _selected(names: list[str] | None) -> list[str]:
    unknown = [n for n in names or [] if n not in PROMPTS]
    if unknown:
        typer.echo(f"Unknown prompts: {', '.join(unknown)}", err=True)
        raise typer.Exit(2)
    return list(names) if names else list(PROMPTS)


@prompts_app.command()
def push(
    names: NamesArg = None,
    promote: Annotated[
        bool, typer.Option("--promote", help="Label the pushed versions production")
    ] = False,
) -> None:
    """Upload repo defaults that differ from Langfuse's latest version."""
    selected = _selected(names)
    client = _client()

    async def go() -> list[str]:
        try:
            return await push_prompts(client, selected, promote=promote)
        finally:
            await client.aclose()

    _echo(go)


@prompts_app.command()
def diff(names: NamesArg = None, label: LabelOpt = PRODUCTION) -> None:
    """Show where Langfuse's version at LABEL differs from the repo; exit 1 on drift."""
    selected = _selected(names)
    client = _client()

    async def go() -> tuple[list[str], bool]:
        try:
            return await diff_prompts(client, selected, label=label)
        finally:
            await client.aclose()

    try:
        lines, drifted = asyncio.run(go())
    except LangfuseError as exc:
        typer.echo(f"Langfuse: {exc}", err=True)
        raise typer.Exit(1) from exc
    for line in lines:
        typer.echo(line)
    if drifted:
        raise typer.Exit(1)
    typer.echo(f"No drift at label {label}.")


@prompts_app.command()
def pull(names: NamesArg = None, label: LabelOpt = PRODUCTION) -> None:
    """Write Langfuse's versions at LABEL back to the repo defaults."""
    selected = _selected(names)
    client = _client()

    async def go() -> list[str]:
        try:
            return await pull_prompts(client, selected, label=label)
        finally:
            await client.aclose()

    _echo(go)


def _echo(go: Callable[[], Coroutine[Any, Any, list[str]]]) -> None:
    """Run *go* and print the report lines it returns."""
    try:
        lines = asyncio.run(go())
    except LangfuseError as exc:
        typer.echo(f"Langfuse: {exc}", err=True)
        raise typer.Exit(1) from exc
    for line in lines:
        typer.echo(line)
