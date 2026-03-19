"""Entry point for the web channel server.

Usage: python -m core.channels
"""

from __future__ import annotations

import uvicorn

from core.channels.web_server import create_app
from shared.config import AlfredConfig
from shared.logging import configure_logging


def main() -> None:
    configure_logging(service="web-channel")
    config = AlfredConfig.from_env()
    app = create_app(redis_url=config.redis_url)
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    main()
