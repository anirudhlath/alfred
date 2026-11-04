"""httpx's request log names a push service's host, never the URL that is the credential."""

import logging
from collections.abc import Iterator

import httpx
import pytest

from core.notifications.http_logs import PushUrlFilter, redact_push_urls

WEB_PUSH = "https://web.push.apple.com/QGuQyavXutnMH8"
APNS = "https://api.push.apple.com/3/device/" + "ab" * 32
REQUEST_LINE = 'HTTP Request: %s %s "%s %d %s"'  # httpx's own format
ANSWER = '"HTTP/2 201 Created"'


@pytest.fixture(autouse=True)
def _no_filter_yet() -> Iterator[None]:
    """Each test starts, and leaves, httpx's logger without the filter."""

    def strip() -> None:
        httpx_logger = logging.getLogger("httpx")
        for found in [f for f in httpx_logger.filters if isinstance(f, PushUrlFilter)]:
            httpx_logger.removeFilter(found)

    strip()
    yield
    strip()


def _log_request(url: object) -> None:
    logging.getLogger("httpx").info(REQUEST_LINE, "POST", url, "HTTP/2", 201, "Created")


@pytest.mark.parametrize("url", [WEB_PUSH, httpx.URL(WEB_PUSH)])
def test_a_web_push_request_names_only_the_host(httpx_log_lines: list[str], url: object) -> None:
    redact_push_urls()
    _log_request(url)
    assert httpx_log_lines == [f"HTTP Request: POST https://web.push.apple.com/… {ANSWER}"]


def test_an_apns_request_keeps_its_device_token_out(httpx_log_lines: list[str]) -> None:
    redact_push_urls()
    _log_request(httpx.URL(APNS))
    assert httpx_log_lines == [f"HTTP Request: POST https://api.push.apple.com/… {ANSWER}"]


def test_a_url_already_in_the_message_is_shortened_too(httpx_log_lines: list[str]) -> None:
    redact_push_urls()
    logging.getLogger("httpx").info(f"retrying {WEB_PUSH} after a reset")
    assert httpx_log_lines == ["retrying https://web.push.apple.com/… after a reset"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/v1/chat/completions",
        "https://notpush.apple.com/QGuQyavXutnMH8",  # the match is on a label boundary
    ],
)
def test_every_other_url_is_logged_as_it_was(httpx_log_lines: list[str], url: str) -> None:
    redact_push_urls()
    _log_request(httpx.URL(url))
    assert httpx_log_lines == [f"HTTP Request: POST {url} {ANSWER}"]


def test_installs_one_filter_however_often_it_is_called() -> None:
    redact_push_urls()
    redact_push_urls()
    filters = logging.getLogger("httpx").filters
    assert sum(isinstance(f, PushUrlFilter) for f in filters) == 1


async def test_a_real_request_through_httpx_logs_no_endpoint(
    httpx_log_lines: list[str],
) -> None:
    redact_push_urls()
    transport = httpx.MockTransport(lambda request: httpx.Response(201))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post(WEB_PUSH)
    assert httpx_log_lines  # httpx logged the request
    assert all("QGuQyavXutnMH8" not in line for line in httpx_log_lines)
    assert any("https://web.push.apple.com/…" in line for line in httpx_log_lines)
