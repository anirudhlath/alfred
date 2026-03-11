"""TriggerRegistry — decorator-based, open trigger type registration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from core.triggers.models import BaseTrigger


class TriggerRegistry:
    """Maps trigger_type strings to BaseTrigger subclasses."""

    _registry: ClassVar[dict[str, type[BaseTrigger]]] = {}

    @classmethod
    def register_type(cls, trigger_type: str) -> Callable[[type[BaseTrigger]], type[BaseTrigger]]:
        """Class decorator to register a trigger type."""

        def decorator(trigger_cls: type[BaseTrigger]) -> type[BaseTrigger]:
            cls._registry[trigger_type] = trigger_cls
            return trigger_cls

        return decorator

    @classmethod
    def get(cls, trigger_type: str) -> type[BaseTrigger]:
        """Look up a trigger class by type string. Raises KeyError if unknown."""
        try:
            return cls._registry[trigger_type]
        except KeyError:
            raise KeyError(
                f"Unknown trigger type: {trigger_type!r}. Available: {list(cls._registry.keys())}"
            ) from None

    @classmethod
    def available_types(cls) -> list[str]:
        """Return all registered trigger type names."""
        return list(cls._registry.keys())

    @classmethod
    def build_conditions_docs(cls) -> str:
        """Introspect all registered types and their Conditions schemas."""
        lines: list[str] = ["Available trigger types and their conditions:"]
        for type_name, trigger_cls in sorted(cls._registry.items()):
            conditions_cls: Any = getattr(trigger_cls, "Conditions", None)
            if conditions_cls is None:
                lines.append(f"  - {type_name}: (no conditions schema)")
                continue

            fields: dict[str, str] = {}
            if hasattr(conditions_cls, "model_fields"):
                for fname, finfo in conditions_cls.model_fields.items():
                    annotation = finfo.annotation
                    type_str = getattr(annotation, "__name__", str(annotation))
                    required = finfo.is_required()
                    desc = finfo.description or ""
                    key = fname if required else f"{fname}?"
                    val = f"{type_str}" + (f" ({desc})" if desc else "")
                    fields[key] = val

            fields_str = ", ".join(f"{k}: {v}" for k, v in fields.items())
            lines.append(f"  - {type_name}: {{{fields_str}}}")

        return "\n".join(lines)
