"""Unified Alfred runner — starts all core services as supervised child processes.

Usage: python -m runner
"""

from __future__ import annotations

import asyncio
import sys

from runner.supervisor import ServiceSpec, Supervisor
from shared.logging import configure_logging

SERVICES = [
    ServiceSpec(name="bridge", module="bus"),
    ServiceSpec(name="reflex", module="core.reflex", delay=1.0),
    ServiceSpec(name="triggers", module="core.triggers"),
]


def main() -> None:
    log = configure_logging(service="runner")

    names = ", ".join(s.name for s in SERVICES)
    log.info("Alfred — starting %d services: %s", len(SERVICES), names)

    supervisor = Supervisor(SERVICES)
    code = asyncio.run(supervisor.run())
    sys.exit(code)


if __name__ == "__main__":
    main()
