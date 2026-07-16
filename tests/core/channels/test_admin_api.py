"""Tests for the admin API router: auth gating + overview."""

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from fastapi.testclient import TestClient

import core.channels.admin_api as admin_api
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
    data = resp.json()
    # Degraded path must return the FULL Overview shape (all keys) so the SPA's
    # required-field Overview type doesn't crash on a partial payload.
    assert data["redis"]["connected"] is False
    assert data["cost"] is None
    assert data["dnd"] == {"active": False}
    assert data["counts"] == {"sessions": 0, "devices": 0, "deferred": 0, "triggers": 0}
    assert data["streams"] == {}
    assert data["inference"] == {"ollama": False, "lmstudio": False}


def test_overview_survives_corrupt_cost_and_dnd() -> None:
    """One corrupt cost/dnd value must not 500 the whole dashboard overview."""
    r = _overview_redis()
    r.get = AsyncMock(return_value=b"{not valid json")
    client = make_admin_client(r)
    resp = client.get("/api/admin/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cost"] is None
    assert data["dnd"] == {"active": False}


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


def test_memory_episodic_recent_lists_hot_and_cold(tmp_path: Any, monkeypatch: Any) -> None:
    r = _overview_redis()
    r.scan_iter = MagicMock(return_value=_aiter([b"ctx:abc"]))

    # hgetall serves BOTH the auth middleware (session key) and the ctx hash —
    # route by key, otherwise the request 401s before reaching the endpoint.
    async def _hgetall(key: Any) -> dict[bytes, bytes]:
        key_str = key.decode() if isinstance(key, bytes) else key
        if key_str == f"{AUTH_SESSION_PREFIX}{_SESSION}":
            return {b"authenticated": b"1"}
        return {
            b"content": b"User asked about lights",
            b"type": b"episodic",
            b"source": b"conversation",
            b"timestamp": b"1718000000.0",
            b"significance": b"0.72",
            b"embedding_content": b"\x00\x01",
        }

    r.hgetall = AsyncMock(side_effect=_hgetall)
    import core.channels.admin_api as admin_api

    monkeypatch.setattr(admin_api, "_MEMORY_DIR", tmp_path)  # no cold DB present
    client = make_admin_client(r)
    resp = client.get("/api/admin/memory/episodic")
    assert resp.status_code == 200
    items = resp.json()["entries"]
    assert items[0]["store"] == "hot"
    assert items[0]["content"] == "User asked about lights"
    assert "embedding_content" not in items[0]


def test_memory_semantic_lists_markdown(tmp_path: Any, monkeypatch: Any) -> None:
    import core.channels.admin_api as admin_api

    prefs = tmp_path / "preferences"
    prefs.mkdir()
    (prefs / "lighting.md").write_text("# Lighting\n- warm")
    (tmp_path / "profile").mkdir()
    monkeypatch.setattr(admin_api, "_MEMORY_DIR", tmp_path)
    client = make_admin_client(_overview_redis())
    resp = client.get("/api/admin/memory/semantic")
    assert resp.status_code == 200
    files = resp.json()["files"]
    assert files == [
        {
            "name": "lighting.md",
            "dir": "preferences",
            "content": "# Lighting\n- warm",
            "modified": files[0]["modified"],
        },
    ]


def test_memory_scratchpad(tmp_path: Any, monkeypatch: Any) -> None:
    import core.channels.admin_api as admin_api

    (tmp_path / "scratchpad.md").write_text("obs 1\n")
    monkeypatch.setattr(admin_api, "_MEMORY_DIR", tmp_path)
    r = _overview_redis()
    r.llen = AsyncMock(return_value=3)
    client = make_admin_client(r)
    resp = client.get("/api/admin/memory/scratchpad")
    assert resp.json() == {"content": "obs 1\n", "pending_queue": 3}


