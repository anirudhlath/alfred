"""capture-context writes one live-state snapshot per service — the shape fixtures load."""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from evals import __main__ as evals_main
from evals.context_fixtures import load_context_text
from sdk.alfred_sdk.context import ContextEntry, ContextSnapshot

if TYPE_CHECKING:
    from pathlib import Path

SNAPSHOT = ContextSnapshot(
    controllable={"light": [ContextEntry(entity_id="light.lamp", state="on")]}
)


async def test_capture_writes_one_snapshot_per_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evals_main, "_CONTEXTS_DIR", tmp_path)
    redis = AsyncMock()
    with (
        patch("shared.redis_streams.create_redis", return_value=redis),
        patch(
            "sdk.alfred_sdk.live_state.read_live_state_by_service",
            AsyncMock(return_value={"home-service": SNAPSHOT}),
        ),
    ):
        await evals_main._cmd_capture_context(argparse.Namespace(output="captured.json"))

    written = json.loads((tmp_path / "captured.json").read_text())
    assert written == {"home-service": SNAPSHOT.model_dump()}
    assert "- light.lamp: on" in load_context_text("captured.json", tmp_path)
    redis.aclose.assert_awaited_once()


async def test_capture_without_live_state_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evals_main, "_CONTEXTS_DIR", tmp_path)
    with (
        patch("shared.redis_streams.create_redis", return_value=AsyncMock()),
        patch(
            "sdk.alfred_sdk.live_state.read_live_state_by_service",
            AsyncMock(return_value={}),
        ),
        pytest.raises(SystemExit),
    ):
        await evals_main._cmd_capture_context(argparse.Namespace(output="captured.json"))
    assert not (tmp_path / "captured.json").exists()
