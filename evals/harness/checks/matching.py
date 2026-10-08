"""Expected-vs-actual matching shared by the check families.

An expected value is one of:

- a plain scalar; strings compare case-insensitively;
- a list: each element must match some actual element;
- a mapping: each key must be present with a matching value, and other keys are ignored;
- ``{approx: x, tol: t}``, for numbers;
- ``{regex: p}``, for strings: the whole trimmed string must match, case-insensitively.
"""

from __future__ import annotations

import json
import re
from typing import Any


def as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def is_approx(expected: object) -> bool:
    return (
        isinstance(expected, dict) and "approx" in expected and set(expected) <= {"approx", "tol"}
    )


def is_regex(expected: object) -> bool:
    return isinstance(expected, dict) and set(expected) == {"regex"}


def validate_expected(expected: object) -> None:
    """Raise ValueError for a ``{regex}`` anywhere in *expected* that is not a compiling
    string, so a golden with one fails at load instead of erroring every sample."""
    if is_regex(expected):
        assert isinstance(expected, dict)
        pattern = expected["regex"]
        if not isinstance(pattern, str):
            raise ValueError(f"regex must be a string, got {pattern!r}")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"regex {pattern!r} does not compile: {exc}") from exc
    elif isinstance(expected, dict):
        for value in expected.values():
            validate_expected(value)
    elif isinstance(expected, list):
        for value in expected:
            validate_expected(value)


def value_matches(expected: object, actual: object) -> bool:
    if is_approx(expected):
        assert isinstance(expected, dict)
        number = as_number(actual)
        target = as_number(expected["approx"])
        tol = as_number(expected.get("tol", 0)) or 0.0
        return number is not None and target is not None and abs(number - target) <= tol
    if is_regex(expected):
        assert isinstance(expected, dict)
        return isinstance(actual, str) and (
            re.fullmatch(expected["regex"], actual.strip(), re.IGNORECASE) is not None
        )
    if isinstance(expected, bool):
        if isinstance(actual, str):
            return actual.strip().lower() == str(expected).lower()
        return actual is expected
    if isinstance(expected, int | float):
        number = as_number(actual)
        return number is not None and number == float(expected)
    if isinstance(expected, str):
        return isinstance(actual, str) and actual.strip().lower() == expected.strip().lower()
    if isinstance(expected, list):
        return isinstance(actual, list) and all(
            any(value_matches(e, a) for a in actual) for e in expected
        )
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and value_matches(value, actual[key]) for key, value in expected.items()
        )
    return expected == actual


def describe(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)
