"""Entry point for the MQTT ↔ Redis bridge service."""

import asyncio

from bus.bridge import run_bridge
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.otel import init_tracing


def main() -> None:
    configure_logging(service="bridge")
    cfg = AlfredConfig.from_env()
    init_tracing(
        service_name="bridge",
        endpoint=cfg.otel_endpoint if cfg.signoz_enabled else None,
    )
    asyncio.run(
        run_bridge(
            redis_url=cfg.redis_url,
            mqtt_host=cfg.mqtt_host,
            mqtt_port=cfg.mqtt_port,
        )
    )


if __name__ == "__main__":
    main()
