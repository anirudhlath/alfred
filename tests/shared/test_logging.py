"""Tests for centralized Loguru logging setup."""

from __future__ import annotations

import sys
from unittest.mock import patch

from shared.logging import configure_logging


def test_configure_logging_returns_logger() -> None:
    """configure_logging() returns a loguru logger with bind context."""
    log = configure_logging(service="test-svc")
    assert hasattr(log, "info")
    assert hasattr(log, "bind")


def test_configure_logging_intercepts_stdlib(capsys: object) -> None:
    """stdlib logging calls are intercepted by loguru after configure_logging()."""
    import logging

    configure_logging(service="test-svc")
    # stdlib logger should now route through loguru's InterceptHandler
    stdlib_logger = logging.getLogger("test.stdlib")
    # Should not raise
    stdlib_logger.info("hello from stdlib")


def test_configure_logging_adds_service_context() -> None:
    """Logger returned by configure_logging has service name bound."""
    log = configure_logging(service="my-service")
    # The bound extra should contain service
    # We verify by checking the record produced
    records: list[dict[str, object]] = []

    def sink(message: object) -> None:
        records.append({"text": str(message)})

    from loguru import logger
    logger.add(sink, format="{extra[service]} | {message}", filter=lambda r: "service" in r["extra"])
    log.info("test message")

    assert len(records) >= 1
    assert "my-service" in str(records[-1]["text"])
