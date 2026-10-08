"""BaseFeature abstraction and @tool decorator for microservice tool registration."""

from __future__ import annotations

import inspect
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypeVar, overload

from pydantic import (
    BaseModel,
    Field,
    PydanticInvalidForJsonSchema,
    PydanticSchemaGenerationError,
    create_model,
)
from pydantic.json_schema import PydanticJsonSchemaWarning

if TYPE_CHECKING:
    from collections.abc import Mapping

# ── Pydantic Manifest Models (write-side, for Redis registration) ──


# Python type names, as `ToolParameter.type` carries them → JSON Schema types.
JSON_TYPE_BY_PYTHON_NAME: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
}


class ToolParameter(BaseModel):
    """Schema for a single tool parameter in the manifest.

    ``type`` is the legacy type name, kept on the wire for readers of ``parameters``
    (Reflex renders the names). A hand-built tool sets ``required`` and, for an exact
    schema, ``json_schema``; without it the schema is derived from ``type``.
    """

    type: str
    description: str = ""
    default: Any = None
    required: bool = False
    json_schema: dict[str, Any] | None = None


ToolAudience = Literal["reflex", "conscious"]
ToolRisk = Literal["benign", "elevated", "critical"]


class CredentialField(BaseModel):
    """Describes one credential input field for a sovereign service.

    Field shape MUST stay identical to core/integrations/base.py CredentialField.
    The JSON contract is the coupling — the SDK never imports core. Guarded by
    sdk/tests/test_schema_compatibility.py::test_credential_models_match_core_field_shape.
    """

    label: str
    field_type: Literal["text", "password", "url"] = "text"
    required: bool = True
    placeholder: str = ""
    default: str = ""  # Pre-filled value (use for sensible defaults like known URLs)
    help_text: str = ""
    transient: bool = False  # If True, value is pushed to the service but not persisted


class CredentialSchema(BaseModel):
    """Describes all credential fields for a sovereign service."""

    fields: dict[str, CredentialField]


class ToolManifest(BaseModel):
    """Schema for a single tool in the manifest."""

    name: str
    description: str = ""
    parameters: dict[str, ToolParameter] = {}
    # The tool's arguments as one JSON Schema object: what a model is offered.
    input_schema: dict[str, Any] = {}
    audience: ToolAudience = "conscious"
    risk: ToolRisk = "benign"


class FeatureManifest(BaseModel):
    """Schema for a feature (group of tools) in the manifest."""

    name: str
    description: str = ""
    tools: list[ToolManifest] = []


class ServiceManifest(BaseModel):
    """Schema for a service's full registration manifest."""

    service_name: str
    service_endpoint: str
    features: list[FeatureManifest] = []
    credentials_schema: CredentialSchema | None = None
    credentials_endpoint: str | None = None


# ── input_schema assembly (hand-built tools) ──


def _base_type_name(type_name: str) -> str:
    """``"list[str]"`` → ``"list"``, ``"datetime.datetime | None"`` → ``"datetime.datetime"``."""
    return type_name.split("|")[0].strip().split("[")[0].strip()


