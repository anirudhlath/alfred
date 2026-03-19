"""Response sanitization — strips prompt injection patterns from adapter responses.

All adapter responses pass through this layer before reaching
Claude's context. Defense-in-depth against compromised data sources.
"""

from __future__ import annotations

import re
from typing import Any

# Patterns that indicate prompt injection attempts
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"system\s*prompt\s*:", re.IGNORECASE),
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"</s>", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
    re.compile(r"<<SYS>>", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all)", re.IGNORECASE),
    re.compile(r"disregard\s+(all|any)\s+prior", re.IGNORECASE),
]


def _sanitize_string(value: str) -> str:
    """Remove prompt injection patterns from a string."""
    result = value
    for pattern in _INJECTION_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def sanitize_response(data: Any) -> Any:
    """Recursively sanitize integration response data.

    Walks dicts, lists, and strings. Leaves numbers and other types untouched.
    """
    if isinstance(data, str):
        return _sanitize_string(data)
    if isinstance(data, dict):
        return {k: sanitize_response(v) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_response(item) for item in data]
    return data
