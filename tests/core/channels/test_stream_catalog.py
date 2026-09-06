"""Tests for the stream catalog and entry decoding."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.channels import stream_catalog
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


_NOW_S = 1_718_000_400.0
_NOW_MS = 1_718_000_400_000


def _entries(count: int, *, oldest_ms: int = _NOW_MS) -> list[tuple[str, dict[str, str]]]:
    """``count`` entries newest-first, the oldest stamped at ``oldest_ms``."""
    ids = [f"{_NOW_MS}-{i}" for i in range(count - 1)] if count else []
    return [*[(entry_id, {}) for entry_id in ids], (f"{oldest_ms}-0", {})] if count else []


def _pinned_clock(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """Freeze ``time.time()`` and report how many times the catalog read it."""
    clock_reads = 0

    def _fake_time() -> float:
        nonlocal clock_reads
        clock_reads += 1
        return _NOW_S

    monkeypatch.setattr(stream_catalog.time, "time", _fake_time)
    return lambda: clock_reads


async def test_stream_summaries_reports_rate_over_five_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fewer entries than the sample size means the sample *is* the whole window,
    so the rate is exact: n over 300 seconds."""
    clock_reads = _pinned_clock(monkeypatch)

    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={"length": 42, "last-entry": (b"1718000000123-0", {b"event": b"{}"})}
    )
    redis.xrevrange = AsyncMock(return_value=[("2-0", {}), ("1-0", {})])

    out = await stream_summaries(redis)

    assert out["events"]["rate_5m"] == round(2 / 300, 3)
    calls = redis.xrevrange.await_args_list
    assert len(calls) == len(STREAM_CATALOG)
    # One boundary shared by every stream, exactly 300s back from a single clock read.
    assert {c.kwargs["min"] for c in calls} == {"1718000100000-0"}
    assert clock_reads() == 1
    assert {c.kwargs["max"] for c in calls} == {"+"}
    assert {c.kwargs["count"] for c in calls} == {stream_catalog._RATE_SAMPLE_SIZE}
    assert stream_catalog._RATE_SAMPLE_SIZE == 100
    # Scanned by the redis key, not the friendly catalog name.
    assert {c.args[0] for c in calls} == set(STREAM_CATALOG.values())


async def test_rate_is_exact_one_entry_below_the_sample_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """99 entries is the accept edge of the exact branch: the scan was not truncated,
    so every entry in the window is counted and divided by the window."""
    _pinned_clock(monkeypatch)

    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={"length": 99, "last-entry": (f"{_NOW_MS}-0", {b"event": b"{}"})}
    )
    # Oldest a full minute back: irrelevant below the cap, which is the point.
    redis.xrevrange = AsyncMock(return_value=_entries(99, oldest_ms=_NOW_MS - 60_000))

    out = await stream_summaries(redis)

    assert out["events"]["rate_5m"] == round(99 / 300, 3)


async def test_rate_is_extrapolated_once_the_sample_fills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """100 entries means the scan was truncated — the window holds at least that many,
    so the rate comes from the span the newest 100 actually cover (here 50 s → 2.0/s),
    not from a count that would read as 0.333 and saturate."""
    _pinned_clock(monkeypatch)

    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={"length": 5000, "last-entry": (f"{_NOW_MS}-0", {b"event": b"{}"})}
    )
    redis.xrevrange = AsyncMock(return_value=_entries(100, oldest_ms=_NOW_MS - 50_000))

    out = await stream_summaries(redis)

    assert out["events"]["rate_5m"] == 2.0


async def test_a_full_sample_spanning_no_time_falls_back_to_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """100 entries sharing one millisecond would divide by zero — the span guard
    reads them as the window instead, the floor of the estimate."""
    _pinned_clock(monkeypatch)

    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={"length": 100, "last-entry": (f"{_NOW_MS}-0", {b"event": b"{}"})}
    )
    redis.xrevrange = AsyncMock(return_value=_entries(100, oldest_ms=_NOW_MS))

    out = await stream_summaries(redis)

    assert out["events"]["rate_5m"] == round(100 / 300, 3)


async def test_a_full_sample_with_an_unparseable_oldest_id_falls_back_to_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No timestamp to extrapolate from is the same case as no span."""
    _pinned_clock(monkeypatch)

    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={"length": 100, "last-entry": (f"{_NOW_MS}-0", {b"event": b"{}"})}
    )
    entries = _entries(100)
    entries[-1] = ("not-an-id", {})
    redis.xrevrange = AsyncMock(return_value=entries)

    out = await stream_summaries(redis)

    assert out["events"]["rate_5m"] == round(100 / 300, 3)


async def test_stream_summaries_rate_is_zero_when_revrange_fails() -> None:
    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={"length": 1, "last-entry": (b"1718000000123-0", {b"event": b"{}"})}
    )
    redis.xrevrange = AsyncMock(side_effect=Exception("boom"))

    out = await stream_summaries(redis)

    assert out["events"]["length"] == 1
    assert out["events"]["rate_5m"] == 0.0