def input_schema_from_parameters(parameters: Mapping[str, ToolParameter]) -> dict[str, Any]:
    """Assemble a tool's JSON Schema from per-parameter metadata.

    For tools built by hand (no signature to read) and for manifests written before
    ``input_schema`` existed. A parameter's ``json_schema`` wins over its ``type`` name.

    Args:
        parameters: Parameter name → its metadata.

    Returns:
        An object schema with ``properties`` and ``required``.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in parameters.items():
        prop: dict[str, Any] = (
            dict(param.json_schema)
            if param.json_schema is not None
            else {"type": JSON_TYPE_BY_PYTHON_NAME.get(_base_type_name(param.type), "string")}
        )
        if param.description and "description" not in prop:
            prop["description"] = param.description
        properties[name] = prop
        if param.required:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


# ── ToolMeta dataclass ──


@dataclass(frozen=True)
class ToolMeta:
    """Extracted metadata for a single tool method.

    ``input_schema`` is the tool's arguments as one JSON Schema object. Left empty, it
    is assembled from ``parameters``.
    """

    name: str
    description: str
    parameters: dict[str, ToolParameter]
    audience: ToolAudience = "conscious"
    risk: ToolRisk = "benign"
    input_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.input_schema:
            # Frozen dataclass: object.__setattr__ is the sanctioned way to fill a derived field.
            object.__setattr__(self, "input_schema", input_schema_from_parameters(self.parameters))


# ── Docstring parser ──


def _parse_google_docstring_args(docstring: str) -> dict[str, str]:
    """Extract parameter descriptions from a Google-style Args section.

    Args:
        docstring: The full docstring to parse.

    Returns:
        Mapping of parameter name to description string.
    """
    args: dict[str, str] = {}
    in_args = False
    current_param: str | None = None
    current_desc_lines: list[str] = []

    for line in docstring.split("\n"):
        stripped = line.strip()

        if stripped == "Args:":
            in_args = True
            continue

        if in_args:
            # End of Args section: blank line or new section header
            if stripped == "" or (stripped.endswith(":") and not stripped.startswith(" ")):
                if current_param is not None:
                    args[current_param] = " ".join(current_desc_lines).strip()
                break

            # New parameter line: "name: description"
            param_match = re.match(r"^(\w+)\s*(?:\(.*?\))?\s*:\s*(.*)$", stripped)
            if param_match:
                if current_param is not None:
                    args[current_param] = " ".join(current_desc_lines).strip()
                current_param = param_match.group(1)
                current_desc_lines = [param_match.group(2)]
            elif current_param is not None:
                # Continuation line for current parameter
                current_desc_lines.append(stripped)

    # Flush last parameter if docstring ends without blank line
    if current_param is not None and current_param not in args:
        args[current_param] = " ".join(current_desc_lines).strip()

    return args


# ── input_schema generation (@tool methods) ──

# Keywords whose values are data, not schemas: a "title" inside them is left alone.
_LITERAL_KEYWORDS = frozenset({"default", "enum", "const", "examples"})
# Keywords whose values map names to schemas: the names are never keywords.
_SCHEMA_MAPS = frozenset({"properties", "$defs", "patternProperties"})

# Parameter kinds a dispatcher cannot pass by keyword (`AlfredClient.dispatch` calls fn(**params)).
_NOT_BY_KEYWORD_KINDS = (
    inspect.Parameter.VAR_POSITIONAL,
    inspect.Parameter.VAR_KEYWORD,
    inspect.Parameter.POSITIONAL_ONLY,
)


def _strip_titles(node: Any) -> Any:
    """Drop Pydantic's generated ``title`` keywords: tokens for the model, no meaning.

    A property or model *named* ``title`` is kept (names under ``properties``/``$defs``
    are walked as names), and data under ``default``/``enum``/``const``/``examples``
    is never touched.
    """
    if isinstance(node, list):
        return [_strip_titles(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" and isinstance(value, str):
            continue
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {name: _strip_titles(schema) for name, schema in value.items()}
        elif key in _LITERAL_KEYWORDS:
            out[key] = value
        else:
            out[key] = _strip_titles(value)
    return out


def _signature_input_schema(
    qualified_name: str,
    params: list[inspect.Parameter],
    hints: dict[str, Any],
    doc_args: dict[str, str],
) -> dict[str, Any]:
    """Generate one JSON Schema for a tool's arguments from its signature.

    Fields are positional names with the parameter name as alias, so a parameter may
    be called anything (``model_config``, ``_private``) without colliding with BaseModel.

    Raises:
        TypeError: A parameter's type cannot be described as JSON Schema.
    """
    fields: dict[str, Any] = {}
    for index, param in enumerate(params):
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[f"p{index}"] = (
            hints.get(param.name, Any),
            Field(default, alias=param.name, description=doc_args.get(param.name) or None),
        )
    try:
        model = create_model("ToolArgs", **fields)
        with warnings.catch_warnings():
            # A default JSON cannot carry (a sentinel object) is left out of the schema;
            # the parameter stays optional, which is all a model needs to know.
            warnings.simplefilter("ignore", PydanticJsonSchemaWarning)
            schema: dict[str, Any] = model.model_json_schema(by_alias=True)
    except (PydanticSchemaGenerationError, PydanticInvalidForJsonSchema) as exc:
        raise TypeError(
            f"Tool '{qualified_name}': cannot describe its parameters as JSON Schema: {exc}"
        ) from exc
    schema = _strip_titles(schema)
    schema.setdefault("properties", {})
    schema.setdefault("required", [])
    return schema


def _extract_tool_meta(
    fn: Any,
    feature_name: str,
    name_override: str | None = None,
    description_override: str | None = None,
    audience: ToolAudience = "conscious",
    risk: ToolRisk = "benign",
) -> ToolMeta:
    """Extract ToolMeta from a @tool-decorated method.

    Args:
        fn: The decorated function/method.
        feature_name: The owning feature's name (used for qualified tool name).
        name_override: Optional explicit tool name.
        description_override: Optional explicit description.
        audience: Which engine sees the tool ("reflex" or "conscious").
        risk: Dispatch risk gate ("benign", "elevated", "critical").
    """
    from typing import get_type_hints

    qualified_name = name_override or f"{feature_name}.{fn.__name__}"
    docstring = inspect.getdoc(fn) or ""
    description = description_override or (docstring.split("\n")[0] if docstring else "")

    # Parse parameter descriptions from Google-style docstring
    doc_args = _parse_google_docstring_args(docstring) if docstring else {}

    # Extract type hints (skip self, cls, return)
    try:
        hints = get_type_hints(fn)
    except Exception as exc:  # NameError for an unresolvable forward reference, among others
        raise TypeError(f"Tool '{qualified_name}': cannot resolve its type hints: {exc}") from exc

    sig = inspect.signature(fn)
    params = [p for name, p in sig.parameters.items() if name not in ("self", "cls")]
    for param in params:
        if param.kind in _NOT_BY_KEYWORD_KINDS:
            raise TypeError(
                f"Tool '{qualified_name}': parameter '{param.name}' must be passable by "
                "keyword (no *args, **kwargs or positional-only parameters)"
            )

    parameters: dict[str, ToolParameter] = {}
    for param in params:
        has_default = param.default is not inspect.Parameter.empty
        parameters[param.name] = ToolParameter(
            type=getattr(hints.get(param.name), "__name__", str(hints.get(param.name, "Any"))),
            description=doc_args.get(param.name, ""),
            default=param.default if has_default else None,
            required=not has_default,
        )

    return ToolMeta(
        name=qualified_name,
        description=description,
        parameters=parameters,
        audience=audience,
        risk=risk,
        input_schema=_signature_input_schema(qualified_name, params, hints, doc_args),
    )


# ── BaseFeature Base Class ──


class BaseFeature:
    """Base class for grouping related tools in a microservice.

    Subclass this and decorate methods with @tool. Tool metadata is
    auto-extracted from docstrings and type hints.
    """

    feature_name: str  # Must be set by subclass

    def get_tools(self) -> list[ToolMeta]:
        """Auto-discover @tool methods and extract their metadata."""
        tools: list[ToolMeta] = []
        for attr_name in dir(self):
            attr = getattr(self, attr_name, None)
            if attr is None or not getattr(attr, "_tool_marker", False):
                continue
            overrides = getattr(attr, "_tool_overrides", {})
            meta = _extract_tool_meta(
                attr,
                feature_name=self.feature_name,
                name_override=overrides.get("name"),
                description_override=overrides.get("description"),
                audience=overrides.get("audience", "conscious"),
                risk=overrides.get("risk", "benign"),
            )
            tools.append(meta)
        return tools

    def get_description(self) -> str:
        """Return the feature description from the class docstring."""
        doc = inspect.getdoc(type(self)) or ""
        return doc.split("\n")[0] if doc else ""

    def to_manifest(self) -> FeatureManifest:
        """Build a FeatureManifest from this feature's tools."""
        tool_manifests = [
            ToolManifest(
                name=t.name,
                description=t.description,
                parameters=dict(t.parameters),
                audience=t.audience,
                risk=t.risk,
                input_schema=t.input_schema,
            )
            for t in self.get_tools()
        ]
        return FeatureManifest(
            name=self.feature_name,
            description=self.get_description(),
            tools=tool_manifests,
        )