def test_memory_routines_empty_dir(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(admin_api, "_MEMORY_DIR", tmp_path)
    client = make_admin_client(_overview_redis())
    resp = client.get("/api/admin/memory/routines")
    assert resp.status_code == 200
    assert resp.json() == {"routines": []}


def test_memory_episodic_hot_scan_filters_out_non_episodic(tmp_path: Any, monkeypatch: Any) -> None:
    """Hot scan must return only type=episodic entries, skipping routine/semantic."""
    r = _overview_redis()
    # Two ctx keys: one episodic, one routine
    r.scan_iter = MagicMock(return_value=_aiter([b"ctx:episodic1", b"ctx:routine1"]))

    async def _hgetall(key: Any) -> dict[bytes, bytes]:
        key_str = key.decode() if isinstance(key, bytes) else key
        if key_str == f"{AUTH_SESSION_PREFIX}{_SESSION}":
            return {b"authenticated": b"1"}
        if b"episodic1" in (key if isinstance(key, bytes) else key.encode()):
            return {
                b"content": b"User asked about lights",
                b"type": b"episodic",
                b"timestamp": b"0",
            }
        # routine entry
        return {b"content": b"Morning routine", b"type": b"routine", b"timestamp": b"0"}

    r.hgetall = AsyncMock(side_effect=_hgetall)
    monkeypatch.setattr(admin_api, "_MEMORY_DIR", tmp_path)
    client = make_admin_client(r)
    resp = client.get("/api/admin/memory/episodic")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["content"] == "User asked about lights"
    assert entries[0]["type"] == "episodic"


def test_memory_episodic_search_returns_503_when_memory_unavailable(
    monkeypatch: Any,
) -> None:
    """GET /memory/episodic?q=... → 503 when _get_episodic_lazy returns None."""
    monkeypatch.setattr(admin_api, "_get_episodic_lazy", lambda r: None)
    client = make_admin_client(_overview_redis())
    resp = client.get("/api/admin/memory/episodic?q=lights")
    assert resp.status_code == 503


def test_memory_episodic_search_success_and_no_stat_mutation(
    monkeypatch: Any,
) -> None:
    """GET /memory/episodic?q=... returns correct shape and calls recall with update_stats=False."""

    class _FakeEntry:
        def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
            return {"id": "e1", "summary": "lights on", "source": "conversation"}

    class _FakeResult:
        source_store = "hot"
        score = 0.9
        entry = _FakeEntry()

    fake_recall = AsyncMock(return_value=[_FakeResult()])

    class _FakeMemory:
        recall = fake_recall

    monkeypatch.setattr(admin_api, "_get_episodic_lazy", lambda r: _FakeMemory())
    client = make_admin_client(_overview_redis())
    resp = client.get("/api/admin/memory/episodic?q=lights")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "entries": [
            {
                "store": "hot",
                "score": 0.9,
                "id": "e1",
                "summary": "lights on",
                "source": "conversation",
            }
        ]
    }
    fake_recall.assert_awaited_once()
    _, kwargs = fake_recall.call_args
    assert kwargs.get("update_stats") is False


def test_triggers_list() -> None:
    r = _overview_redis()

    async def _hgetall(key: str) -> dict[bytes, bytes]:
        if key == f"{AUTH_SESSION_PREFIX}{_SESSION}":
            return {b"authenticated": b"1"}
        return {
            b"t1": json.dumps(
                {
                    "trigger_id": "t1",
                    "name": "sunset",
                    "trigger_type": "time",
                    "enabled": True,
                    "created_at": "2026-06-01T00:00:00+00:00",
                }
            ).encode()
        }

    r.hgetall = AsyncMock(side_effect=_hgetall)
    client = make_admin_client(r)
    resp = client.get("/api/admin/triggers")
    assert resp.status_code == 200
    assert resp.json()["triggers"][0]["name"] == "sunset"


def test_deferred_notifications() -> None:
    r = _overview_redis()
    r.lrange = AsyncMock(
        return_value=[json.dumps({"notification_id": "n1", "title": "Hi"}).encode()]
    )
    client = make_admin_client(r)
    resp = client.get("/api/admin/notifications/deferred")
    assert resp.json()["notifications"][0]["title"] == "Hi"


def test_sessions_list() -> None:
    r = _overview_redis()
    r.scan_iter = MagicMock(return_value=_aiter([b"alfred:sessions:s1"]))
    r.ttl = AsyncMock(return_value=1200)
    client = make_admin_client(r)
    resp = client.get("/api/admin/sessions")
    body = resp.json()["sessions"]
    assert body[0]["session_id"] == "s1"
    assert body[0]["ttl_seconds"] == 1200


def test_sessions_list_populated() -> None:
    """Sessions list with a real session hash — channel, turns, created_at, and ttl populated."""
    r = _overview_redis()
    r.scan_iter = MagicMock(return_value=_aiter([b"alfred:sessions:s2"]))

    async def _hgetall(key: str) -> dict[bytes, bytes]:
        if key == f"{AUTH_SESSION_PREFIX}{_SESSION}":
            return {b"authenticated": b"1"}
        # Session hash for s2
        return {
            b"channel": b"web_pwa",
            b"history": json.dumps(
                [{"role": "user"}, {"role": "assistant"}, {"role": "user"}]
            ).encode(),
            b"created_at": b"2026-06-10T10:00:00+00:00",
        }

    r.hgetall = AsyncMock(side_effect=_hgetall)
    r.ttl = AsyncMock(return_value=900)
    client = make_admin_client(r)
    resp = client.get("/api/admin/sessions")
    assert resp.status_code == 200
    body = resp.json()["sessions"]
    assert len(body) == 1
    entry = body[0]
    assert entry["session_id"] == "s2"
    assert entry["channel"] == "web_pwa"
    assert entry["turns"] == 3
    assert entry["created_at"] == "2026-06-10T10:00:00+00:00"
    assert entry["ttl_seconds"] == 900


