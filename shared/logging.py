"""Centralized Loguru logging setup.

Replaces all logging.basicConfig() calls across Alfred entry points.
Call configure_logging() once at service startup.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    import types

    from loguru import Logger


class _InterceptHandler(logging.Handler):
    """Route stdlib logging through Loguru, preserving the bound service name."""

    _service: str = "unknown"

    def emit(self, record: logging.LogRecord) -> None:
        # Find caller from where the logged message originated
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame: types.FrameType | None = logging.currentframe()
        depth = 0
        while frame is not None and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.bind(service=self._service).opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def configure_logging(
    service: str,
    *,
    level: str | None = None,
    json_output: bool | None = None,
) -> Logger:
    """Configure Loguru as the sole logging backend.

    Args:
        service: Service name bound to all log records.
        level: Minimum log level (default from LOG_LEVEL env var, fallback INFO).
        json_output: If True, emit JSON-serialized logs (for production).
                     Defaults to True when LOG_FORMAT=json env var is set.

    Returns:
        A Loguru logger with service context bound.
    """
    resolved_level: str = level if level is not None else os.getenv("LOG_LEVEL", "INFO")
    resolved_json = (
        json_output if json_output is not None else os.getenv("LOG_FORMAT", "").lower() == "json"
    )

    # Remove default loguru handler
    logger.remove()

    # Set the service name as the global default for all loguru loggers in this process
    logger.configure(extra={"service": service})

    # Console sink
    if resolved_json:
        logger.add(sys.stderr, serialize=True, level=resolved_level)
    else:
        fmt = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
            "[<cyan>{extra[service]}</cyan>] "
            "<level>{level: <8}</level> "
            "<level>{message}</level>"
        )
        logger.add(sys.stderr, format=fmt, level=resolved_level, colorize=True)

    # Intercept stdlib logging with service name propagated
    handler = _InterceptHandler()
    handler._service = service
    logging.basicConfig(handlers=[handler], level=0, force=True)

    # Return logger with service context bound
    return logger.bind(service=service)