# ── @tool Decorator ──

# @tool supports both @tool and @tool(description=..., name=...)
# Using @overload for type safety so the decorated function's signature is preserved.

_F = TypeVar("_F", bound=Callable[..., Any])


@overload
def tool(fn: _F) -> _F: ...


@overload
def tool(
    *,
    description: str | None = None,
    name: str | None = None,
    audience: ToolAudience = "conscious",
    risk: ToolRisk = "benign",
) -> Callable[[_F], _F]: ...


def tool(
    fn: _F | None = None,
    *,
    description: str | None = None,
    name: str | None = None,
    audience: ToolAudience = "conscious",
    risk: ToolRisk = "benign",
) -> _F | Callable[[_F], _F]:
    """Mark a BaseFeature method as a tool.

    Supports bare ``@tool`` and ``@tool(description=..., name=..., audience=..., risk=...)``.
    Metadata is auto-extracted from docstring + type hints at discovery time.
    ``audience`` gates which engine sees the tool ("reflex" tools also reach
    Conscious); ``risk`` gates dispatch ("critical" requires user confirmation).
    """

    def decorator(f: _F) -> _F:
        f._tool_marker = True  # type: ignore[attr-defined]
        f._tool_overrides = {  # type: ignore[attr-defined]
            "description": description,
            "name": name,
            "audience": audience,
            "risk": risk,
        }
        return f

    if fn is not None:
        return decorator(fn)
    return decorator
