"""One throwaway Alfred container for a suite: boot, readiness, teardown."""

from __future__ import annotations

import asyncio
import logging
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

from alfredctl.runtime import eval_container_name, image_tag
from bus.schemas.events import AlfredResponse, UserRequest
from core.channels.request_bus import publish_and_wait
from evals.harness.fake_ha import EVAL_HA_TOKEN, FakeHA
from evals.harness.net import container_reachable, in_container_url
from shared.redis_streams import create_redis

if TYPE_CHECKING:
    from evals.harness.proxy import LlmProxy
    from shared.types import AioRedis

logger = logging.getLogger(__name__)

CONSCIOUS_SOURCE = "conscious-engine"
EVAL_SOURCE = "alfred-evals"
EVAL_OPENROUTER_PLACEHOLDER = "alfred-eval-not-a-key"
EVAL_SIGNAL_NUMBER = "+15550100"
_BGE_M3_RECALL_FLOOR = "0.575"  # CLAUDE.md: bge-m3 needs 0.575 (EXP-009)


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


def _cannot_run(shown: str, exc: subprocess.TimeoutExpired | OSError) -> str:
    """Why *shown* never finished: it ran out of time, or never started (no executable)."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"{shown} timed out after {exc.timeout:.0f}s"
    return f"{shown} could not run: {exc}"


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
    except subprocess.CalledProcessError as exc:
        raise StackError(f"{shown} failed:\n{exc.stderr or exc.stdout}") from exc
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise StackError(_cannot_run(shown, exc)) from exc


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
        try:
            out = await run_cmd(["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=30)
        except StackError:
            return False
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
        except (subprocess.TimeoutExpired, OSError) as exc:
            return f"(no logs: {_cannot_run(f'docker logs {name}', exc)})"

    async def wipe_data(self, name: str, data_dir: Path, image: str) -> None:
        """The container writes /data as root; empty it from inside before removing."""
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
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise StackError(_cannot_run(f"docker rm -f {name}", exc)) from exc


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
        self.boot_seconds: float | None = None
        self.first_reply_ms: float | None = None
        self.restarts = 0

    def up_command(self, data_dir: Path) -> list[str]:
        cmd = [
            str(self._alfredctl),
            "up",
            "--eval",
            "--runtime",
            "docker",
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
                authenticated=True,
                content_type="text",
                content="Reply with the single word: ready.",
            )
            remaining = deadline - time.monotonic()
            reply = await publish_and_wait(
                self.redis, request, request.session_id, timeout=max(min(60.0, remaining), 1.0)
            )
            if reply.source == CONSCIOUS_SOURCE:
                self.first_reply_ms = (time.monotonic() - sent) * 1000
                break
            if not await self.docker.running(self.name):
                raise await self._fail("exited before System 2 answered")
            if time.monotonic() > deadline:
                raise await self._fail("System 2 never answered the readiness request")
        self.boot_seconds = time.monotonic() - t0
        logger.info(
            "%s ready in %.0fs (first reply %.0f ms)",
            self.name,
            self.boot_seconds,
            self.first_reply_ms,
        )

    async def alive(self) -> bool:
        return await self.docker.running(self.name)

    async def send(self, request: UserRequest, timeout_s: float) -> AlfredResponse:
        if self.redis is None:
            raise StackError("stack is not started")
        return await publish_and_wait(self.redis, request, request.session_id, timeout=timeout_s)

    def _log_left_behind(self, step: str, exc: Exception, data_dir: Path | None) -> None:
        logger.error(
            "%s: %s failed during teardown: %s. If the container is still there, "
            "remove it by hand: docker rm -f %s (data dir: %s)",
            self.name,
            step,
            exc,
            self.name,
            data_dir,
        )

    async def _teardown(self, *, force: bool) -> None:
        """Never raises: ``stop()`` runs in the orchestrator's ``finally``, where an error
        would replace the boot error the operator needs. Each failure is logged and the
        rest still runs; a container left behind goes at the next ``alfredctl up``, which
        runs ``rm -f`` first."""
        data_dir = self.data_dir
        if self.redis is not None:
            try:
                await self.redis.aclose()
            except Exception as exc:
                self._log_left_behind("closing its redis client", exc, data_dir)
            finally:
                self.redis = None
        if self.cfg.keep and not force:
            logger.warning("--keep: leaving %s and %s in place", self.name, data_dir)
            return
        if data_dir is not None:
            try:
                await self.docker.wipe_data(self.name, data_dir, self.image)
            except Exception as exc:
                self._log_left_behind("wiping its data dir", exc, data_dir)
            shutil.rmtree(data_dir, ignore_errors=True)
            self.data_dir = None
        try:
            await self.docker.remove(self.name)
        except Exception as exc:
            self._log_left_behind("removing the container", exc, data_dir)

    async def stop(self) -> None:
        await self._teardown(force=False)

    async def restart(self) -> None:
        await self._teardown(force=True)
        self.restarts += 1
        await self.start()
