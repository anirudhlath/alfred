from __future__ import annotations

import subprocess
import sys

import pytest

from evals.harness._proc import describe_failure, run_checked


class RefusedError(RuntimeError):
    pass


def test_run_checked_returns_stdout() -> None:
    assert (
        run_checked([sys.executable, "-c", "print('hi')"], timeout=30, error=RefusedError) == "hi\n"
    )


def test_run_checked_raises_the_callers_error_saying_why() -> None:
    cmd = [sys.executable, "-c", "import sys; sys.exit('no luck')"]
    with pytest.raises(RefusedError, match="failed \\(exit 1\\):\nno luck"):
        run_checked(cmd, timeout=30, error=RefusedError)


def test_run_checked_names_a_command_it_could_not_run() -> None:
    with pytest.raises(RefusedError, match="no-such-command-anywhere could not run"):
        run_checked(["no-such-command-anywhere"], timeout=30, error=RefusedError)


def test_describe_failure_says_when_it_timed_out() -> None:
    exc = subprocess.TimeoutExpired(["sleep", "9"], timeout=2.0)
    assert describe_failure("sleep 9", exc) == "sleep 9 timed out after 2s"
