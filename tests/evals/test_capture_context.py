"""capture-context writes one live-state snapshot per service — the shape fixtures load."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from evals import __main__ as evals_main
from evals.context_fixtures import load_context_text
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot

if TYPE_CHECKING:
    from pathlib import Path

CREATE_REDIS = "shared.redis_streams.create_redis"
READER = "sdk.alfred_sdk.live_state.read_live_state_by_service"

SNAPSHOT = ContextSnapshot(
    controllable={"light": [ContextEntry(entity_id="light.lamp", state="on")]}
)
OTHER_SNAPSHOT = ContextSnapshot(
    sensors={"sensor": [ContextEntry(entity_id="sensor.hall_temp", state="21")]}
)


async def test_capture_writes_one_snapshot_per_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evals_main, "_CONTEXTS_DIR", tmp_path)
    redis = AsyncMock()
    reader = AsyncMock(return_value={"home-service": SNAPSHOT})
    with patch(CREATE_REDIS, return_value=redis), patch(READER, reader):
        await evals_main._cmd_capture_context(argparse.Namespace(output="captured.json"))

    written = json.loads((tmp_path / "captured.json").read_text())
    assert written == {"home-service": SNAPSHOT.model_dump()}
    assert "- light.lamp: on" in load_context_text("captured.json", tmp_path)
    reader.assert_awaited_once_with(redis)
    redis.aclose.assert_awaited_once()


async def test_capture_writes_services_in_sorted_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evals_main, "_CONTEXTS_DIR", tmp_path)
    # The reader hands them back unsorted, so only the command's own sort can order the file.
    reader = AsyncMock(return_value={"zeta-service": OTHER_SNAPSHOT, "alpha-service": SNAPSHOT})
    with patch(CREATE_REDIS, return_value=AsyncMock()), patch(READER, reader):
        await evals_main._cmd_capture_context(argparse.Namespace(output="captured.json"))

    written = json.loads((tmp_path / "captured.json").read_text())
    assert list(written) == ["alpha-service", "zeta-service"]
    assert written == {
        "alpha-service": SNAPSHOT.model_dump(),
        "zeta-service": OTHER_SNAPSHOT.model_dump(),
    }


async def test_capture_without_live_state_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evals_main, "_CONTEXTS_DIR", tmp_path)
    redis = AsyncMock()
    with (
        patch(CREATE_REDIS, return_value=redis),
        patch(READER, AsyncMock(return_value={})),
        pytest.raises(SystemExit) as excinfo,
    ):
        await evals_main._cmd_capture_context(argparse.Namespace(output="captured.json"))
    assert excinfo.value.code == 1
    redis.aclose.assert_awaited_once()
    assert not (tmp_path / "captured.json").exists()


async def test_capture_gives_up_on_an_unresponsive_redis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(evals_main, "_CONTEXTS_DIR", tmp_path)
    monkeypatch.setattr(evals_main, "CAPTURE_TIMEOUT_S", 0.05)
    # REDIS_HOST is pasted into the URL verbatim, so userinfo there puts a password in it.
    monkeypatch.setenv("REDIS_HOST", ":s3cret-pass@localhost")
    monkeypatch.setenv("REDIS_PORT", "6390")
    redis = AsyncMock()

    async def never_answers(_redis: object) -> dict[str, ContextSnapshot]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    with (
        patch(CREATE_REDIS, return_value=redis),
        patch(READER, AsyncMock(side_effect=never_answers)),
        pytest.raises(SystemExit) as excinfo,
    ):
        # A guard for the suite, far above the patched bound: without the command's own
        # bound this would hang forever.
        async with asyncio.timeout(2):
            await evals_main._cmd_capture_context(argparse.Namespace(output="captured.json"))

    assert excinfo.value.code == 1
    redis.aclose.assert_awaited_once()
    captured = capsys.readouterr()
    assert "localhost:6390" in captured.out
    assert "0.05s" in captured.out
    assert "s3cret-pass" not in captured.out + captured.err
    assert not (tmp_path / "captured.json").exists()
