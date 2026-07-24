"""Entry point for the web channel server.

Usage: python -m core.channels
"""

from __future__ import annotations

import os
import time

import uvicorn
from loguru import logger

from core.channels.voice_models import aget_tts
from core.channels.web_server import create_app, get_web_websockets
from core.notifications.adapters.websocket import WebSocketChannelAdapter
from core.notifications.channels import ChannelRegistry
from shared.config import AlfredConfig
from shared.logging import configure_logging


def main() -> None:
    configure_logging(service="web-channel")
    config = AlfredConfig.from_env()

    # Wire channel adapters — only push to web/PWA clients.
    # iOS receives notifications via APNs; notification_id dedup is a safety net.
    # WebSocket adapter handles both text and TTS audio (URGENT only).
    ChannelRegistry.set_instance(
        "websocket",
        WebSocketChannelAdapter(get_sessions=get_web_websockets, aget_tts=aget_tts),
    )

    app = create_app(redis_url=config.redis_url)
    port = int(os.getenv("CHANNELS_PORT", "8081"))
    for attempt in range(5):
        try:
            uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
            break
        except OSError as e:
            if e.errno == 48 and attempt < 4:
                wait = attempt + 1
                logger.warning("Port {} in use, retrying in {}s...", port, wait)
                time.sleep(wait)
            else:
                raise


if __name__ == "__main__":
    main()
