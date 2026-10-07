"""Checks that run before anything is built or booted."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 120.0


class PreflightError(RuntimeError):
    """The run cannot start; the message says what to fix."""


def describe_failure(shown: str, exc: subprocess.SubprocessError | OSError) -> str:
    """Why the command *shown* did not succeed, with whatever it printed about it."""
    if isinstance(exc, subprocess.CalledProcessError):
        return f"{shown} failed (exit {exc.returncode}):\n{exc.stderr or exc.stdout}"
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"{shown} timed out after {exc.timeout:.0f}s"
    return f"{shown} could not run: {exc}"


def run_checked(cmd: list[str], *, timeout: float) -> str:
    """*cmd*'s stdout; any failure to run it is a PreflightError that says why."""
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as exc:
        raise PreflightError(describe_failure(" ".join(cmd), exc)) from exc
    return out.stdout


async def check_models(client: httpx.AsyncClient, base_url: str, model: str) -> None:
    """*base_url* includes ``/v1``. Raises unless the server lists *model*."""
    try:
        response = await client.get(f"{base_url}/models")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PreflightError(f"{base_url} is not reachable: {exc}") from exc
    try:
        found = [str(m.get("id")) for m in response.json().get("data", [])]
    except (ValueError, AttributeError, TypeError) as exc:
        raise PreflightError(
            f"{base_url}/models is not an OpenAI-compatible model list ({exc}); "
            f"is {base_url} the server's /v1 URL?"
        ) from exc
    if model not in found:
        raise PreflightError(f"{base_url} does not serve {model!r}; it serves {found}")


def _git(path: Path, *args: str) -> str:
    return run_checked(["git", "-C", str(path), *args], timeout=_GIT_TIMEOUT_S).strip()


def check_home_service(path: Path, *, allow_stale: bool, git: Callable[..., str] = _git) -> str:
    """Return the home-service commit the image will bundle; refuse a stale or dirty one.

    Production follows alfred-home-service ``main``, so evals do too. *allow_stale*
    waives staleness only (and a fetch that fails, say offline); never uncommitted changes.
    """
    if not (path / ".git").exists():
        raise PreflightError(f"home-service at {path} is not a git checkout (pass --home-service)")
    try:
        git(path, "fetch", "-q", "origin", "main")
    except PreflightError as exc:
        if not allow_stale:
            raise PreflightError(
                f"{exc}\nhome-service at {path} cannot be checked against origin/main. "
                "Fix the fetch, or pass --allow-stale-home-service to use the checkout as it is."
            ) from exc
        logger.warning("%s\n--allow-stale-home-service: using %s as it is", exc, path)
    head = git(path, "rev-parse", "HEAD")
    if git(path, "status", "--porcelain"):
        raise PreflightError(
            f"home-service at {path} has uncommitted changes; commit or stash them first"
        )
    if allow_stale:
        return head
    upstream = git(path, "rev-parse", "origin/main")
    if head != upstream:
        raise PreflightError(
            f"home-service at {path} is at {head[:7]}; production runs origin/main {upstream[:7]}. "
            f"Run: git -C {path} checkout --detach origin/main  "
            "(or pass --allow-stale-home-service)"
        )
    return head
