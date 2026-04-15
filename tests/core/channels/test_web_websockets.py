"""Tests for WebSocket channel filtering (get_web_websockets vs get_active_websockets)."""

from unittest.mock import MagicMock

from core.channels.web_server import (
    _active_websockets,
    get_active_websockets,
    get_web_websockets,
)


def test_get_web_websockets_excludes_ios() -> None:
    """iOS WebSocket connections should be excluded from web-only list."""
    ws_web = MagicMock()
    ws_ios = MagicMock()
    ws_voice = MagicMock()

    _active_websockets.clear()
    _active_websockets[ws_web] = "web_pwa"
    _active_websockets[ws_ios] = "ios"
    _active_websockets[ws_voice] = "voice"

    try:
        web_only = get_web_websockets()
        all_ws = get_active_websockets()

        assert len(web_only) == 2
        assert ws_web in web_only
        assert ws_voice in web_only
        assert ws_ios not in web_only

        assert len(all_ws) == 3
        assert ws_ios in all_ws
    finally:
        _active_websockets.clear()


def test_get_web_websockets_empty_when_only_ios() -> None:
    """If only iOS clients connected, web list should be empty."""
    ws_ios = MagicMock()

    _active_websockets.clear()
    _active_websockets[ws_ios] = "ios"

    try:
        assert get_web_websockets() == []
        assert len(get_active_websockets()) == 1
    finally:
        _active_websockets.clear()
