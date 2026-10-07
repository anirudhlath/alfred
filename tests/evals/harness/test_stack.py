from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from bus.schemas.events import AlfredResponse, UserRequest
from evals.harness.fake_ha import FakeHA
from evals.harness.proxy import LlmProxy
from evals.harness.stack import Docker, Stack, StackConfig, StackError, container_env, run_cmd
from evals.harness.world import load_world

if TYPE_CHECKING:
    from collections.abc import Callable


def cfg(tmp_path: Path, **kw) -> StackConfig:  # type: ignore[no-untyped-def]
    base = dict(
        model="gemma-4-26b-a4b",
        vllm_url="http://localhost:8000/v1",
        embed_url="http://localhost:8001",
        embed_model="BAAI/bge-m3",
        work_dir=tmp_path,
        home_service_dir=tmp_path,
        boot_timeout_s=5.0,
    )
    return StackConfig(**{**base, **kw})


class FakeDocker:
    def __init__(self, running: bool = True) -> None:
        self.running_value = running
        self.commands: list[list[str]] = []
        self.removed: list[str] = []
        self.wiped: list[Path] = []

    async def run_cmd(self, cmd: list[str], *, timeout: float = 600) -> str:
        self.commands.append(cmd)
        return ""

    async def port(self, name: str, container_port: int) -> int:
        return 40000 + container_port % 1000

    async def running(self, name: str) -> bool:
        return self.running_value

    async def logs_tail(self, name: str, lines: int = 60) -> str:
        return "Traceback: conscious crashed"

    async def wipe_data(self, name: str, data_dir: Path, image: str) -> None:
        self.wiped.append(data_dir)

    async def remove(self, name: str) -> None:
        self.removed.append(name)


def make_stack(tmp_path: Path, docker: FakeDocker, *, healthy: bool = True, **kw) -> Stack:  # type: ignore[no-untyped-def]
    ha = FakeHA(load_world("apartment"))

    async def health(port: int) -> bool:
        # start() resets the fake HA first; a healthy container's home-service then connects.
        if healthy:
            ha.connected.set()
        return healthy

    return Stack(
        cfg(tmp_path, **kw),
        fake_ha=ha,
        proxy=LlmProxy("http://x"),
        docker=docker,  # type: ignore[arg-type]
        alfredctl=Path("/venv/bin/alfredctl"),
        health=health,
    )


def test_container_env_points_every_llm_at_the_proxy_and_ha_at_the_fake(tmp_path: Path) -> None:
    env = container_env(cfg(tmp_path), proxy_port=9100, fake_ha_port=9200)
    assert env["HA_HOST"] == "http://host.docker.internal:9200"
    assert env["HA_TOKEN"] == "alfred-eval-ha-token"
    assert env["OPENAI_COMPAT_HOST"] == "http://host.docker.internal:9100"
    assert env["OPENAI_API_BASE"] == env["OPENAI_BASE_URL"] == "http://host.docker.internal:9100/v1"
    assert env["CLAUDE_MODEL"] == "openai/gemma-4-26b-a4b"
    assert env["EMBEDDING_HOST"] == "http://host.docker.internal:8001"
    assert env["OPENROUTER_API_KEY"] == "alfred-eval-not-a-key"


def test_up_command_is_eval_mode_with_every_env_pair(tmp_path: Path) -> None:
    stack = make_stack(tmp_path, FakeDocker())
    cmd = stack.up_command(tmp_path / "data")
    assert cmd[:4] == ["/venv/bin/alfredctl", "up", "--eval", "--runtime"]
    assert "--no-build" in cmd and cmd[cmd.index("--persist") + 1] == str(tmp_path / "data")
    assert any(c.startswith("HA_HOST=http://host.docker.internal:") for c in cmd)


async def test_boot_fails_fast_with_logs_when_the_container_exits(tmp_path: Path) -> None:
    docker = FakeDocker(running=False)
    stack = make_stack(tmp_path, docker, healthy=False)
    with pytest.raises(StackError, match="conscious crashed"):
        await stack.start()


