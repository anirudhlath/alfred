from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import fakeredis
import pytest
from pydantic import ValidationError
from redis import exceptions as redis_exceptions

from alfredctl.runtime import image_tag
from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.identity import IDENTITY_SIR
from evals.harness.evidence import LlmCall
from evals.harness.fake_ha import FakeHA
from evals.harness.proxy import LlmProxy
from evals.harness.stack import (
    Docker,
    Stack,
    StackConfig,
    StackError,
    container_env,
    probe_host_ports,
    run_cmd,
)
from evals.harness.world import load_world
from shared.streams import USER_TIMEZONE_KEY

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
        self.events: list[str] = []

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
        self.events.append("wipe")

    async def remove(self, name: str) -> None:
        self.removed.append(name)
        self.events.append("remove")


class _ReachedProxy(LlmProxy):
    """System 2 reached the LLM for every request it answered: each window holds a call."""

    def __init__(self) -> None:
        super().__init__("http://x")

    def calls_between(self, t0: float, t1: float) -> list[LlmCall]:
        return [LlmCall(t=t0, role="system2", latency_ms=1.0, status=200)]


def make_stack(  # type: ignore[no-untyped-def]
    tmp_path: Path,
    docker: FakeDocker,
    *,
    healthy: bool = True,
    ha_connects: bool = True,
    proxy: LlmProxy | None = None,
    **kw,
) -> Stack:
    ha = FakeHA(load_world("apartment"))

    async def health(port: int) -> bool:
        # start() resets the fake HA first; a healthy container's home-service then connects.
        if healthy and ha_connects:
            ha.connected.set()
        return healthy

    return Stack(
        cfg(tmp_path, **kw),
        fake_ha=ha,
        proxy=proxy or _ReachedProxy(),
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
    assert env["REFLEX_BACKEND"] == env["EMBEDDING_BACKEND"] == "openai"
    assert env["OPENAI_COMPAT_MODEL"] == "gemma-4-26b-a4b"


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
    published: list[str] = []
    authenticated: list[tuple[str, bool]] = []
    proxy = LlmProxy("http://x")

    async def fake_publish(redis, request: UserRequest, session_id: str, timeout: float):  # type: ignore[no-untyped-def]
        published.append(session_id)
        authenticated.append((request.identity_claim, request.authenticated))
        source = next(answers)
        if source == "conscious-engine":  # System 2 reached the LLM through the proxy
            proxy.calls.append(
                LlmCall(t=time.monotonic(), role="system2", latency_ms=1.0, status=200)
            )
        return AlfredResponse(source=source, channel="web_pwa", session_id=session_id, text="ready")

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", fake_publish)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: _NullRedis())
    stack = make_stack(tmp_path, FakeDocker(), proxy=proxy)
    await stack.start()
    assert stack.first_reply_ms is not None and stack.boot_seconds is not None
    # The channels-only answer did not count: it asked again until System 2 replied.
    assert len(published) == 2 and next(answers, None) is None
    # Readiness asks the way real channels do: a server-derived claim, unauthenticated.
    assert authenticated == [(IDENTITY_SIR, False)] * 2
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
    # Wiping /data under a live container would race its writes: remove it first.
    assert docker.events == ["remove", "wipe"]

    kept = FakeDocker()
    stack = make_stack(tmp_path, kept, keep=True)
    await stack.start()
    await stack.stop()
    assert kept.removed == [] and kept.wiped == []
    assert stack.data_dir is not None and stack.data_dir.exists()


async def test_stop_removes_the_work_dir_once_nothing_is_left_in_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_publish(redis, request, session_id, timeout):  # type: ignore[no-untyped-def]
        return AlfredResponse(
            source="conscious-engine", channel="web_pwa", session_id=session_id, text="ready"
        )

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", fake_publish)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: _NullRedis())
    work = tmp_path / "data"  # what a run passes: <run dir>/data
    stack = make_stack(tmp_path, FakeDocker(), work_dir=work)
    await stack.start()
    assert work.is_dir()
    await stack.stop()
    assert not work.exists()  # no empty data/ left in the run dir

    # Only an empty one goes: anything else in it keeps it.
    stack = make_stack(tmp_path, FakeDocker(), work_dir=work)
    await stack.start()
    (work / "left-by-hand").write_text("x", encoding="utf-8")
    await stack.stop()
    assert (work / "left-by-hand").exists()

    # --keep keeps it, with the data dir inside.
    kept = make_stack(tmp_path, FakeDocker(), work_dir=tmp_path / "kept", keep=True)
    await kept.start()
    await kept.stop()
    assert kept.data_dir is not None and kept.data_dir.parent == tmp_path / "kept"
    assert kept.data_dir.exists()


async def test_a_restart_keeps_the_suites_first_boot_and_reply_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An isolated golden restarts the stack; the stack line must still report the suite's
    # first boot and its very first request after boot, not the latest restart's. The
    # restart's readiness answers 50 ms later, so an overwrite could not equal the first.
    delays = iter([0.0, 0.05])

    async def fake_publish(redis, request, session_id, timeout):  # type: ignore[no-untyped-def]
        await asyncio.sleep(next(delays))
        return AlfredResponse(
            source="conscious-engine", channel="web_pwa", session_id=session_id, text="ready"
        )

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", fake_publish)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: _NullRedis())
    stack = make_stack(tmp_path, FakeDocker())
    await stack.start()
    first = (stack.boot_seconds, stack.first_reply_ms)
    assert None not in first
    await stack.restart()
    assert next(delays, None) is None  # the restart really did boot and run readiness again
    assert (stack.boot_seconds, stack.first_reply_ms) == first
    await stack.stop()


class _NullRedis:
    async def aclose(self) -> None:
        return None


# Teardown runs in the orchestrator's finally: it must never replace the boot error.


class _RemoveFails(FakeDocker):
    async def remove(self, name: str) -> None:
        raise StackError(f"docker rm -f {name} timed out after 60s")


class _WipeFails(FakeDocker):
    async def wipe_data(self, name: str, data_dir: Path, image: str) -> None:
        raise StackError(f"docker exec {name} … failed:\nno space left")


class _CloseFails:
    async def aclose(self) -> None:
        raise ConnectionError("redis went away")


def _answer_ready(
    monkeypatch: pytest.MonkeyPatch, redis: object, source: str = "conscious-engine"
) -> None:
    async def fake_publish(redis, request, session_id, timeout):  # type: ignore[no-untyped-def]
        return AlfredResponse(source=source, channel="web_pwa", session_id=session_id, text="ready")

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", fake_publish)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: redis)


def _teardown_errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "evals.harness.stack" and r.levelno == logging.ERROR
    ]


