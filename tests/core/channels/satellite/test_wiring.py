"""Satellite bridge lifespan wiring."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.channels.web_server import create_app
from core.notifications.channels import ChannelRegistry


@pytest.fixture(autouse=True)
def _restore_channel_registry() -> Iterator[None]:
    """ChannelRegistry is process-global — snapshot/restore instances so this
    module's satellite registration never leaks into other test modules."""
    snapshot = dict(ChannelRegistry._instances)
    yield
    ChannelRegistry._instances.clear()
    ChannelRegistry._instances.update(snapshot)


def test_bridge_started_when_satellites_configured(tmp_path: Path) -> None:
    cfg = tmp_path / "satellites.yaml"
    cfg.write_text("satellites:\n  - name: kitchen\n    host: 127.0.0.1\n    area: Kitchen\n")

    with (
        patch.dict("os.environ", {"SATELLITES_CONFIG": str(cfg)}),
        patch("core.channels.web_server.SatelliteBridge") as bridge_cls,
        # The lifespan's background warmup task calls the real _aget_stt/_aget_tts,
        # which serialize on a module-level asyncio.Lock in voice_models.py that
        # binds to whichever event loop first acquires it — leaking that bind
        # here would break unrelated tests (e.g. test_voice_async.py) that expect
        # a fresh, unbound lock on their own event loop. Stub the warmup calls so
        # this wiring test doesn't touch that shared lock (or spend real time
        # loading STT/TTS models neither test exercises).
        patch("core.channels.web_server._aget_stt", new=AsyncMock(return_value=None)),
        patch("core.channels.web_server._aget_tts", new=AsyncMock(return_value=None)),
    ):
        bridge_cls.return_value.stop = AsyncMock()
        app = create_app(redis_url="redis://localhost:6379")
        with TestClient(app):
            bridge_cls.assert_called_once()
            entries = bridge_cls.call_args.args[0]
            assert entries[0].name == "kitchen"
            bridge_cls.return_value.start.assert_called_once()
            assert ChannelRegistry.get_instance("satellite") is not None
        bridge_cls.return_value.stop.assert_awaited_once()


def test_no_bridge_without_config(tmp_path: Path) -> None:
    with (
        patch.dict("os.environ", {"SATELLITES_CONFIG": str(tmp_path / "missing.yaml")}),
        patch("core.channels.web_server.SatelliteBridge") as bridge_cls,
        # See comment in test_bridge_started_when_satellites_configured above.
        patch("core.channels.web_server._aget_stt", new=AsyncMock(return_value=None)),
        patch("core.channels.web_server._aget_tts", new=AsyncMock(return_value=None)),
    ):
        app = create_app(redis_url="redis://localhost:6379")
        with TestClient(app):
            bridge_cls.assert_not_called()
