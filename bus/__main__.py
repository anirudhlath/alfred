"""Entry point for the MQTT ↔ Redis bridge service."""

import asyncio

from bus.bridge import run_bridge
from shared.config import AlfredConfig
from shared.logging import configure_logging


def main() -> None:
    configure_logging(service="bridge")
    cfg = AlfredConfig.from_env()
    asyncio.run(
        run_bridge(
            redis_url=cfg.redis_url,
            mqtt_host=cfg.mqtt_host,
            mqtt_port=cfg.mqtt_port,
        )
    )


if __name__ == "__main__":
    main()
