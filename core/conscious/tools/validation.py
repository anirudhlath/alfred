"""JSON Schema validation for tools whose schema arrives as data: registry tools and
integration capabilities (spec, component 2).

Pydantic AI checks only that the arguments are a JSON object (``ANY_OBJECT``). The tool's
own schema is checked by ``check_arguments``, whose ``ModelRetry`` names every error, so
the model can fix all of them in one retry.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic_ai import ModelRetry
from pydantic_core import SchemaValidator, core_schema

if TYPE_CHECKING:
    from collections.abc import Mapping

    from jsonschema.exceptions import ValidationError

logger = logging.getLogger(__name__)

ANY_OBJECT: Final = SchemaValidator(
    core_schema.dict_schema(core_schema.str_schema(), core_schema.any_schema())
)


def compile_schema(tool: str, schema: dict[str, Any]) -> Draft202012Validator | None:
    """A validator for *schema*, or None, logged once, when the schema is not valid.

    Compiled once per (tool, schema) per process: tools are listed on every model request.
    """
    return _compile(tool, json.dumps(schema, sort_keys=True))


@lru_cache(maxsize=1024)
def _compile(tool: str, schema_json: str) -> Draft202012Validator | None:
    schema = json.loads(schema_json)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        logger.error(
            "Tool %s has an invalid input_schema and is not offered: %s", tool, exc.message
        )
        return None
    return Draft202012Validator(schema)


def check_arguments(validator: Draft202012Validator, args: Mapping[str, Any]) -> None:
    """Raise ``ModelRetry`` naming every way *args* break the tool's schema."""
    errors = sorted(_describe(error) for error in validator.iter_errors(dict(args)))
    if errors:
        raise ModelRetry("; ".join(errors))


def _describe(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    return f"{path}: {error.message}" if path else error.message