@pytest.mark.parametrize("docker", [_RemoveFails, _WipeFails])
async def test_a_failed_wipe_or_remove_is_logged_with_the_manual_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    docker: type[FakeDocker],
) -> None:
    _answer_ready(monkeypatch, _NullRedis())
    stack = make_stack(tmp_path, docker())
    await stack.start()
    data_dir = stack.data_dir
    assert data_dir is not None and data_dir.exists()

    await stack.stop()

    (logged,) = _teardown_errors(caplog)
    assert f"docker rm -f {stack.name}" in logged and f"sudo rm -rf {data_dir}" in logged
    assert not data_dir.exists()


async def test_a_failed_redis_close_is_logged_and_teardown_carries_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _answer_ready(monkeypatch, _CloseFails())
    docker = FakeDocker()
    stack = make_stack(tmp_path, docker)
    await stack.start()
    data_dir = stack.data_dir
    assert data_dir is not None

    await stack.stop()

    (logged,) = _teardown_errors(caplog)
    assert "redis went away" in logged and f"docker rm -f {stack.name}" in logged
    assert str(data_dir) in logged
    assert stack.redis is None and docker.removed == [stack.name] and not data_dir.exists()


async def test_a_failed_boot_keeps_its_error_through_teardown(tmp_path: Path) -> None:
    stack = make_stack(tmp_path, _RemoveFails(running=False), healthy=False)
    with pytest.raises(StackError, match="exited during boot") as err:
        try:
            await stack.start()
        finally:
            await stack.stop()
    assert "conscious crashed" in str(err.value) and "docker rm -f" not in str(err.value)


async def test_keep_mode_never_suggests_removing_what_it_keeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _answer_ready(monkeypatch, _CloseFails())
    stack = make_stack(tmp_path, FakeDocker(), keep=True)
    await stack.start()
    await stack.stop()
    (logged,) = _teardown_errors(caplog)
    assert "redis went away" in logged and "rm -f" not in logged and "rm -rf" not in logged


async def test_a_data_dir_that_survives_teardown_is_logged_with_the_manual_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _answer_ready(monkeypatch, _NullRedis())
    stack = make_stack(tmp_path, FakeDocker())
    await stack.start()
    data_dir = stack.data_dir
    # Root-owned files the wipe missed: rmtree(ignore_errors=True) leaves the dir behind.
    monkeypatch.setattr("evals.harness.stack.shutil.rmtree", lambda *a, **k: None)
    await stack.stop()
    (logged,) = _teardown_errors(caplog)
    assert f"sudo rm -rf {data_dir}" in logged


