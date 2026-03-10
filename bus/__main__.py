"""Entry point for the MQTT ↔ Redis bridge service."""

import asyncio
import logging
import os

from bus.bridge import run_bridge

logging.basicConfig(level=logging.INFO)


def main() -> None:
    asyncio.run(
        run_bridge(
            redis_url=(
                f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}"
            ),
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        )
    )


if __name__ == "__main__":
    main()
