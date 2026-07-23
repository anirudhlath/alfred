"""Multi-process supervisor for Alfred core services.

Spawns each service as a child process, prefixes log output,
restarts on crash with exponential backoff, and propagates
shutdown signals for clean teardown.

Hot-reload: when ``reload=True``, source directories are watched
with ``watchfiles`` and affected services are restarted on change.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Directories that all services depend on — changes here restart everything.
_SHARED_DIRS = ("shared", "sdk", "bus/schemas", "core/integrations")


def _watch_dirs_for_module(module: str, root: Path) -> list[Path]:
    """Return directories to watch for a given service module."""
    # e.g. "core.reflex" → "core/reflex", "bus" → "bus"
    pkg_dir = root / module.replace(".", "/")
    dirs = [pkg_dir]
    for shared in _SHARED_DIRS:
        d = root / shared
        if d.is_dir():
            dirs.append(d)
    return [d for d in dirs if d.is_dir()]


@dataclass(frozen=True)
class ServiceSpec:
    """Specification for a supervised child service (python module or native command)."""

    name: str
    module: str | None = None
    command: list[str] | None = None
    delay: float = 0.0
    max_restarts: int = 5
    watch_dirs: list[str] = field(default_factory=list)
    ready_check: Callable[[], Awaitable[bool]] | None = None

    def __post_init__(self) -> None:
        if (self.module is None) == (self.command is None):
            raise ValueError(f"{self.name}: exactly one of 'module' or 'command' must be set")


class _ManagedService:
    """Mutable runtime state for a supervised service."""

    __slots__ = ("_reloading", "process", "restart_count", "spec")

    def __init__(self, spec: ServiceSpec) -> None:
        self.spec = spec
        self.process: asyncio.subprocess.Process | None = None
        self.restart_count = 0
        self._reloading = False


class Supervisor:
    """Start and monitor Alfred core services as child processes.

    - Prefixed log output per service
    - Automatic restart with exponential backoff on crash
    - Graceful shutdown via SIGTERM/SIGINT propagation
    - Shuts down everything if any service exceeds *max_restarts*
    - Hot-reload on source file changes (when ``reload=True``)
    """

    def __init__(
        self,
        services: list[ServiceSpec],
        *,
        reload: bool = False,
        root: Path | None = None,
    ) -> None:
        self._managed = [_ManagedService(spec=s) for s in services]
        self._shutdown = asyncio.Event()
        self._reload = reload
        self._root = root or Path.cwd()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _start_process(self, svc: _ManagedService) -> None:
        """Spawn a service as a child process (python module or native command)."""
        if svc.spec.command is not None:
            cmd = list(svc.spec.command)
        else:
            assert svc.spec.module is not None
            cmd = [sys.executable, "-u", "-m", svc.spec.module]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        svc.process = proc
        logger.info("[%s] started (PID %d)", svc.spec.name, proc.pid)

    async def _pipe_output(self, svc: _ManagedService) -> None:
        """Read child stdout and re-emit it. No prefix — loguru already tags with [service]."""
        proc = svc.process
        if proc is None or proc.stdout is None:
            return
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").rstrip("\n")
            if line:
                print(line, flush=True)

    async def _await_ready(self, svc: _ManagedService, timeout: float = 30.0) -> bool:
        """Probe a service's ready_check until it passes or timeout (startup gate only)."""
        check = svc.spec.ready_check
        if check is None:
            return True
        deadline = timeout
        interval = 0.25
        elapsed = 0.0
        while elapsed < deadline and not self._shutdown.is_set():
            try:
                if await check():
                    logger.info("[%s] ready", svc.spec.name)
                    return True
            except Exception:
                pass
            await asyncio.sleep(interval)
            elapsed += interval
        logger.error("[%s] not ready after %.0fs", svc.spec.name, timeout)
        return False

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

            # Hot-reload triggered — restart immediately, no backoff.
            if svc._reloading:
                svc._reloading = False
                logger.info("[%s] reloaded", svc.spec.name)
                continue

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

    async def _reload_service(self, svc: _ManagedService) -> None:
        """Terminate a service so the monitor loop restarts it."""
        if svc.process is not None and svc.process.returncode is None:
            svc._reloading = True
            logger.info("[%s] file changed — restarting", svc.spec.name)
            svc.process.terminate()

    async def _watch_files(self) -> None:
        """Watch source directories and restart services on changes."""
        try:
            from watchfiles import Change, awatch
        except ImportError:
            logger.warning("watchfiles not installed — hot-reload disabled")
            return

        # Build a mapping: directory → list of managed services
        dir_to_svcs: dict[Path, list[_ManagedService]] = {}
        for svc in self._managed:
            if svc.spec.module is None:
                continue  # native commands are not hot-reloaded
            for d in _watch_dirs_for_module(svc.spec.module, self._root):
                dir_to_svcs.setdefault(d, []).append(svc)
            for extra in svc.spec.watch_dirs:
                d = self._root / extra
                if d.is_dir():
                    dir_to_svcs.setdefault(d, []).append(svc)

        all_dirs = list(dir_to_svcs.keys())
        if not all_dirs:
            return

        logger.info("Hot-reload watching %d directories", len(all_dirs))

        async for changes in awatch(
            *all_dirs,
            stop_event=self._shutdown,
            step=500,
        ):
            # Determine which services are affected by the changed files.
            affected: set[str] = set()
            for change_type, path_str in changes:
                if change_type == Change.deleted:
                    continue
                p = Path(path_str)
                if p.suffix != ".py":
                    continue
                for watch_dir, svcs in dir_to_svcs.items():
                    if p.is_relative_to(watch_dir):
                        for svc in svcs:
                            if svc.spec.name not in affected:
                                affected.add(svc.spec.name)
                                await self._reload_service(svc)

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

        Returns 0 on clean shutdown, 1 if any service exhausted its restarts
        or a readiness gate never passed (so a container PID 1 fails loudly
        instead of reporting success with infra never up).
        """
        loop = asyncio.get_running_loop()

        def on_signal() -> None:
            if not self._shutdown.is_set():
                logger.info("Shutdown signal received")
                self._shutdown.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, on_signal)

        infra = [m for m in self._managed if m.spec.ready_check is not None]
        rest = [m for m in self._managed if m.spec.ready_check is None]

        gate_failed = False
        monitor_tasks = [asyncio.create_task(self._monitor(m)) for m in infra]
        if infra:
            ready = await asyncio.gather(*(self._await_ready(m) for m in infra))
            if not all(ready):
                gate_failed = True
                self._shutdown.set()
        if not self._shutdown.is_set():
            monitor_tasks += [asyncio.create_task(self._monitor(m)) for m in rest]

        # Start file watcher for hot-reload.
        watcher_task: asyncio.Task[None] | None = None
        if self._reload:
            watcher_task = asyncio.create_task(self._watch_files())

        # Block until shutdown is triggered
        # (signal, clean exit of a service, or max_restarts exceeded).
        await self._shutdown.wait()

        for task in monitor_tasks:
            task.cancel()
        if watcher_task is not None:
            watcher_task.cancel()

        await self._terminate_all()

        crashed = any(svc.restart_count > svc.spec.max_restarts for svc in self._managed)
        return 1 if (crashed or gate_failed) else 0
