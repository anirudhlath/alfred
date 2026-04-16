"""Unified Alfred runner — starts all core services as supervised child processes.

Usage: python -m runner [--no-reload] [--debug]

Hot-reload is enabled by default: source file changes automatically
restart the affected service.  Pass ``--no-reload`` to disable.
Pass ``--debug`` to enable verbose LiteLLM logging.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from runner.supervisor import ServiceSpec, Supervisor
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.otel import init_tracing

SERVICES = [
    ServiceSpec(name="bridge", module="bus"),
    ServiceSpec(name="reflex", module="core.reflex", delay=1.0),
    ServiceSpec(name="triggers", module="core.triggers"),
    ServiceSpec(
        name="conscious",
        module="core.conscious",
        delay=2.0,
        watch_dirs=["core/conscious/prompts"],
    ),
    ServiceSpec(
        name="channels",
        module="core.channels",
        delay=2.0,
        watch_dirs=["core/voice", "core/conscious/prompts"],
    ),
    ServiceSpec(name="memory-ingestor", module="core.memory.ingestor_main", delay=1.5),
]


def main() -> None:
    reload = "--no-reload" not in sys.argv
    if "--debug" in sys.argv:
        os.environ["ALFRED_DEBUG"] = "1"
    log = configure_logging(service="runner")
    config = AlfredConfig.from_env()
    init_tracing(
        service_name="runner",
        endpoint=config.otel_endpoint if config.signoz_enabled else None,
    )

    names = ", ".join(s.name for s in SERVICES)
    mode = "reload" if reload else "static"
    log.info("Alfred — starting {} services ({}): {}", len(SERVICES), mode, names)

    supervisor = Supervisor(SERVICES, reload=reload, root=Path.cwd())
    code = asyncio.run(supervisor.run())
    sys.exit(code)


if __name__ == "__main__":
    main()
