"""Unified Alfred runner — starts all core services as supervised child processes.

Usage: python -m runner
"""

from __future__ import annotations

import asyncio
import logging
import sys

from runner.supervisor import ServiceSpec, Supervisor

SERVICES = [
    ServiceSpec(name="bridge", module="bus"),
    ServiceSpec(name="reflex", module="core.reflex", delay=1.0),
    ServiceSpec(name="triggers", module="core.triggers"),
]


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    log = logging.getLogger("runner")

    names = ", ".join(s.name for s in SERVICES)
    log.info("Alfred — starting %d services: %s", len(SERVICES), names)

    supervisor = Supervisor(SERVICES)
    code = asyncio.run(supervisor.run())
    sys.exit(code)


if __name__ == "__main__":
    main()
