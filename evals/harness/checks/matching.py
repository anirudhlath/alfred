"""Expected-vs-actual matching shared by the check families.

An expected value is a plain scalar, a list (each element must match some actual
element), or ``{approx: x, tol: t}`` for numbers. Strings compare case-insensitively.
"""

from __future__ import annotations

import json
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


def _is_approx(expected: object) -> bool:
    return (
        isinstance(expected, dict) and "approx" in expected and set(expected) <= {"approx", "tol"}
    )


def value_matches(expected: object, actual: object) -> bool:
    if _is_approx(expected):
        assert isinstance(expected, dict)
        number = as_number(actual)
        target = as_number(expected["approx"])
        tol = as_number(expected.get("tol", 0)) or 0.0
        return number is not None and target is not None and abs(number - target) <= tol
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
    return expected == actual


def describe(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)
