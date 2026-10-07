"""One throwaway Alfred container for a suite: boot, readiness, teardown."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
from redis.exceptions import RedisError

from alfredctl.runtime import Runtime, eval_container_name, host_alias_args, image_tag
from bus.schemas.events import AlfredResponse, UserRequest
from core.channels.request_bus import publish_and_wait
from evals.harness.fake_ha import EVAL_HA_TOKEN, FakeHA
from evals.harness.net import (
    HOST_FIREWALL_DOC,
    IN_CONTAINER_HOST,
    container_reachable,
    in_container_url,
)
from evals.harness.preflight import describe_failure
from shared.redis_streams import create_redis

if TYPE_CHECKING:
    from collections.abc import Mapping

    from evals.harness.proxy import LlmProxy
    from shared.types import AioRedis

logger = logging.getLogger(__name__)

CONSCIOUS_SOURCE = "conscious-engine"
EVAL_SOURCE = "alfred-evals"
EVAL_OPENROUTER_PLACEHOLDER = "alfred-eval-not-a-key"
EVAL_SIGNAL_NUMBER = "+15550100"
EVAL_GUEST_SIGNAL_NUMBER = "+15550199"
_BGE_M3_RECALL_FLOOR = "0.575"  # CLAUDE.md: bge-m3 needs 0.575 (EXP-009)
# Every eval container runs on docker: `alfredctl up --eval --runtime docker`, and the probe.
EVAL_RUNTIME = Runtime(name="docker", exe="docker")
PROBE_CONNECT_TIMEOUT_S = 5.0


class StackError(RuntimeError):
    """The eval container could not be brought up or answered nothing."""


@dataclass(frozen=True)
class StackConfig:
    model: str
    vllm_url: str  # host view, with /v1
    embed_url: str  # host view, without /v1
    embed_model: str
    work_dir: Path
    home_service_dir: Path
    boot_timeout_s: float = 420.0
    reply_timeout_s: float = 120.0
    keep: bool = False


def container_env(cfg: StackConfig, *, proxy_port: int, fake_ha_port: int) -> dict[str, str]:
    """Everything the container needs; nothing from the operator's environment."""
    proxy = in_container_url(proxy_port)
    env = {
        "REFLEX_BACKEND": "openai",
        "OPENAI_COMPAT_HOST": proxy,
        "OPENAI_COMPAT_MODEL": cfg.model,
        "CLAUDE_MODEL": f"openai/{cfg.model}",
        "OPENAI_API_BASE": f"{proxy}/v1",
        "OPENAI_BASE_URL": f"{proxy}/v1",
        "OPENROUTER_API_KEY": EVAL_OPENROUTER_PLACEHOLDER,
        "EMBEDDING_BACKEND": "openai",
        "EMBEDDING_HOST": container_reachable(cfg.embed_url),
        "EMBEDDING_MODEL": cfg.embed_model,
        "HA_HOST": in_container_url(fake_ha_port),
        "HA_TOKEN": EVAL_HA_TOKEN,
        "SIGNAL_PHONE_NUMBER": EVAL_SIGNAL_NUMBER,
        "LIBRARIAN_INTERVAL_SECONDS": "86400",
    }
    if cfg.embed_model == "BAAI/bge-m3":
        env["INVOLUNTARY_RECALL_THRESHOLD"] = _BGE_M3_RECALL_FLOOR
    return env


_NO_SUCH_CONTAINER = re.compile(r"no such (container|object)", re.IGNORECASE)


async def run_cmd(
    cmd: list[str], *, timeout: float = 600, env: dict[str, str] | None = None
) -> str:
    def _run() -> str:
        out = subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=timeout, env=env
        )
        return out.stdout

    shown = f"{' '.join(cmd[:3])} …"
    try:
        return await asyncio.to_thread(_run)
    except (subprocess.SubprocessError, OSError) as exc:
        raise StackError(describe_failure(shown, exc)) from exc