def test_devices_list() -> None:
    r = _overview_redis()

    async def _hgetall(key: str) -> dict[bytes, bytes]:
        if key == f"{AUTH_SESSION_PREFIX}{_SESSION}":
            return {b"authenticated": b"1"}
        return {b"tok1": json.dumps({"platform": "ios", "identity": "sir"}).encode()}

    r.hgetall = AsyncMock(side_effect=_hgetall)
    client = make_admin_client(r)
    resp = client.get("/api/admin/devices")
    assert resp.json()["devices"][0]["platform"] == "ios"


# ---------------------------------------------------------------------------
# Controls — Task 7
# ---------------------------------------------------------------------------


def test_dnd_set_and_clear() -> None:
    r = _overview_redis()
    r.set = AsyncMock()
    r.delete = AsyncMock()
    client = make_admin_client(r)

    resp = client.post("/api/admin/dnd", json={"active": True, "reason": "focus"})
    assert resp.status_code == 200
    key, payload = r.set.await_args.args
    assert key == "alfred:memory:dnd"
    assert json.loads(payload) == {
        "active": True,
        "until": None,
        "reason": "focus",
        "source": "manual",
    }

    resp = client.post("/api/admin/dnd", json={"active": False})
    assert resp.json() == {"active": False}
    r.delete.assert_awaited_once_with("alfred:memory:dnd")


def test_drain_and_librarian_publish_actions() -> None:
    r = _overview_redis()
    r.xadd = AsyncMock()
    client = make_admin_client(r)

    assert client.post("/api/admin/notifications/drain").json() == {"status": "queued"}
    assert client.post("/api/admin/librarian/run").json() == {"status": "queued"}

    calls = r.xadd.await_args_list
    assert calls[0].args[0] == "alfred:actions"
    drained = json.loads(calls[0].args[1]["event"])
    assert drained["tool_name"] == "drain_deferred_notifications"
    assert drained["target_service"] == "conscious-engine"
    librarian = json.loads(calls[1].args[1]["event"])
    assert librarian["tool_name"] == "run_librarian"


