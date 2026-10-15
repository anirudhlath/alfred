"""JSON Schema validation for tools whose schema arrives as data."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import ModelRetry

from core.conscious.tools import validation
from core.conscious.tools.validation import ANY_OBJECT, check_arguments, compile_schema

if TYPE_CHECKING:
    from collections.abc import Iterator

_SCHEMA = {
    "type": "object",
    "properties": {
        "entity_id": {"type": "string"},
        "brightness": {"type": "integer", "minimum": 0, "maximum": 255},
    },
    "required": ["entity_id"],
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    validation._compile.cache_clear()
    yield
    validation._compile.cache_clear()


def test_any_object_accepts_objects_and_refuses_everything_else() -> None:
    assert ANY_OBJECT.validate_python({"a": 1}) == {"a": 1}
    assert ANY_OBJECT.validate_json('{"a": [1]}') == {"a": [1]}
    for bad in ([], "x", 5):
        with pytest.raises(ValueError):
            ANY_OBJECT.validate_python(bad)


def test_valid_arguments_pass() -> None:
    validator = compile_schema("home.light", _SCHEMA)
    assert validator is not None
    check_arguments(validator, {"entity_id": "light.desk", "brightness": 10})


def test_every_error_is_named_in_one_retry() -> None:
    validator = compile_schema("home.light", _SCHEMA)
    assert validator is not None
    with pytest.raises(ModelRetry) as caught:
        check_arguments(validator, {"brightness": 300, "colour": "red"})
    message = str(caught.value)
    assert "'entity_id' is a required property" in message
    assert "brightness: 300 is greater than the maximum of 255" in message
    assert "'colour' was unexpected" in message


def test_an_invalid_schema_compiles_to_none_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bad = {"type": "object", "properties": {"x": {"type": "no-such-type"}}}
    with caplog.at_level(logging.ERROR, logger="core.conscious.tools.validation"):
        assert compile_schema("home.bad", bad) is None
        assert compile_schema("home.bad", dict(bad)) is None
    assert caplog.text.count("Tool home.bad has an invalid input_schema and is not offered") == 1
