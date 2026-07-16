"""POST /api/voice/enroll."""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from core.channels.satellite.audio import pcm_to_wav
from core.channels.web_server import create_app


def _sample() -> str:
    wav = pcm_to_wav(b"\x00\x01" * 16000)  # 1s of noise-ish PCM
    return "data:audio/wav;base64," + base64.b64encode(wav).decode()


def test_enroll_happy_path(web_client: TestClient) -> None:
    speaker_id = AsyncMock()
    speaker_id.enroll = AsyncMock(return_value=True)
    with (
        patch("core.channels.web_server.aget_speaker_id", AsyncMock(return_value=speaker_id)),
        patch("core.voice.audio.decode_to_pcm16k", return_value=b"\x00\x00" * 16000),
    ):
        resp = web_client.post(
            "/api/voice/enroll",
            json={"identity": "sir", "samples": [_sample(), _sample(), _sample()]},
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "enrolled", "identity": "sir"}
    pcm_lists: list[Any] = speaker_id.enroll.call_args.args[1]
    assert len(pcm_lists) == 3


def test_enroll_unavailable_without_voice_extra(web_client: TestClient) -> None:
    with patch("core.channels.web_server.aget_speaker_id", AsyncMock(return_value=None)):
        resp = web_client.post(
            "/api/voice/enroll", json={"identity": "sir", "samples": [_sample()] * 3}
        )
    assert resp.status_code == 503


def test_enroll_validates_identity(web_client: TestClient) -> None:
    resp = web_client.post(
        "/api/voice/enroll", json={"identity": "Bad Name!", "samples": [_sample()] * 3}
    )
    assert resp.status_code == 422


def test_enroll_rejects_unauthenticated() -> None:
    """Gated the same way as admin routes: trusted network alone is not enough.

    ``TestClient`` hits the app over the "testclient" pseudo-host, which
    ``require_trusted_network`` allows — so without the ``alfred_auth`` cookie
    this exercises the authentication gate specifically, not the network one.
    """
    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value={})

    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = mock_redis
    client = TestClient(app)  # no alfred_auth cookie set

    resp = client.post("/api/voice/enroll", json={"identity": "sir", "samples": [_sample()] * 3})
    assert resp.status_code == 401
