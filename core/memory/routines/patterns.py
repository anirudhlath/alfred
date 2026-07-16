"""Trigger pattern matching for routine lifecycle."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from datetime import datetime


def match_trigger_pattern(pattern: str, now: datetime, tz_name: str = "UTC") -> bool:
    """Check if a trigger pattern matches the current time.

    ``now`` should be an aware datetime (typically UTC); matching is performed
    against ``tz_name``'s wall clock, so callers pass UTC now plus the user's
    zone rather than pre-localizing.

    Supports HH:MM (±1 hour), 'morning', 'evening', 'weekday', 'weekend'.
    Returns True for unrecognized patterns (conservative — avoids premature dormancy).
    """
    now = now.astimezone(ZoneInfo(tz_name))
    p = pattern.lower()
    time_match = re.search(r"(\d{1,2}):(\d{2})", p)
    if time_match:
        hour = int(time_match.group(1))
        return abs(now.hour - hour) <= 1
    if "morning" in p:
        return 5 <= now.hour < 12
    if "evening" in p:
        return 17 <= now.hour < 23
    if "weekday" in p:
        return now.weekday() < 5
    if "weekend" in p:
        return now.weekday() >= 5
    return True  # Unknown pattern — conservative
