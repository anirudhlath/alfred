"""Tests for the stream catalog and entry decoding."""

from typing import Any
from unittest.mock import AsyncMock

from core.channels.stream_catalog import (
    STREAM_CATALOG,
    decode_entry,
    stream_summaries,
)
from shared.streams import EVENTS_STREAM, REFLEX_OBSERVATIONS_STREAM


def test_catalog_maps_friendly_names_to_keys() -> None:
    assert STREAM_CATALOG["events"] == EVENTS_STREAM
    assert STREAM_CATALOG["reflex_observations"] == REFLEX_OBSERVATIONS_STREAM
    assert len(STREAM_CATALOG) == 8


def test_decode_entry_parses_event_json() -> None:
    entry: dict[bytes | str, bytes | str] = {b"event": b'{"event_type": "state_changed", "x": 1}'}
    assert decode_entry(entry) == {"event_type": "state_changed", "x": 1}


def test_decode_entry_falls_back_to_raw_fields() -> None:
    entry: dict[bytes | str, bytes | str] = {b"event": b"not json", b"other": b"v"}
    assert decode_entry(entry) == {"event": "not json", "other": "v"}


async def test_stream_summaries_defensive_on_missing_stream() -> None:
    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(side_effect=Exception("no such key"))
    out: dict[str, dict[str, Any]] = await stream_summaries(redis)
    assert out["events"] == {"length": 0, "last_id": None, "last_ts": None, "rate_5m": 0.0}


async def test_stream_summaries_extracts_length_and_ts() -> None:
    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={"length": 42, "last-entry": (b"1718000000123-0", {b"event": b"{}"})}
    )
    out = await stream_summaries(redis)
    assert out["events"]["length"] == 42
    assert out["events"]["last_id"] == "1718000000123-0"
    assert out["events"]["last_ts"] == 1718000000.123


def test_decode_entry_parses_notification_json() -> None:
    entry: dict[bytes | str, bytes | str] = {
        b"notification": b'{"notification_id": "n1", "title": "Hi"}'
    }
    assert decode_entry(entry) == {"notification_id": "n1", "title": "Hi"}


async def test_stream_summaries_bytes_keys() -> None:
    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={b"length": 42, b"last-entry": (b"1718000000123-0", {b"event": b"{}"})}
    )
    out = await stream_summaries(redis)
    assert out["events"]["length"] == 42
    assert out["events"]["last_id"] == "1718000000123-0"
    assert out["events"]["last_ts"] == 1718000000.123


async def test_stream_summaries_reports_rate_over_five_minutes() -> None:
    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={"length": 42, "last-entry": (b"1718000000123-0", {b"event": b"{}"})}
    )
    redis.xrevrange = AsyncMock(return_value=[("2-0", {}), ("1-0", {})])

    out = await stream_summaries(redis)

    assert out["events"]["rate_5m"] == round(2 / 300, 3)
    kwargs = redis.xrevrange.await_args.kwargs
    assert kwargs["max"] == "+"
    assert kwargs["count"] == 5000
    assert kwargs["min"].endswith("-0")
    assert int(kwargs["min"].split("-")[0]) > 0


async def test_stream_summaries_rate_is_zero_when_revrange_fails() -> None:
    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={"length": 1, "last-entry": (b"1718000000123-0", {b"event": b"{}"})}
    )
    redis.xrevrange = AsyncMock(side_effect=Exception("boom"))

    out = await stream_summaries(redis)

    assert out["events"]["length"] == 1
    assert out["events"]["rate_5m"] == 0.0
