"""Tests for the unified runner supervisor."""

from __future__ import annotations

import asyncio

import pytest

from runner.supervisor import ServiceSpec, Supervisor, _ManagedService


class TestServiceSpec:
    def test_defaults(self) -> None:
        spec = ServiceSpec(name="svc", module="some.module")
        assert spec.name == "svc"
        assert spec.module == "some.module"
        assert spec.delay == 0.0
        assert spec.max_restarts == 5

    def test_frozen(self) -> None:
        spec = ServiceSpec(name="svc", module="m")
        with pytest.raises(AttributeError):
            spec.name = "x"  # type: ignore[misc]


class TestManagedService:
    def test_initial_state(self) -> None:
        spec = ServiceSpec(name="svc", module="m")
        managed = _ManagedService(spec)
        assert managed.process is None
        assert managed.restart_count == 0


class TestSupervisor:
    async def test_immediate_shutdown(self) -> None:
        """Supervisor exits cleanly when shutdown is set before run."""
        supervisor = Supervisor([])
        supervisor._shutdown.set()
        code = await supervisor.run()
        assert code == 0

    async def test_service_exits_cleanly(self) -> None:
        """A service that exits 0 triggers shutdown of the supervisor."""
        spec = ServiceSpec(name="quick", module="__hello__", max_restarts=0)
        supervisor = Supervisor([spec])
        code = await supervisor.run()
        assert code == 0

    async def test_crash_and_restart(self) -> None:
        """A crashing service is restarted until max_restarts is exceeded."""
        # Python -m with a non-existent module exits with code 1
        spec = ServiceSpec(
            name="crasher",
            module="__nonexistent_module_for_test__",
            max_restarts=1,
        )
        supervisor = Supervisor([spec])
        code = await supervisor.run()
        assert code == 1

    async def test_startup_delay_respected(self) -> None:
        """A service with delay starts after the specified wait."""
        spec = ServiceSpec(name="delayed", module="__hello__", delay=0.2)
        supervisor = Supervisor([spec])

        started_at = asyncio.get_running_loop().time()
        await supervisor.run()
        elapsed = asyncio.get_running_loop().time() - started_at

        assert elapsed >= 0.15  # allow small tolerance

    async def test_shutdown_cancels_delay(self) -> None:
        """Setting shutdown during startup delay cancels it promptly."""
        spec = ServiceSpec(name="delayed", module="__hello__", delay=10.0)
        supervisor = Supervisor([spec])

        async def cancel_soon() -> None:
            await asyncio.sleep(0.1)
            supervisor._shutdown.set()

        background_tasks: set[asyncio.Task[None]] = set()
        task = asyncio.create_task(cancel_soon())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

        code = await supervisor.run()
        assert code == 0
