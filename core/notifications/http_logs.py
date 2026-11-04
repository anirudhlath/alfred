"""Keep push credentials out of httpx's request log.

httpx logs every request it sends at INFO (``HTTP Request: POST <url> "HTTP/2 201 Created"``),
and a push request's URL is a credential: a Web Push endpoint is the whole URL, and an APNs
device token is the last segment of its path. ``redact_push_urls()`` puts a filter on httpx's
logger that shortens every URL on a push service (``PUSH_SERVICE_HOSTS``, whose
``push.apple.com`` covers APNs's hosts too) to its scheme and host, so the line still says a
push was sent and how the service answered. Both push adapters install it when they are built.
"""

from __future__ import annotations

import logging
import re
from typing import Final
from urllib.parse import urlsplit

from core.notifications.devices import is_push_service

HTTPX_LOGGER: Final = "httpx"
# What a push URL's path, query and fragment become in a log line.
REDACTED_PATH: Final = "/…"

_URL: Final = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.IGNORECASE)


def _shorten(match: re.Match[str]) -> str:
    url = match.group(0)
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:  # urlsplit refuses a malformed IPv6 host
        return url
    if host is None or not is_push_service(host):
        return url
    return f"{parts.scheme}://{host}{REDACTED_PATH}"


def redact(text: str) -> str:
    """``text`` with each push-service URL in it cut to its scheme and host."""
    return _URL.sub(_shorten, text)


def _redacted_arg(arg: object) -> object:
    if isinstance(arg, str):
        return redact(arg)
    if arg is None or isinstance(arg, int | float):  # bool is an int
        return arg
    # httpx passes the request's ``httpx.URL``, which formats with %s like its str().
    text = str(arg)
    shortened = redact(text)
    return arg if shortened == text else shortened


class PushUrlFilter(logging.Filter):
    """Cuts every push-service URL in a record, its message or its arguments, to the host."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redacted_arg(arg) for arg in record.args)
        return True


def redact_push_urls() -> None:
    """Put ``PushUrlFilter`` on httpx's logger; a second call adds nothing."""
    httpx_logger = logging.getLogger(HTTPX_LOGGER)
    if not any(isinstance(found, PushUrlFilter) for found in httpx_logger.filters):
        httpx_logger.addFilter(PushUrlFilter())
