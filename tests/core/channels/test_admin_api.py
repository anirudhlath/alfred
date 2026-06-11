"""Tests for the admin API router: auth gating + overview."""

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from core.channels.web_server import create_app, require_trusted_network
from shared.streams import AUTH_SESSION_PREFIX

_SESSION = "admin-test-session"


def _aiter(items: list[Any]) -> AsyncIterator[Any]:
    async def gen() -> AsyncIterator[Any]:
        for item in items:
            yield item

    return gen()


def make_admin_client(mock_redis: AsyncMock, *, authed: bool = True) -> TestClient:
    """App with mocked redis; cookie optional to exercise the 401 path."""

    async def _fake_hgetall(key: str) -> dict[bytes, bytes]:
        if key == f"{AUTH_SESSION_PREFIX}{_SESSION}":
            return {b"authenticated": b"1"}
        return {}

    if mock_redis.hgetall._mock_side_effect is None:
        mock_redis.hgetall = AsyncMock(side_effect=_fake_hgetall)
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = mock_redis
    client = TestClient(app)
    if authed:
        client.cookies.set("alfred_auth", _SESSION)
    return client


def _overview_redis() -> AsyncMock:
    r = AsyncMock()
    r.ping = AsyncMock(return_value=True)
    r.get = AsyncMock(return_value=None)
    r.hlen = AsyncMock(return_value=0)
    r.llen = AsyncMock(return_value=0)
    r.scan_iter = MagicMock(return_value=_aiter([]))
    r.xinfo_stream = AsyncMock(side_effect=Exception("missing"))
    return r


def test_admin_requires_auth_cookie() -> None:
    client = make_admin_client(_overview_redis(), authed=False)
    resp = client.get("/api/admin/overview")
    assert resp.status_code == 401


def test_admin_requires_trusted_network() -> None:
    client = make_admin_client(_overview_redis())

    def _reject() -> None:
        raise HTTPException(status_code=403, detail="untrusted")

    client.app.dependency_overrides[require_trusted_network] = _reject  # type: ignore[attr-defined]
    resp = client.get("/api/admin/overview")
    assert resp.status_code == 403


def test_overview_shape() -> None:
    r = _overview_redis()
    r.get = AsyncMock(
        side_effect=lambda key: (
            json.dumps({"date": "2026-06-10", "spend_usd": 1.24, "cap_usd": 5.0}).encode()
            if key == "alfred:cost:daily"
            else None
        )
    )
    client = make_admin_client(r)
    # No httpx client on app.state → inference checks report False, not errors.
    resp = client.get("/api/admin/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["redis"]["connected"] is True
    assert data["cost"]["spend_usd"] == 1.24
    assert data["dnd"] == {"active": False}
    assert data["counts"] == {"sessions": 0, "devices": 0, "deferred": 0, "triggers": 0}
    assert data["streams"]["events"]["length"] == 0
    assert data["inference"] == {"ollama": False, "lmstudio": False}


def test_overview_reports_redis_down() -> None:
    r = _overview_redis()
    r.ping = AsyncMock(side_effect=ConnectionError("down"))
    client = make_admin_client(r)
    resp = client.get("/api/admin/overview")
    assert resp.status_code == 200
    assert resp.json()["redis"]["connected"] is False


def test_overview_inference_up_with_http_client() -> None:
    r = _overview_redis()
    client = make_admin_client(r)
    fake_http = AsyncMock()
    fake_http.get = AsyncMock(return_value=MagicMock(status_code=200))
    client.app.state.http = fake_http  # type: ignore[attr-defined]
    resp = client.get("/api/admin/overview")
    assert resp.status_code == 200
    assert resp.json()["inference"] == {"ollama": True, "lmstudio": True}


def test_streams_list() -> None:
    client = make_admin_client(_overview_redis())
    resp = client.get("/api/admin/streams")
    assert resp.status_code == 200
    assert "events" in resp.json()


def test_stream_history_unknown_name_404() -> None:
    client = make_admin_client(_overview_redis())
    assert client.get("/api/admin/streams/nope").status_code == 404


def test_stream_history_paginates() -> None:
    r = _overview_redis()
    r.xrevrange = AsyncMock(
        return_value=[
            (b"2-0", {b"event": b'{"event_type": "state_changed"}'}),
            (b"1-0", {b"event": b'{"event_type": "trigger_fired"}'}),
        ]
    )
    client = make_admin_client(r)
    resp = client.get("/api/admin/streams/events?count=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entries"][0] == {"id": "2-0", "event": {"event_type": "state_changed"}}
    assert body["next_before"] == "1-0"
    r.xrevrange.assert_awaited_once_with("alfred:events", max="+", min="-", count=2)


def test_stream_history_before_is_exclusive() -> None:
    r = _overview_redis()
    r.xrevrange = AsyncMock(return_value=[])
    client = make_admin_client(r)
    resp = client.get("/api/admin/streams/events?count=5&before=2-0")
    assert resp.status_code == 200
    assert resp.json() == {"entries": [], "next_before": None}
    r.xrevrange.assert_awaited_once_with("alfred:events", max="(2-0", min="-", count=5)


def test_stream_history_rejects_malformed_before() -> None:
    client = make_admin_client(_overview_redis())
    resp = client.get("/api/admin/streams/events?before=garbage")
    assert resp.status_code == 400


def test_stream_history_notification_payload_decode() -> None:
    r = _overview_redis()
    r.xrevrange = AsyncMock(
        return_value=[
            (b"3-0", {b"notification": b'{"notification_id": "n1", "title": "Hi"}'}),
        ]
    )
    client = make_admin_client(r)
    resp = client.get("/api/admin/streams/notifications?count=1")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert entries[0]["event"]["title"] == "Hi"
