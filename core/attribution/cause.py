"""Classify the cause of a state change from its context, and phrase it.

The context comes from the publishing service (``ChangeContext``); this module only
reads it. The checks run in order and the first match wins:

1. no context            -> no cause (``None``): an older publisher, nothing to say
2. ``by_service``        -> ``alfred``: the service's own call, made on Alfred's request
3. ``user_id``           -> ``person``: someone used an app or a dashboard
4. ``parent_id``         -> ``automation``: a script or automation ran the call
5. anything else         -> ``outside``: a switch, an app or a device changed itself

``by_service`` is checked first so it wins over any user or parent id on the same context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from bus.schemas.events import ChangeContext

Cause = Literal["person", "alfred", "automation", "outside"]

# The causes that name an actor. ``outside`` says only that nobody we can name did it.
ATTRIBUTED: Final[frozenset[Cause]] = frozenset({"person", "alfred", "automation"})

_PERSON_FALLBACK: Final = "a person"
_WORDS: Final[dict[Cause, str]] = {
    "alfred": "by Alfred",
    "automation": "by an automation",
    "outside": "by a switch, app or device",
}


def cause_of(context: ChangeContext | None) -> Cause | None:
    """The cause of a change, or ``None`` when the event carried no context."""
    if context is None:
        return None
    if context.by_service:
        return "alfred"
    if context.user_id:
        return "person"
    if context.parent_id:
        return "automation"
    return "outside"


def cause_words(context: ChangeContext | None, name: str | None = None) -> str | None:
    """The cause as a short phrase (``by Alex``), or ``None`` when there is no cause.

    ``name`` is how the caller addresses the person (a display name it resolved); without
    it a person is named by the context's person entity, and without that, "a person".
    """
    cause = cause_of(context)
    if cause is None or context is None:
        return None
    if cause == "person":
        return f"by {name or context.person or _PERSON_FALLBACK}"
    return _WORDS[cause]
