"""Entry point for the web channel server.

Usage: python -m core.channels
"""

from __future__ import annotations

import uvicorn

# Import adapter modules to trigger @ChannelRegistry.register() decorators
import core.notifications.adapters.voice
import core.notifications.adapters.websocket  # noqa: F401
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
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    main()
