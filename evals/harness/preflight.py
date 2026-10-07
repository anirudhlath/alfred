"""Checks that run before anything is built or booted."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from evals.harness._proc import run_checked

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 120.0


class PreflightError(RuntimeError):
    """The run cannot start; the message says what to fix."""


def base_url(url: str, option: str) -> str:
    """*url* without surrounding space or trailing slashes. A PreflightError naming *option*
    unless it is an http(s) URL with a host and a valid port: httpx would otherwise fail on
    it with a traceback, not an error saying which option is wrong."""
    stripped = url.strip().rstrip("/")
    parts = urlsplit(stripped)
    if parts.scheme not in ("http", "https"):
        raise PreflightError(f"{option} {url!r} is not an http:// or https:// URL")
    if not parts.hostname:
        raise PreflightError(f"{option} {url!r} has no host")
    try:
        parts.port  # noqa: B018 — parsing the port is the check
    except ValueError as exc:
        raise PreflightError(f"{option} {url!r} has an invalid port ({exc})") from exc
    return stripped


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
    cmd = ["git", "-C", str(path), *args]
    return run_checked(cmd, timeout=_GIT_TIMEOUT_S, error=PreflightError).strip()


def alfred_commit(repo: Path, git: Callable[..., str] = _git) -> str:
    """The Alfred commit the image is built from, ``+dirty`` when the checkout has changes.

    The build stages tracked and untracked files alike, so a bare sha would misdescribe
    a checkout with uncommitted or new files.
    """
    head = git(repo, "rev-parse", "HEAD")
    return f"{head}+dirty" if git(repo, "status", "--porcelain") else head


def home_service_commit(path: Path, git: Callable[..., str] = _git) -> str:
    """The home-service checkout's commit, ``+dirty`` with changes, for a run that does not
    build: the image holds whatever home-service it was built with, so the checkout is only
    described, never fetched or refused. ``unknown``, with a warning, when it cannot be read."""
    if not (path / ".git").exists():
        logger.warning("home-service at %s is not a git checkout; its commit is unknown", path)
        return "unknown"
    try:
        head = git(path, "rev-parse", "HEAD")
        dirty = git(path, "status", "--porcelain")
    except PreflightError as exc:
        logger.warning("%s\nthe home-service commit is unknown", exc)
        return "unknown"
    return f"{head}+dirty" if dirty else head


def check_home_service(path: Path, *, allow_stale: bool, git: Callable[..., str] = _git) -> str:
    """Return the home-service commit the image will bundle; refuse a stale or dirty one.

    Production follows alfred-home-service ``main``, so evals do too. *allow_stale*
    waives staleness, and with it the fetch that only serves that check (a hanging network
    would otherwise hold the run up to the git timeout); never uncommitted changes.
    """
    if not (path / ".git").exists():
        raise PreflightError(f"home-service at {path} is not a git checkout (pass --home-service)")
    if not allow_stale:
        try:
            git(path, "fetch", "-q", "origin", "main")
        except PreflightError as exc:
            raise PreflightError(
                f"{exc}\nhome-service at {path} cannot be checked against origin/main. "
                "Fix the fetch, or pass --allow-stale-home-service to use the checkout as it is."
            ) from exc
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
