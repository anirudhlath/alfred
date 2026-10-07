from __future__ import annotations

import subprocess

import pytest

from evals.harness.net import container_reachable, docker_bridge_gateway
from evals.harness.preflight import PreflightError


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:8001", "http://host.docker.internal:8001"),
        ("http://127.0.0.1:8000/v1", "http://host.docker.internal:8000/v1"),
        ("http://[::1]:8001", "http://host.docker.internal:8001"),
        ("http://0.0.0.0:8001", "http://host.docker.internal:8001"),
        ("http://localhost.localdomain:8001", "http://localhost.localdomain:8001"),
        ("http://embedder:8001/localhost", "http://embedder:8001/localhost"),
    ],
)
def test_container_reachable_rewrites_only_a_loopback_host(url: str, expected: str) -> None:
    assert container_reachable(url) == expected


def _run_failing(error: Exception):  # type: ignore[no-untyped-def]
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise error

    return run


def _run_printing(stdout: str):  # type: ignore[no-untyped-def]
    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    return run


def test_bridge_gateway_failure_carries_dockers_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon_down = subprocess.CalledProcessError(
        1, ["docker"], output="", stderr="Cannot connect to the Docker daemon"
    )
    monkeypatch.setattr(subprocess, "run", _run_failing(daemon_down))
    with pytest.raises(PreflightError, match="Cannot connect to the Docker daemon") as err:
        docker_bridge_gateway()
    assert "docker network inspect bridge" in str(err.value)


def test_bridge_gateway_without_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = FileNotFoundError(2, "No such file or directory", "docker")
    monkeypatch.setattr(subprocess, "run", _run_failing(missing))
    with pytest.raises(PreflightError, match="could not run"):
        docker_bridge_gateway()


def test_bridge_gateway_that_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _run_printing("\n"))
    with pytest.raises(PreflightError, match="no gateway"):
        docker_bridge_gateway()
