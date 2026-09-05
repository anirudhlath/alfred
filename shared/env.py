"""Environment-flag parsing shared by the server and the CLI.

One spelling of "is this flag on?" so ``core/channels/web_server`` and
``alfredctl/launch`` can never disagree about whether the operator set it. They did
disagree by prose only until this module existed: each carried its own tuple of truthy
strings, and a flag the CLI read as off while the server read it as on silently changes
which networks are trusted.

Deliberately dependency-free, for the same reason as ``shared/gateway.py``:
``alfredctl/launch.py`` imports it at module scope, and anything that pulls in
``shared.config`` would run ``load_dotenv()`` on the repo-root ``.env`` before the CLI
has read its own ``--env-file``.
"""

from __future__ import annotations

_TRUTHY = ("1", "true", "yes")


def is_truthy_flag(value: str | None) -> bool:
    """True for the env-var spellings Alfred treats as "on": ``1`` / ``true`` / ``yes``.

    Case-insensitive and whitespace-tolerant. Everything else is off, including
    ``None``, ``""``, ``"0"``, ``"false"`` and ``"no"`` — an unrecognised value is never
    treated as enabling anything.
    """
    return (value or "").strip().lower() in _TRUTHY
