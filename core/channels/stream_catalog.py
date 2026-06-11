"""Stream catalog — single source of truth for admin-visible Redis streams.

Maps friendly names (used in API paths and WS subscribe messages) to the
canonical Redis stream keys from shared.streams, and provides defensive
decoding of stream entries for display.

Stream entry shapes vary by stream:
- Most streams (events, actions, etc.): ``{"event": "<json>"}``
- Notification dispatch stream: ``{"notification": "<json>"}``

``decode_entry`` handles both payload field shapes transparently.
"""

from __future__ import annotations

import json
from typing import Any

from shared.streams import (
    ACTIONS_STREAM,
    EVENTS_STREAM,
    HOME_ACTION_RESULTS_STREAM,
    HOME_STATE_STREAM,
    NOTIFICATION_DISPATCH_STREAM,
    REFLEX_OBSERVATIONS_STREAM,
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
    decode_stream_value,
)
from shared.types import AioRedis  # noqa: TC001

# Fields that carry the primary JSON payload for a stream entry.
# "event"        — used by most streams (events, actions, user_requests, …)
# "notification" — used by NOTIFICATION_DISPATCH_STREAM (see dispatcher.py)
_PAYLOAD_FIELDS: frozenset[str] = frozenset({"event", "notification"})

STREAM_CATALOG: dict[str, str] = {
    "events": EVENTS_STREAM,
    "actions": ACTIONS_STREAM,
    "user_requests": USER_REQUESTS_STREAM,
    "user_responses": USER_RESPONSES_STREAM,
    "reflex_observations": REFLEX_OBSERVATIONS_STREAM,
    "notifications": NOTIFICATION_DISPATCH_STREAM,
    "home_state": HOME_STATE_STREAM,
    "home_action_results": HOME_ACTION_RESULTS_STREAM,
}

KEY_TO_NAME: dict[str, str] = {v: k for k, v in STREAM_CATALOG.items()}


def decode_entry(entry: dict[bytes | str, bytes | str]) -> dict[str, Any]:
    """Decode a stream entry.

    Entries carry a primary payload field — either ``{"event": "<json>"}``
    (most streams) or ``{"notification": "<json>"}`` (notification dispatch
    stream).  When the payload field contains a valid JSON object, its
    contents are returned directly.  For any other field, or when the payload
    field is not valid JSON, fall back to raw decoded field values.
    """
    decoded: dict[str, Any] = {}
    for k, v in entry.items():
        key = decode_stream_value(k)
        val = decode_stream_value(v)
        if key in _PAYLOAD_FIELDS:
            try:
                parsed: dict[str, Any] = dict(json.loads(val))
            except (json.JSONDecodeError, TypeError, ValueError):
                decoded[key] = val
            else:
                return parsed
        else:
            decoded[key] = val
    return decoded


def _id_to_ts(entry_id: str) -> float | None:
    """Stream IDs are '<ms>-<seq>' — extract seconds since epoch."""
    try:
        return int(entry_id.split("-")[0]) / 1000.0
    except ValueError:
        return None


async def stream_summaries(redis: AioRedis) -> dict[str, dict[str, Any]]:
    """Length + last-entry recency for every catalog stream. Missing streams
    report zero — never raise."""
    out: dict[str, dict[str, Any]] = {}
    for name, key in STREAM_CATALOG.items():
        try:
            raw: Any = await redis.xinfo_stream(key)
            info: dict[str | bytes, Any] = raw
            last = info.get("last-entry") or info.get(b"last-entry")
            last_id = decode_stream_value(last[0]) if last else None
            out[name] = {
                "length": int(info.get("length") or info.get(b"length") or 0),
                "last_id": last_id,
                "last_ts": _id_to_ts(last_id) if last_id else None,
            }
        except Exception:
            out[name] = {"length": 0, "last_id": None, "last_ts": None}
    return out