class Docker:
    """The few docker commands the stack needs."""

    async def run_cmd(self, cmd: list[str], *, timeout: float = 600) -> str:
        return await run_cmd(cmd, timeout=timeout)

    async def port(self, name: str, container_port: int) -> int:
        cmd = ["docker", "port", name, f"{container_port}/tcp"]
        out = await run_cmd(cmd, timeout=30)
        try:
            return int(out.splitlines()[0].rsplit(":", 1)[1])
        except (IndexError, ValueError):
            raise StackError(f"{' '.join(cmd)} printed no host port: {out!r}") from None

    async def running(self, name: str) -> bool:
        """False when the container has stopped or docker says it does not exist. Any other
        docker failure raises: a hung daemon is not an exited container."""
        try:
            out = await run_cmd(["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=30)
        except StackError as exc:
            cause = exc.__cause__
            if isinstance(cause, subprocess.CalledProcessError) and _NO_SUCH_CONTAINER.search(
                cause.stderr or ""
            ):
                return False
            raise
        return out.strip() == "true"

    async def logs_tail(self, name: str, lines: int = 60) -> str:
        def _run() -> str:
            out = subprocess.run(
                ["docker", "logs", "--tail", str(lines), name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return (out.stdout + out.stderr)[-6000:]

        try:
            return await asyncio.to_thread(_run)
        except (subprocess.SubprocessError, OSError) as exc:
            return f"(no logs: {describe_failure(f'docker logs {name}', exc)})"

    async def wipe_data(self, name: str, data_dir: Path, image: str) -> None:
        """The container writes /data as root, so empty it as root: from inside the
        container while it runs, else from a throwaway one that mounts the dir."""
        wipe = ["find", "/data", "-mindepth", "1", "-delete"]
        if await self.running(name):
            await run_cmd(["docker", "exec", name, *wipe], timeout=120)
        else:
            await run_cmd(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--entrypoint",
                    wipe[0],
                    "-v",
                    f"{data_dir}:/data",
                    image,
                    *wipe[1:],
                ],
                timeout=120,
            )

    async def remove(self, name: str) -> None:
        try:
            await asyncio.to_thread(
                subprocess.run, ["docker", "rm", "-f", name], capture_output=True, timeout=60
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise StackError(describe_failure(f"docker rm -f {name}", exc)) from exc


# Runs in the probe container: one line, "<port> <why>", per port it cannot connect to.
_PROBE_SCRIPT = """\
import socket, sys
host, timeout = sys.argv[1], float(sys.argv[2])
for port in sys.argv[3:]:
    try:
        socket.create_connection((host, int(port)), timeout=timeout).close()
    except OSError as exc:
        print(port, " ".join(str(exc).split()) or type(exc).__name__, flush=True)
"""


async def probe_host_ports(
    ports: Mapping[str, int],
    *,
    gateway: str,
    docker: Docker | None = None,
    timeout_s: float = PROBE_CONNECT_TIMEOUT_S,
) -> None:
    """Raise unless a throwaway container of the eval image, given the stack's host alias,
    can open a TCP connection to each named host port. A host firewall that drops
    container→host traffic otherwise shows only as a boot that times out minutes later."""
    docker = docker or Docker()
    cmd = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        *host_alias_args(EVAL_RUNTIME),
        "--entrypoint",
        "python",
        image_tag(),
        "-c",
        _PROBE_SCRIPT,
        IN_CONTAINER_HOST,
        str(timeout_s),
        *(str(port) for port in ports.values()),
    ]
    out = await docker.run_cmd(cmd, timeout=60 + timeout_s * len(ports))
    why: dict[str, str] = {}
    for line in out.splitlines():
        port, _, reason = line.strip().partition(" ")
        why[port] = reason
    failed = [(name, port) for name, port in ports.items() if str(port) in why]
    if not failed:
        return
    unreached = " or ".join(
        f"the {name} at {IN_CONTAINER_HOST}:{port} ({why[str(port)]})" for name, port in failed
    )
    allow = ", ".join(str(port) for _, port in failed)
    raise StackError(
        f"a container cannot reach {unreached}, which this host serves on the docker bridge "
        f"gateway {gateway} — a host firewall is probably dropping container→host traffic; "
        f"allow TCP {allow} to {gateway} from the docker bridge (see {HOST_FIREWALL_DOC})"
    )


HealthFn = Callable[[int], Awaitable[bool]]


async def _http_health(port: int) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            return (await client.get(f"http://127.0.0.1:{port}/health")).status_code == 200
    except httpx.HTTPError:
        return False


class Stack:
    def __init__(
        self,
        cfg: StackConfig,
        *,
        fake_ha: FakeHA,
        proxy: LlmProxy,
        docker: Docker | None = None,
        alfredctl: Path | None = None,
        health: HealthFn | None = None,
    ) -> None:
        self.cfg = cfg
        self.fake_ha = fake_ha
        self.proxy = proxy
        self.docker = docker or Docker()
        self._alfredctl = alfredctl or Path(sys.executable).parent / "alfredctl"
        self._health = health or _http_health
        self.name = eval_container_name()
        self.image = image_tag()
        self.redis: AioRedis | None = None
        self.web_port: int | None = None
        self.redis_port: int | None = None
        self.data_dir: Path | None = None
        # The suite's first boot and its first request after boot: set by the first readiness
        # only. A restart (an isolated golden) keeps both, so the stack line means what the
        # warm-up golden says.
        self.boot_seconds: float | None = None
        self.first_reply_ms: float | None = None

    def up_command(self, data_dir: Path) -> list[str]:
        cmd = [
            str(self._alfredctl),
            "up",
            "--eval",
            "--runtime",
            EVAL_RUNTIME.name,
            "--no-build",
            "--persist",
            str(data_dir),
        ]
        env = container_env(self.cfg, proxy_port=self.proxy.port, fake_ha_port=self.fake_ha.port)
        for key, value in env.items():
            cmd += ["--env", f"{key}={value}"]
        return cmd

    async def start(self) -> None:
        self.fake_ha.reset()
        self.cfg.work_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(tempfile.mkdtemp(prefix="alfred-eval-data-", dir=self.cfg.work_dir))
        t0 = time.monotonic()
        await self.docker.run_cmd(self.up_command(self.data_dir), timeout=300)
        self.web_port = await self.docker.port(self.name, 8081)
        self.redis_port = await self.docker.port(self.name, 6379)
        await self._wait_ready(t0)

    async def _fail(self, why: str) -> StackError:
        return StackError(
            f"{self.name}: {why}\n--- docker logs ---\n{await self.docker.logs_tail(self.name)}"
        )

    async def _wait_ready(self, t0: float) -> None:
        deadline = t0 + self.cfg.boot_timeout_s
        assert self.web_port is not None and self.redis_port is not None
        while not await self._health(self.web_port):
            if not await self.docker.running(self.name):
                raise await self._fail("exited during boot")
            if time.monotonic() > deadline:
                raise await self._fail(f"/health not ready after {self.cfg.boot_timeout_s:.0f}s")
            await asyncio.sleep(2)
        try:
            await asyncio.wait_for(
                self.fake_ha.connected.wait(), max(deadline - time.monotonic(), 1)
            )
        except TimeoutError:
            raise await self._fail(
                "home-service never connected to the fake Home Assistant"
            ) from None
        self.redis = create_redis(f"redis://127.0.0.1:{self.redis_port}")
        while True:
            sent = time.monotonic()
            request = UserRequest(
                source=EVAL_SOURCE,
                channel="web_pwa",
                session_id=f"eval-ready-{uuid4().hex[:8]}",
                identity_claim="sir",
                content_type="text",
                content="Reply with the single word: ready.",
            )
            remaining = deadline - time.monotonic()
            try:
                reply = await publish_and_wait(
                    self.redis, request, request.session_id, timeout=max(min(60.0, remaining), 1.0)
                )
            except (RedisError, OSError) as exc:
                raise await self._fail(f"lost redis during readiness: {exc}") from exc
            if reply.source == CONSCIOUS_SOURCE:
                # Conscious can answer with a fallback when its LLM call failed: only a call
                # the proxy saw proves System 2 reaches the model.
                if not self.proxy.calls_between(sent, time.monotonic()):
                    raise await self._fail(
                        "System 2 answered the readiness request without reaching the LLM "
                        "proxy — check the container's LLM settings"
                    )
                reply_ms = (time.monotonic() - sent) * 1000
                break
            if not await self.docker.running(self.name):
                raise await self._fail("exited before System 2 answered")
            if time.monotonic() > deadline:
                raise await self._fail("System 2 never answered the readiness request")
        boot_seconds = time.monotonic() - t0
        if self.boot_seconds is None:
            self.boot_seconds = boot_seconds
        if self.first_reply_ms is None:
            self.first_reply_ms = reply_ms
        logger.info("%s ready in %.0fs (first reply %.0f ms)", self.name, boot_seconds, reply_ms)

    async def alive(self) -> bool:
        """Raises StackError when docker itself cannot say."""
        return await self.docker.running(self.name)

    async def send(self, request: UserRequest, timeout_s: float) -> AlfredResponse:
        if self.redis is None:
            raise StackError("stack is not started")
        try:
            return await publish_and_wait(
                self.redis, request, request.session_id, timeout=timeout_s
            )
        except (RedisError, OSError) as exc:
            raise StackError(
                f"{self.name}: lost redis sending {request.session_id}: {exc}"
            ) from exc

    def _manual_cleanup(self, data_dir: Path | None) -> str:
        steps = [f"docker rm -f {self.name}"]
        if data_dir is not None:
            steps.append(f"sudo rm -rf {data_dir}")  # the container wrote it as root
        return "; ".join(steps)

    def _log_left_behind(
        self, step: str, exc: Exception, data_dir: Path | None, *, keeping: bool = False
    ) -> None:
        if keeping:
            logger.error("%s: %s failed during teardown: %s", self.name, step, exc)
            return
        logger.error(
            "%s: %s failed during teardown: %s. Clean up whatever is left by hand: %s",
            self.name,
            step,
            exc,
            self._manual_cleanup(data_dir),
        )

    async def _teardown(self, *, force: bool) -> None:
        """Never raises: ``stop()`` runs in the orchestrator's ``finally``, where an error
        would replace the boot error the operator needs. Each failure is logged and the
        rest still runs; a container left behind goes at the next ``alfredctl up``, which
        runs ``rm -f`` first."""
        data_dir = self.data_dir
        keeping = self.cfg.keep and not force
        if self.redis is not None:
            try:
                await self.redis.aclose()
            except Exception as exc:
                self._log_left_behind("closing its redis client", exc, data_dir, keeping=keeping)
            finally:
                self.redis = None
        if keeping:
            logger.warning("--keep: leaving %s and %s in place", self.name, data_dir)
            return
        # Remove first: wiping /data under a live container races its writes.
        try:
            await self.docker.remove(self.name)
        except Exception as exc:
            self._log_left_behind("removing the container", exc, data_dir)
        if data_dir is None:
            return
        try:
            await self.docker.wipe_data(self.name, data_dir, self.image)
        except Exception as exc:
            self._log_left_behind("wiping its data dir", exc, data_dir)
        shutil.rmtree(data_dir, ignore_errors=True)
        self.data_dir = None
        if data_dir.exists():
            logger.error(
                "%s: %s survived teardown (files the container wrote as root); "
                "delete it by hand: sudo rm -rf %s",
                self.name,
                data_dir,
                data_dir,
            )

    async def stop(self) -> None:
        await self._teardown(force=False)

    async def restart(self) -> None:
        await self._teardown(force=True)
        await self.start()