async def test_readiness_waits_for_a_conscious_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(["channels", "conscious-engine"])

    async def fake_publish(redis, request: UserRequest, session_id: str, timeout: float):  # type: ignore[no-untyped-def]
        return AlfredResponse(
            source=next(answers), channel="web_pwa", session_id=session_id, text="ready"
        )

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", fake_publish)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: _NullRedis())
    stack = make_stack(tmp_path, FakeDocker())
    await stack.start()
    assert stack.first_reply_ms is not None and stack.boot_seconds is not None
    await stack.stop()


async def test_stop_wipes_data_and_removes_the_container_unless_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_publish(redis, request, session_id, timeout):  # type: ignore[no-untyped-def]
        return AlfredResponse(
            source="conscious-engine", channel="web_pwa", session_id=session_id, text="ready"
        )

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", fake_publish)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: _NullRedis())
    docker = FakeDocker()
    stack = make_stack(tmp_path, docker)
    await stack.start()
    await stack.stop()
    assert docker.removed == [stack.name] and len(docker.wiped) == 1

    kept = FakeDocker()
    stack = make_stack(tmp_path, kept, keep=True)
    await stack.start()
    await stack.stop()
    assert kept.removed == []


class _NullRedis:
    async def aclose(self) -> None:
        return None


# The orchestrator catches only StackError: every way a docker call can fail must become one.

_CANNOT_RUN = [
    pytest.param(lambda: subprocess.TimeoutExpired(["docker"], 30), "timed out", id="timeout"),
    pytest.param(
        lambda: FileNotFoundError(2, "No such file or directory", "docker"),
        "could not run",
        id="missing-executable",
    ),
]


def _raising(make_error: Callable[[], Exception]):  # type: ignore[no-untyped-def]
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise make_error()

    return run


def _printing(stdout: str):  # type: ignore[no-untyped-def]
    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    return run


@pytest.mark.parametrize(("make_error", "says"), _CANNOT_RUN)
async def test_run_cmd_names_the_command_it_could_not_run(
    monkeypatch: pytest.MonkeyPatch, make_error: Callable[[], Exception], says: str
) -> None:
    monkeypatch.setattr(subprocess, "run", _raising(make_error))
    with pytest.raises(StackError, match=says) as err:
        await run_cmd(["docker", "port", "alfred-eval-x", "8081/tcp"], timeout=30)
    assert "docker port alfred-eval-x" in str(err.value)


async def test_run_cmd_names_the_command_that_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = subprocess.CalledProcessError(1, ["docker"], output="", stderr="no such container")
    monkeypatch.setattr(subprocess, "run", _raising(lambda: failed))
    with pytest.raises(StackError, match="no such container") as err:
        await run_cmd(["docker", "port", "alfred-eval-x", "8081/tcp"])
    assert "docker port alfred-eval-x" in str(err.value)


async def test_docker_port_reads_the_first_host_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", _printing("127.0.0.1:49153\n[::]:49153\n"))
    assert await Docker().port("alfred-eval-x", 8081) == 49153


@pytest.mark.parametrize("printed", ["", "\n", "no port here\n", "127.0.0.1:http\n"])
async def test_docker_port_refuses_output_without_a_host_port(
    monkeypatch: pytest.MonkeyPatch, printed: str
) -> None:
    monkeypatch.setattr(subprocess, "run", _printing(printed))
    with pytest.raises(StackError, match="docker port alfred-eval-x 8081/tcp"):
        await Docker().port("alfred-eval-x", 8081)


@pytest.mark.parametrize(("make_error", "says"), _CANNOT_RUN)
async def test_logs_tail_reports_why_there_are_no_logs(
    monkeypatch: pytest.MonkeyPatch, make_error: Callable[[], Exception], says: str
) -> None:
    monkeypatch.setattr(subprocess, "run", _raising(make_error))
    logs = await Docker().logs_tail("alfred-eval-x")
    assert "no logs" in logs and says in logs


@pytest.mark.parametrize(("make_error", "says"), _CANNOT_RUN)
async def test_remove_names_the_container_it_could_not_remove(
    monkeypatch: pytest.MonkeyPatch, make_error: Callable[[], Exception], says: str
) -> None:
    monkeypatch.setattr(subprocess, "run", _raising(make_error))
    with pytest.raises(StackError, match=says) as err:
        await Docker().remove("alfred-eval-x")
    assert "docker rm -f alfred-eval-x" in str(err.value)