# Readiness: every way it gives up says why and carries the container's logs.


async def test_losing_redis_during_readiness_fails_with_the_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def lost(redis, request, session_id, timeout):  # type: ignore[no-untyped-def]
        raise redis_exceptions.ConnectionError("Connection closed by server.")

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", lost)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: _NullRedis())
    stack = make_stack(tmp_path, FakeDocker(running=False))
    with pytest.raises(StackError, match="lost redis during readiness") as err:
        await stack.start()
    assert "conscious crashed" in str(err.value)


def _unreadable_reply() -> ValidationError:
    try:
        AlfredResponse.model_validate({"source": "conscious-engine"})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


async def test_an_unreadable_readiness_reply_fails_with_the_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def garbled(redis, request, session_id, timeout):  # type: ignore[no-untyped-def]
        raise _unreadable_reply()

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", garbled)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: _NullRedis())
    stack = make_stack(tmp_path, FakeDocker(running=False))
    with pytest.raises(StackError, match="unreadable reply during readiness") as err:
        await stack.start()
    assert "conscious crashed" in str(err.value)


async def test_an_unreadable_reply_mid_run_is_a_stack_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer_ready(monkeypatch, _NullRedis())
    stack = make_stack(tmp_path, FakeDocker())
    await stack.start()

    async def garbled(redis, request, session_id, timeout):  # type: ignore[no-untyped-def]
        raise _unreadable_reply()

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", garbled)
    request = UserRequest(
        source="alfred-evals",
        channel="web_pwa",
        session_id="eval-1",
        identity_claim="sir",
        content_type="text",
        content="Turn on the kitchen lights.",
    )
    with pytest.raises(StackError, match="unreadable reply to eval-1"):
        await stack.send(request, timeout_s=1.0)
    await stack.stop()


async def test_losing_redis_mid_run_is_a_stack_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer_ready(monkeypatch, _NullRedis())
    stack = make_stack(tmp_path, FakeDocker())
    await stack.start()

    async def lost(redis, request, session_id, timeout):  # type: ignore[no-untyped-def]
        raise redis_exceptions.ConnectionError("Connection closed by server.")

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", lost)
    request = UserRequest(
        source="alfred-evals",
        channel="web_pwa",
        session_id="eval-1",
        identity_claim="sir",
        content_type="text",
        content="Turn on the kitchen lights.",
    )
    with pytest.raises(StackError, match="Connection closed by server"):
        await stack.send(request, timeout_s=1.0)
    await stack.stop()


@pytest.mark.parametrize(
    ("healthy", "ha_connects", "says"),
    [
        (False, True, "/health not ready"),
        (True, False, "home-service never connected to the fake Home Assistant"),
        (True, True, "System 2 never answered"),
    ],
    ids=["health", "fake-ha", "system-2"],
)
async def test_each_readiness_stage_gives_up_at_the_boot_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, healthy: bool, ha_connects: bool, says: str
) -> None:
    _answer_ready(monkeypatch, _NullRedis(), source="channels")
    stack = make_stack(
        tmp_path, FakeDocker(), healthy=healthy, ha_connects=ha_connects, boot_timeout_s=0.0
    )
    with pytest.raises(StackError, match=says) as err:
        await stack.start()
    assert "conscious crashed" in str(err.value)


_NOT_REACHED = (
    "System 2 answered the readiness request without reaching the LLM proxy — check the "
    "container's LLM settings"
)


