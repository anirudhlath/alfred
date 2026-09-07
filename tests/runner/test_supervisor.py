"""Tests for the unified runner supervisor."""

from __future__ import annotations

import asyncio
import functools
import sys

import pytest

from runner.supervisor import _PIPE_LINE_LIMIT, ServiceSpec, Supervisor, _ManagedService


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

    async def test_run_returns_nonzero_when_ready_gate_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PID-1 contract: a container must not report success when a ready gate fails."""

        async def _never_ready() -> bool:
            return False

        spec = ServiceSpec(name="redis", command=["sleep", "5"], ready_check=_never_ready)
        sup = Supervisor([spec])

        async def _fast_gate(self: Supervisor, svc: object, timeout: float = 30.0) -> bool:
            return False

        monkeypatch.setattr(Supervisor, "_await_ready", _fast_gate)
        assert await sup.run() == 1

    async def test_shutdown_during_ready_gate_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A SIGTERM/SIGINT arriving while the readiness gate is still probing must
        be treated as a clean shutdown (return 0), not a gate failure — even though
        the real ``_await_ready`` bails out and returns False once shutdown is set.
        """

        async def _never_ready() -> bool:
            return False

        spec = ServiceSpec(name="redis", command=["sleep", "5"], ready_check=_never_ready)
        sup = Supervisor([spec])

        # Use the REAL _await_ready — only shorten its default timeout so the test
        # stays fast, so the shutdown-during-probe race is genuinely exercised.
        real_await_ready = Supervisor._await_ready
        monkeypatch.setattr(
            Supervisor,
            "_await_ready",
            functools.partialmethod(real_await_ready, timeout=2.0),
        )

        task = asyncio.create_task(sup.run())
        await asyncio.sleep(0.5)
        sup._shutdown.set()  # simulate the signal handler firing mid-probe

        code = await asyncio.wait_for(task, timeout=15.0)
        assert code == 0


# A child that writes one oversized line, then enough further output to fill the
# 64 KiB pipe. If the supervisor stops draining after the long line, the child blocks
# forever on its next write and never exits.
_LONG_LINE_CHILD = (
    "import sys\n"
    "print('x' * int(sys.argv[1]), flush=True)\n"
    "for i in range(20):\n"
    "    print('tail-%d ' % i + 'y' * 8_000, flush=True)\n"
    "print('last-line', flush=True)\n"
)


class TestPipeOutput:
    @pytest.mark.parametrize(
        ("line_len", "dropped"),
        [
            (100_000, False),  # over asyncio's 64 KiB default, under ours: passed through
            (_PIPE_LINE_LIMIT + 1_000, True),  # over ours: dropped with a notice
        ],
    )
    async def test_child_survives_oversized_line(
        self, capsys: pytest.CaptureFixture[str], line_len: int, dropped: bool
    ) -> None:
        """One oversized log line must not deadlock the child (regression: the
        conscious engine hung mid-turn after litellm dumped a >64 KiB request)."""
        spec = ServiceSpec(
            name="chatty",
            command=[sys.executable, "-u", "-c", _LONG_LINE_CHILD, str(line_len)],
            max_restarts=0,
        )
        supervisor = Supervisor([spec], reload=False)
        code = await asyncio.wait_for(supervisor.run(), timeout=10.0)
        assert code == 0
        out = capsys.readouterr().out
        assert ("[chatty] dropped a log line" in out) is dropped
        assert "tail-19" in out
        assert "last-line" in out
