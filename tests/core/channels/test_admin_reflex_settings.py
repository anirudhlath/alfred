"""The admin API's Reflex settings: a session-gated GET and a validating PUT."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import pytest
from fastapi.testclient import TestClient

from core.channels.web_server import create_app
from core.reflex.settings import ReflexSettings
from shared.streams import AUTH_SESSION_PREFIX, REFLEX_SETTINGS_KEY
from tests.core.channels.conftest import _TEST_SESSION_ID

URL = "/api/admin/reflex/settings"

# Every field set to something other than its default.
_FULL: dict[str, Any] = {
    "mode": "ask",
    "ask_tools": ["alpha_*", "beta_off"],
    "allowed_scripts": ["target.one"],
    "reverse_window_minutes": 15,
    "ask_expiry_seconds": 3600,
    "repeat_hours": 0.5,
    "daily_ask_cap": 0,
    "quiet_hours_start": "22:30",
    "quiet_hours_end": "22:30",
}


@pytest.fixture
def server() -> fakeredis.FakeServer:
    """One in-memory Redis, shared by the app (async) and the test (sync)."""
    shared = fakeredis.FakeServer()
    seed = fakeredis.FakeRedis(server=shared)
    seed.hset(
        f"{AUTH_SESSION_PREFIX}{_TEST_SESSION_ID}",
        mapping={"authenticated": "1", "credential_id": "test-cred"},
    )
    return shared


@pytest.fixture
def stored(server: fakeredis.FakeServer) -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(server=server)


def _client(server: fakeredis.FakeServer, *, authed: bool = True) -> TestClient:
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = fakeredis.FakeAsyncRedis(server=server)
    client = TestClient(app)
    if authed:
        client.cookies.set("alfred_auth", _TEST_SESSION_ID)
    return client


def test_get_returns_the_defaults_when_nothing_is_stored(server: fakeredis.FakeServer) -> None:
    resp = _client(server).get(URL)

    assert resp.status_code == 200
    assert resp.json() == ReflexSettings().model_dump()
    assert resp.json()["mode"] == "shadow"


def test_put_then_get_round_trips_the_whole_document(
    server: fakeredis.FakeServer, stored: fakeredis.FakeRedis
) -> None:
    client = _client(server)

    put = client.put(URL, json=_FULL)

    assert put.status_code == 200
    assert put.json() == _FULL
    assert json.loads(stored.get(REFLEX_SETTINGS_KEY)) == _FULL  # type: ignore[arg-type]
    assert client.get(URL).json() == _FULL


def test_put_fills_a_missing_field_with_its_default(
    server: fakeredis.FakeServer, stored: fakeredis.FakeRedis
) -> None:
    stored.set(REFLEX_SETTINGS_KEY, json.dumps(_FULL))

    resp = _client(server).put(URL, json={"mode": "ask"})

    assert resp.status_code == 200
    # A PUT replaces the document: what the body leaves out goes back to its default.
    assert resp.json() == ReflexSettings(mode="ask").model_dump()
    assert json.loads(stored.get(REFLEX_SETTINGS_KEY)) == resp.json()  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "act"},
        {"mode": 5},
        {"surprise": 1},
        {"ask_tools": "alpha_*"},
        {"ask_tools": [""]},
        {"allowed_scripts": [1]},
        {"ask_expiry_seconds": 59},
        {"repeat_hours": 169},
        {"daily_ask_cap": 51},
        {"quiet_hours_start": "24:00"},
        {"quiet_hours_end": "7am"},
    ],
)
def test_put_of_an_invalid_document_is_422_and_saves_nothing(
    server: fakeredis.FakeServer, stored: fakeredis.FakeRedis, body: dict[str, Any]
) -> None:
    stored.set(REFLEX_SETTINGS_KEY, json.dumps(_FULL))

    resp = _client(server).put(URL, json=body)

    assert resp.status_code == 422
    assert json.loads(stored.get(REFLEX_SETTINGS_KEY)) == _FULL  # type: ignore[arg-type]


def test_put_of_an_invalid_document_leaves_an_empty_store_empty(
    server: fakeredis.FakeServer, stored: fakeredis.FakeRedis
) -> None:
    assert _client(server).put(URL, json={"mode": "act"}).status_code == 422
    assert stored.get(REFLEX_SETTINGS_KEY) is None


def test_get_reads_an_unusable_stored_document_as_the_defaults(
    server: fakeredis.FakeServer, stored: fakeredis.FakeRedis
) -> None:
    stored.set(REFLEX_SETTINGS_KEY, "{not json")

    resp = _client(server).get(URL)

    assert resp.status_code == 200
    assert resp.json() == ReflexSettings().model_dump()


def test_both_routes_need_a_session(
    server: fakeredis.FakeServer, stored: fakeredis.FakeRedis
) -> None:
    client = _client(server, authed=False)

    assert client.get(URL).status_code == 401
    assert client.put(URL, json=_FULL).status_code == 401
    assert stored.get(REFLEX_SETTINGS_KEY) is None


def test_redis_failing_is_503_on_both_routes(server: fakeredis.FakeServer) -> None:
    client = _client(server)
    broken = AsyncMock()
    broken.hgetall = client.app.state.redis.hgetall  # type: ignore[attr-defined]
    broken.get = AsyncMock(side_effect=ConnectionError("down"))
    broken.set = AsyncMock(side_effect=ConnectionError("down"))
    client.app.state.redis = broken  # type: ignore[attr-defined]

    assert client.get(URL).status_code == 503
    assert client.put(URL, json=_FULL).status_code == 503
