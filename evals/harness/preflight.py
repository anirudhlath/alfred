"""Checks that run before anything is built or booted."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class PreflightError(RuntimeError):
    """The run cannot start; the message says what to fix."""


async def check_models(client: httpx.AsyncClient, base_url: str, model: str) -> None:
    """*base_url* includes ``/v1``. Raises unless the server lists *model*."""
    try:
        response = await client.get(f"{base_url}/models")
        response.raise_for_status()
        found = [str(m.get("id")) for m in response.json().get("data", [])]
    except httpx.HTTPError as exc:
        raise PreflightError(f"{base_url} is not reachable: {exc}") from exc
    if model not in found:
        raise PreflightError(f"{base_url} does not serve {model!r}; it serves {found}")


def _git(path: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def check_home_service(path: Path, *, allow_stale: bool, git: Callable[..., str] = _git) -> str:
    """Return the home-service commit the image will bundle; refuse a stale or dirty one.

    Production follows alfred-home-service ``main``, so evals do too.
    """
    if not (path / ".git").exists():
        raise PreflightError(f"home-service at {path} is not a git checkout (pass --home-service)")
    git(path, "fetch", "-q", "origin", "main")
    head = git(path, "rev-parse", "HEAD")
    upstream = git(path, "rev-parse", "origin/main")
    if allow_stale:
        return head
    if git(path, "status", "--porcelain"):
        raise PreflightError(f"home-service at {path} has uncommitted changes")
    if head != upstream:
        raise PreflightError(
            f"home-service at {path} is at {head[:7]}; production runs origin/main {upstream[:7]}. "
            f"Run: git -C {path} checkout --detach origin/main  "
            "(or pass --allow-stale-home-service)"
        )
    return head
