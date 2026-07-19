"""Supervisor runs native-command services and gates on readiness."""

from __future__ import annotations

import pytest

from runner.supervisor import ServiceSpec


def test_spec_requires_exactly_one_of_module_or_command() -> None:
    with pytest.raises(ValueError):
        ServiceSpec(name="bad")  # neither
    with pytest.raises(ValueError):
        ServiceSpec(name="bad", module="bus", command=["redis-server"])  # both
    # valid forms do not raise:
    ServiceSpec(name="ok-mod", module="bus")
    ServiceSpec(name="ok-cmd", command=["redis-server"])


async def test_await_ready_probes_until_true() -> None:
    # Deterministic unit test of the readiness gate — no subprocess race.
    from runner.supervisor import Supervisor, _ManagedService

    calls: list[int] = []

    async def ready() -> bool:
        calls.append(1)
        return len(calls) >= 2  # False on first probe, True on second

    sup = Supervisor([], reload=False)
    svc = _ManagedService(ServiceSpec(name="probe", command=["sleep", "1"], ready_check=ready))
    ok = await sup._await_ready(svc, timeout=5.0)
    assert ok is True
    assert len(calls) >= 2  # probed more than once before readiness


async def test_native_command_spawns_via_start_process() -> None:
    # Verifies the command branch of _start_process without driving run().
    from runner.supervisor import Supervisor, _ManagedService

    sup = Supervisor([], reload=False)
    svc = _ManagedService(ServiceSpec(name="probe", command=["true"]))
    await sup._start_process(svc)
    assert svc.process is not None
    await svc.process.wait()
    assert svc.process.returncode == 0