@pytest.mark.parametrize(
    ("before", "during", "says"),
    [
        ([("system2", 200)], [], _NOT_REACHED),
        ([], [("system1", 200), ("librarian", 200)], _NOT_REACHED),
        (
            [],
            [("system2", 502)],
            "System 2 answered the readiness request, but the LLM upstream "
            "failed (502) — check http://x",
        ),
        (
            [],
            [("system1", 200), ("system2", 400), ("system2", 503)],
            "System 2 answered the "
            "readiness request, but the LLM upstream failed (400, 503) — check http://x",
        ),
        # System 1 calls from the fake HA's initial states land in this window even when
        # System 2 never reached the proxy, so only `unknown` points at the fingerprints.
        (
            [("unknown", 200)],
            [("system1", 200), ("unknown", 200), ("unknown", 502)],
            "System 2 answered the readiness request, but the LLM proxy saw 2 call(s) it could "
            "not classify (role unknown) and none recognised as System 2 — if System 2's prompt "
            "changed, update ROLE_FINGERPRINTS in evals/harness/proxy.py",
        ),
    ],
    ids=[
        "stale-call",
        "other-roles-only",
        "upstream-failed",
        "every-system2-call-failed",
        "unclassified-calls",
    ],
)
async def test_readiness_needs_a_successful_system2_llm_call_since_the_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: list[tuple[str, int]],
    during: list[tuple[str, int]],
    says: str,
) -> None:
    # Conscious can answer with a fallback when its LLM call failed: that reply proves the
    # container is up, not that System 2 reaches the model. Only a System 2 call made since
    # the readiness request, and answered upstream, does.
    proxy = LlmProxy("http://x")
    proxy.calls += [LlmCall(t=0.0, role=r, latency_ms=1.0, status=st) for r, st in before]  # type: ignore[arg-type]

    async def fake_publish(redis, request, session_id, timeout):  # type: ignore[no-untyped-def]
        proxy.calls.extend(
            LlmCall(t=time.monotonic(), role=r, latency_ms=1.0, status=st)  # type: ignore[arg-type]
            for r, st in during
        )
        return AlfredResponse(
            source="conscious-engine", channel="web_pwa", session_id=session_id, text="ready"
        )

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", fake_publish)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: _NullRedis())
    stack = make_stack(tmp_path, FakeDocker(), proxy=proxy)
    with pytest.raises(StackError) as err:
        await stack.start()
    assert str(err.value).startswith(f"{stack.name}: {says}\n--- docker logs ---\n")
    assert "conscious crashed" in str(err.value)


async def test_one_answered_system2_call_is_enough_beside_a_failed_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy = LlmProxy("http://x")

    async def fake_publish(redis, request, session_id, timeout):  # type: ignore[no-untyped-def]
        for status in (503, 200):  # a retry that got through
            proxy.calls.append(
                LlmCall(t=time.monotonic(), role="system2", latency_ms=1.0, status=status)
            )
        return AlfredResponse(
            source="conscious-engine", channel="web_pwa", session_id=session_id, text="ready"
        )

    monkeypatch.setattr("evals.harness.stack.publish_and_wait", fake_publish)
    monkeypatch.setattr("evals.harness.stack.create_redis", lambda url: _NullRedis())
    stack = make_stack(tmp_path, FakeDocker(), proxy=proxy)
    await stack.start()
    assert stack.first_reply_ms is not None
    await stack.stop()


# The reachability probe: a throwaway container of the eval image, connecting to the fakes.

_FAKES = {"fake HA": 18123, "LLM proxy": 18100}


class _ProbeDocker(FakeDocker):
    def __init__(self, printed: str) -> None:
        super().__init__()
        self.printed = printed

    async def run_cmd(self, cmd: list[str], *, timeout: float = 600) -> str:
        self.commands.append(cmd)
        return self.printed


async def test_the_probe_runs_the_eval_image_with_the_stacks_host_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    docker = FakeDocker()
    await probe_host_ports(_FAKES, gateway="172.17.0.1", docker=docker)  # type: ignore[arg-type]
    (cmd,) = docker.commands
    assert cmd[:3] == ["docker", "run", "--rm"]
    # The alias `alfredctl up --eval` gives the stack's container on Linux docker.
    alias = cmd.index("--add-host")
    assert cmd[alias + 1] == "host.docker.internal:host-gateway"
    tail = cmd[cmd.index("--entrypoint") :]
    assert tail[:4] == ["--entrypoint", "python", image_tag(), "-c"]
    assert tail[5:] == ["host.docker.internal", "5.0", "18123", "18100"]


