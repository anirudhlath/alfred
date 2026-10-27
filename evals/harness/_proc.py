"""Running a command the harness depends on, and saying why it did not succeed."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def describe_failure(shown: str, exc: subprocess.SubprocessError | OSError) -> str:
    """Why the command *shown* did not succeed, with whatever it printed about it."""
    if isinstance(exc, subprocess.CalledProcessError):
        return f"{shown} failed (exit {exc.returncode}):\n{exc.stderr or exc.stdout}"
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"{shown} timed out after {exc.timeout:.0f}s"
    return f"{shown} could not run: {exc}"


def run_checked(cmd: list[str], *, timeout: float, error: Callable[[str], Exception]) -> str:
    """*cmd*'s stdout. Any failure to run it raises ``error(why)``, chained to the cause."""
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as exc:
        raise error(describe_failure(" ".join(cmd), exc)) from exc
    return out.stdout
