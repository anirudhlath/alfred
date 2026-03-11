"""Multi-process supervisor for Alfred core services.

Spawns each service as a child process, prefixes log output,
restarts on crash with exponential backoff, and propagates
shutdown signals for clean teardown.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceSpec:
    """Specification for a supervised child service."""

    name: str
    module: str
    delay: float = 0.0
    max_restarts: int = 5


class _ManagedService:
    """Mutable runtime state for a supervised service."""

    __slots__ = ("process", "restart_count", "spec")

    def __init__(self, spec: ServiceSpec) -> None:
        self.spec = spec
        self.process: asyncio.subprocess.Process | None = None
        self.restart_count = 0


class Supervisor:
    """Start and monitor Alfred core services as child processes.

    - Prefixed log output per service
    - Automatic restart with exponential backoff on crash
    - Graceful shutdown via SIGTERM/SIGINT propagation
    - Shuts down everything if any service exceeds *max_restarts*
    """

    def __init__(self, services: list[ServiceSpec]) -> None:
        self._managed = [_ManagedService(spec=s) for s in services]
        self._shutdown = asyncio.Event()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _start_process(self, svc: _ManagedService) -> None:
        """Spawn a service as a child process."""
        cmd = [sys.executable, "-u", "-m", svc.spec.module]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        svc.process = proc
        logger.info("[%s] started (PID %d)", svc.spec.name, proc.pid)

    async def _pipe_output(self, svc: _ManagedService) -> None:
        """Read child stdout and re-emit with a service-name prefix."""
        proc = svc.process
        if proc is None or proc.stdout is None:
            return
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").rstrip("\n")
            if line:
                print(f"[{svc.spec.name}] {line}", flush=True)

    async def _monitor(self, svc: _ManagedService) -> None:
        """Start, watch, and restart a service on crash."""
        # Optional startup delay (e.g. let bridge come up before reflex).
        if svc.spec.delay > 0:
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=svc.spec.delay)
                return  # shutdown requested during delay
            except TimeoutError:
                pass

        while not self._shutdown.is_set():
            await self._start_process(svc)
            pipe_task = asyncio.create_task(self._pipe_output(svc))

            assert svc.process is not None
            returncode = await svc.process.wait()
            # Drain remaining buffered output before moving on.
            await asyncio.gather(pipe_task, return_exceptions=True)

            if self._shutdown.is_set():
                return

            if returncode == 0:
                logger.info("[%s] exited cleanly", svc.spec.name)
                self._shutdown.set()
                return

            svc.restart_count += 1
            if svc.restart_count > svc.spec.max_restarts:
                logger.error(
                    "[%s] crashed %d times, giving up",
                    svc.spec.name,
                    svc.restart_count,
                )
                self._shutdown.set()
                return

            backoff = min(2.0**svc.restart_count, 30.0)
            logger.warning(
                "[%s] exited with code %d — restarting in %.0fs (%d/%d)",
                svc.spec.name,
                returncode,
                backoff,
                svc.restart_count,
                svc.spec.max_restarts,
            )

            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=backoff)
                return  # shutdown requested during backoff
            except TimeoutError:
                pass  # backoff elapsed, restart

    async def _terminate_all(self) -> None:
        """Send SIGTERM to running children, SIGKILL after timeout."""
        alive = [
            svc
            for svc in self._managed
            if svc.process is not None and svc.process.returncode is None
        ]
        for svc in alive:
            assert svc.process is not None
            logger.info("[%s] stopping (PID %d)", svc.spec.name, svc.process.pid)
            svc.process.terminate()

        async def _wait_or_kill(svc: _ManagedService) -> None:
            assert svc.process is not None
            try:
                await asyncio.wait_for(svc.process.wait(), timeout=10.0)
            except TimeoutError:
                logger.warning(
                    "[%s] force-killing (PID %d)",
                    svc.spec.name,
                    svc.process.pid,
                )
                svc.process.kill()

        await asyncio.gather(*(_wait_or_kill(svc) for svc in alive))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> int:
        """Run the supervisor until shutdown.

        Returns 0 on clean shutdown, 1 if any service exhausted its restarts.
        """
        loop = asyncio.get_running_loop()

        def on_signal() -> None:
            if not self._shutdown.is_set():
                logger.info("Shutdown signal received")
                self._shutdown.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, on_signal)

        monitor_tasks = [asyncio.create_task(self._monitor(svc)) for svc in self._managed]

        # Block until shutdown is triggered
        # (signal, clean exit of a service, or max_restarts exceeded).
        await self._shutdown.wait()

        for task in monitor_tasks:
            task.cancel()

        await self._terminate_all()

        crashed = any(svc.restart_count > svc.spec.max_restarts for svc in self._managed)
        return 1 if crashed else 0