async def test_the_probe_script_reports_only_the_ports_it_cannot_reach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The script the probe container runs, run here against a real listener and a port
    # nothing listens on.
    docker = FakeDocker()
    await probe_host_ports(_FAKES, gateway="172.17.0.1", docker=docker)  # type: ignore[arg-type]
    (cmd,) = docker.commands
    script = cmd[cmd.index("-c") + 1]
    with socket.socket() as listening, socket.socket() as silent:
        listening.bind(("127.0.0.1", 0))
        listening.listen()
        silent.bind(("127.0.0.1", 0))  # bound, never listening: connections are refused
        reachable, unreachable = listening.getsockname()[1], silent.getsockname()[1]
        out = subprocess.run(
            [sys.executable, "-c", script, "127.0.0.1", "2", str(reachable), str(unreachable)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
    (line,) = out.splitlines()
    assert line.startswith(f"{unreachable} ") and "refused" in line.lower()


async def test_a_probe_that_reaches_every_fake_passes() -> None:
    await probe_host_ports(_FAKES, gateway="172.17.0.1", docker=_ProbeDocker(""))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("printed", "named", "not_named"),
    [
        ("18123 timed out\n", ["the fake HA at host.docker.internal:18123 (timed out)"], "18100"),
        (
            "18123 timed out\n18100 [Errno 111] Connection refused\n",
            [
                "the fake HA at host.docker.internal:18123 (timed out)",
                "the LLM proxy at host.docker.internal:18100 ([Errno 111] Connection refused)",
            ],
            None,
        ),
    ],
    ids=["one-port", "both-ports"],
)
async def test_a_failed_probe_names_the_ports_the_firewall_and_the_fix(
    printed: str, named: list[str], not_named: str | None
) -> None:
    with pytest.raises(StackError) as err:
        await probe_host_ports(_FAKES, gateway="172.17.0.1", docker=_ProbeDocker(printed))  # type: ignore[arg-type]
    message = str(err.value)
    assert "\n" not in message
    assert all(part in message for part in named)
    assert "172.17.0.1" in message and "firewall" in message
    assert message.endswith("(see docs/evals.md#host-firewall)")
    if not_named is not None:
        assert not_named not in message


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


@pytest.mark.parametrize(
    "stderr",
    [
        "Error: No such object: alfred-eval-x",
        "Error response from daemon: No such container: alfred-eval-x",
    ],
)
async def test_running_is_false_only_when_docker_has_no_such_container(
    monkeypatch: pytest.MonkeyPatch, stderr: str
) -> None:
    gone = subprocess.CalledProcessError(1, ["docker"], output="", stderr=stderr)
    monkeypatch.setattr(subprocess, "run", _raising(lambda: gone))
    assert await Docker().running("alfred-eval-x") is False


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: subprocess.CalledProcessError(
            1, ["docker"], output="", stderr="Cannot connect to the Docker daemon"
        ),
        lambda: subprocess.TimeoutExpired(["docker"], 30),
    ],
    ids=["daemon-down", "daemon-hung"],
)
async def test_running_raises_when_docker_itself_fails(
    monkeypatch: pytest.MonkeyPatch, make_error: Callable[[], Exception]
) -> None:
    # A hung daemon is not an exited container: reading it as one restarts for nothing.
    monkeypatch.setattr(subprocess, "run", _raising(make_error))
    with pytest.raises(StackError, match="docker inspect"):
        await Docker().running("alfred-eval-x")


@pytest.mark.parametrize(("printed", "expected"), [("true\n", True), ("false\n", False)])
async def test_running_reads_the_container_state(
    monkeypatch: pytest.MonkeyPatch, printed: str, expected: bool
) -> None:
    monkeypatch.setattr(subprocess, "run", _printing(printed))
    assert await Docker().running("alfred-eval-x") is expected


@pytest.mark.parametrize(
    ("state", "wipe"),
    [
        (
            "true\n",
            ["docker", "exec", "alfred-eval-x", "find", "/data", "-mindepth", "1", "-delete"],
        ),
        (
            "false\n",
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "find",
                "-v",
                "{data}:/data",
                "alfred:x",
                "/data",
                "-mindepth",
                "1",
                "-delete",
            ],
        ),
    ],
    ids=["running-exec", "gone-run"],
)
async def test_wipe_data_empties_data_as_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, wipe: list[str]
) -> None:
    seen: list[list[str]] = []

    def docker_cli(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        stdout = state if cmd[:2] == ["docker", "inspect"] else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", docker_cli)
    await Docker().wipe_data("alfred-eval-x", tmp_path, "alfred:x")
    assert seen[-1] == [part.replace("{data}", str(tmp_path)) for part in wipe]


async def test_the_bus_follows_the_stacks_redis(tmp_path: Path) -> None:
    stack = make_stack(tmp_path, FakeDocker())
    with pytest.raises(StackError, match="not started"):
        await stack.bus.user_timezone()
    stack.redis = fakeredis.FakeAsyncRedis()
    with pytest.raises(StackError, match="not started"):
        stack._web_url()
    stack.web_port = 1234
    await stack.redis.set(USER_TIMEZONE_KEY, "Etc/GMT-7")
    assert await stack.bus.user_timezone() == "Etc/GMT-7"
    assert stack._web_url() == "http://127.0.0.1:1234"
