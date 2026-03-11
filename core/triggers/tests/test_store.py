"""Tests for TriggerStore — Redis CRUD + YAML snapshot/rehydration."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from core.triggers.registry import TriggerRegistry
from core.triggers.store import TriggerStore


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


def _make_trigger_dict(trigger_id: str = "t-1", trigger_type: str = "time") -> dict[str, Any]:
    return {
        "trigger_id": trigger_id,
        "trigger_type": trigger_type,
        "name": "test trigger",
        "enabled": True,
        "one_shot": False,
        "created_by": "test",
        "created_at": datetime.now(UTC).isoformat(),
        "last_fired": None,
        "action": None,
        "conditions": {"cron": "0 7 * * *"} if trigger_type == "time" else {"entity_id": "light.x"},
    }


@pytest.fixture
def mock_redis() -> AsyncMock:
    r = AsyncMock()
    r.hgetall = AsyncMock(return_value={})
    r.hset = AsyncMock()
    r.hdel = AsyncMock()
    return r


@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    d = tmp_path / "triggers"
    d.mkdir()
    return d


@pytest.mark.asyncio
async def test_save_writes_to_redis_and_yaml(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    cls = TriggerRegistry.get("time")
    trigger = cls(**_make_trigger_dict())

    await store.save(trigger)

    mock_redis.hset.assert_called_once()
    yaml_file = snapshot_dir / "t-1.yaml"
    assert yaml_file.exists()


@pytest.mark.asyncio
async def test_delete_removes_from_redis_and_yaml(
    mock_redis: AsyncMock, snapshot_dir: Path
) -> None:
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)
    (snapshot_dir / "t-1.yaml").write_text("test")

    await store.delete("t-1")

    mock_redis.hdel.assert_called_once()
    assert not (snapshot_dir / "t-1.yaml").exists()


@pytest.mark.asyncio
async def test_load_from_redis(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    data = _make_trigger_dict()
    mock_redis.hgetall = AsyncMock(return_value={"t-1": json.dumps(data)})
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)

    triggers = await store.load()

    assert len(triggers) == 1
    assert triggers[0].trigger_id == "t-1"


@pytest.mark.asyncio
async def test_load_falls_back_to_disk(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    mock_redis.hgetall = AsyncMock(return_value={})
    data = _make_trigger_dict()
    (snapshot_dir / "t-1.yaml").write_text(yaml.dump(data))
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)

    triggers = await store.load()

    assert len(triggers) == 1
    assert triggers[0].trigger_id == "t-1"
    mock_redis.hset.assert_called()


def test_rehydrate_from_disk(snapshot_dir: Path) -> None:
    data = _make_trigger_dict()
    (snapshot_dir / "t-1.yaml").write_text(yaml.dump(data))

    triggers = TriggerStore.rehydrate_from_disk_static(snapshot_dir, TriggerRegistry)
    assert len(triggers) == 1


@pytest.mark.asyncio
async def test_list_all(mock_redis: AsyncMock, snapshot_dir: Path) -> None:
    d1 = _make_trigger_dict("t-1")
    d2 = _make_trigger_dict("t-2")
    d2["enabled"] = False
    mock_redis.hgetall = AsyncMock(
        return_value={
            "t-1": json.dumps(d1),
            "t-2": json.dumps(d2),
        }
    )
    store = TriggerStore(redis=mock_redis, snapshot_dir=snapshot_dir)

    all_triggers = await store.list_all()
    assert len(all_triggers) == 2

    enabled_only = await store.list_all(enabled_only=True)
    assert len(enabled_only) == 1
