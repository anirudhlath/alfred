"""Entry point for the web channel server.

Usage: python -m core.channels
"""

from __future__ import annotations

import logging
import os
import time

import uvicorn

from core.channels.web_server import create_app, get_active_websockets
from core.notifications.adapters.voice import VoiceChannelAdapter
from core.notifications.adapters.websocket import WebSocketChannelAdapter
from core.notifications.channels import ChannelRegistry
from shared.config import AlfredConfig
from shared.logging import configure_logging


def _get_tts_lazy() -> object:
    """Lazy TTS getter matching web_server pattern."""
    from core.channels.web_server import _get_tts

    return _get_tts()


def main() -> None:
    configure_logging(service="web-channel")
    config = AlfredConfig.from_env()

    # Wire channel adapters with WebSocket session access
    ChannelRegistry.set_instance(
        "websocket",
        WebSocketChannelAdapter(get_sessions=get_active_websockets),
    )
    ChannelRegistry.set_instance(
        "voice",
        VoiceChannelAdapter(get_tts=_get_tts_lazy, get_sessions=get_active_websockets),
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
                logging.getLogger(__name__).warning(
                    "Port %d in use, retrying in %ds...", port, wait
                )
                time.sleep(wait)
            else:
                raise


if __name__ == "__main__":
    main()
