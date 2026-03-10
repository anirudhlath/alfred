"""Entry point for the MQTT ↔ Redis bridge service."""

import asyncio
import logging

from bus.bridge import run_bridge
from shared.config import AlfredConfig

logging.basicConfig(level=logging.INFO)


def main() -> None:
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