def test_trigger_enable_publishes_action_to_triggers_process() -> None:
    """Enable toggle validates via hget, then publishes to ACTIONS_STREAM (no hset)."""
    trigger = {"trigger_id": "t1", "name": "sunset", "trigger_type": "time", "enabled": True}
    r = _overview_redis()
    r.hget = AsyncMock(return_value=json.dumps(trigger).encode())
    r.hset = AsyncMock()
    r.xadd = AsyncMock()
    client = make_admin_client(r)
    resp = client.post("/api/admin/triggers/t1/enabled", json={"enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "status": "queued",
        "trigger_id": "t1",
        "enabled": False,
        "effective_within_seconds": 60,
    }
    # No direct hash write — the triggers process owns the mutation.
    r.hset.assert_not_awaited()
    stream, payload = r.xadd.await_args.args
    assert stream == "alfred:actions"
    action = json.loads(payload["event"])
    assert action["tool_name"] == "set_trigger_enabled"
    assert action["target_service"] == "trigger-engine"
    assert action["source"] == "admin-api"
    assert action["parameters"] == {"trigger_id": "t1", "enabled": False}


def test_trigger_fire_publishes_action_to_triggers_process() -> None:
    """Fire validates via hget, then publishes fire_trigger to ACTIONS_STREAM."""
    trigger = {
        "trigger_id": "t1",
        "name": "sunset",
        "trigger_type": "time",
        "enabled": True,
        "one_shot": False,
        "action": {
            "tool_name": "dim_lights",
            "target_service": "home-service",
            "parameters": {"level": 30},
        },
    }
    r = _overview_redis()
    r.hget = AsyncMock(return_value=json.dumps(trigger).encode())
    r.hset = AsyncMock()
    r.hdel = AsyncMock()
    r.xadd = AsyncMock()
    r.lpush = AsyncMock()
    client = make_admin_client(r)
    resp = client.post("/api/admin/triggers/t1/fire")
    assert resp.json() == {"status": "queued", "trigger_id": "t1"}
    # No hand-mirrored fire logic in the channels process anymore.
    r.hset.assert_not_awaited()
    r.hdel.assert_not_awaited()
    r.lpush.assert_not_awaited()
    stream, payload = r.xadd.await_args.args
    assert stream == "alfred:actions"
    action = json.loads(payload["event"])
    assert action["tool_name"] == "fire_trigger"
    assert action["target_service"] == "trigger-engine"
    assert action["source"] == "admin-api"
    assert action["parameters"] == {"trigger_id": "t1"}


def test_trigger_fire_unknown_404() -> None:
    r = _overview_redis()
    r.hget = AsyncMock(return_value=None)
    client = make_admin_client(r)
    assert client.post("/api/admin/triggers/nope/fire").status_code == 404


def test_session_delete() -> None:
    r = _overview_redis()
    r.delete = AsyncMock(return_value=1)
    client = make_admin_client(r)
    resp = client.delete("/api/admin/sessions/s1")
    assert resp.json() == {"deleted": True}
    r.delete.assert_awaited_once_with("alfred:sessions:s1")


# ---------------------------------------------------------------------------
# New tests (Fix items 1-5)
# ---------------------------------------------------------------------------


def test_trigger_fire_one_shot_does_not_mutate_hash_in_channels() -> None:
    """One-shot deletion is now owned by the triggers process — channels only queues.

    The channels process must NOT touch the hash (no HDEL / HSET); it publishes
    a fire_trigger action and the triggers process handles one-shot deletion via
    TriggerStore.delete (YAML-consistent).
    """
    trigger = {
        "trigger_id": "t1",
        "name": "flash-sale",
        "trigger_type": "time",
        "enabled": True,
        "one_shot": True,
        "action": {
            "tool_name": "send_alert",
            "target_service": "conscious-engine",
            "parameters": {},
        },
    }
    r = _overview_redis()
    r.hget = AsyncMock(return_value=json.dumps(trigger).encode())
    r.hset = AsyncMock()
    r.hdel = AsyncMock()
    r.xadd = AsyncMock()
    r.lpush = AsyncMock()
    client = make_admin_client(r)
    resp = client.post("/api/admin/triggers/t1/fire")
    assert resp.status_code == 200
    assert resp.json() == {"status": "queued", "trigger_id": "t1"}
    r.hdel.assert_not_awaited()
    r.hset.assert_not_awaited()
    stream, payload = r.xadd.await_args.args
    assert stream == "alfred:actions"
    assert json.loads(payload["event"])["tool_name"] == "fire_trigger"


def test_dnd_set_with_until_stores_isoformat() -> None:
    """POST /dnd with 'until' stores the correct ISO-8601 string in Redis."""
    r = _overview_redis()
    r.set = AsyncMock()
    client = make_admin_client(r)

    resp = client.post(
        "/api/admin/dnd",
        json={"active": True, "until": "2026-06-12T08:00:00+00:00"},
    )
    assert resp.status_code == 200
    _key, payload = r.set.await_args.args
    stored = json.loads(payload)
    assert stored["until"] == "2026-06-12T08:00:00+00:00"
    assert stored["active"] is True


def test_set_trigger_enabled_404_when_missing() -> None:
    """POST .../enabled with an unknown trigger_id → 404."""
    r = _overview_redis()
    r.hget = AsyncMock(return_value=None)
    client = make_admin_client(r)
    resp = client.post("/api/admin/triggers/ghost/enabled", json={"enabled": False})
    assert resp.status_code == 404


def test_fire_trigger_corrupt_json_returns_500() -> None:
    """Corrupt stored JSON in fire_trigger → 500 with 'corrupt' in detail."""
    r = _overview_redis()
    r.hget = AsyncMock(return_value=b"not-json{{")
    client = make_admin_client(r)
    resp = client.post("/api/admin/triggers/t1/fire")
    assert resp.status_code == 500
    assert "corrupt" in resp.json()["detail"].lower()


def test_set_trigger_enabled_corrupt_json_returns_500() -> None:
    """Corrupt stored JSON in set_trigger_enabled → 500 with 'corrupt' in detail."""
    r = _overview_redis()
    r.hget = AsyncMock(return_value=b"not-json{{")
    client = make_admin_client(r)
    resp = client.post("/api/admin/triggers/t1/enabled", json={"enabled": True})
    assert resp.status_code == 500
    assert "corrupt" in resp.json()["detail"].lower()
