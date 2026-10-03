"""Display-only redaction for what alfredctl prints.

alfredctl output gets pasted into issues, chat and CI logs, so nothing it echoes may
carry the operator's credentials. Two shapes reach the terminal:

- URLs with ``user:password@`` userinfo — ``EMBEDDING_HOST``, ``HA_HOST`` and
  ``OLLAMA_HOST`` can all carry basic auth, and ``doctor`` quotes them in its checks.
- The runtime command ``main._run`` echoes before running it, which for ``up`` carries
  the whole merged env as ``-e KEY=value``: every ``.env`` secret, ``HF_TOKEN`` and the
  secrets passphrase.

Every function here returns a copy for printing. The request or command itself is
always made with the values as configured.

Stdlib-only on purpose: alfredctl modules load before anything has read the operator's
``.env``, and a display helper has no reason to import configuration.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# What a redacted value prints as: visibly a redaction, never mistakable for a value.
REDACTED = "***"

# Authority-only: userinfo is everything between the start of the authority and the
# last "@" before the path, so an @ inside a path is left alone and this cannot raise
# the way urlsplit can on a malformed authority. Two details earn their keep:
# ``[^/]*@`` is greedy to the *last* @ because httpx delimits there too (a password may
# contain one), and the scheme is optional because a schemeless host still reaches the
# output — httpx rejects it, and the rejection detail quotes the URL back.
_USERINFO_RE = re.compile(r"^([a-zA-Z][\w+.-]*://|//)?[^/]*@")

# Runtime flags whose next argument is an environment pair. Docker, Podman and Apple
# `container` all spell it `-e` / `--env`; `--env=KEY=value` is handled separately.
_ENV_FLAGS = frozenset({"-e", "--env"})
_ENV_EQUALS = "--env="


def redact_userinfo(url: str) -> str:
    """Hide any ``user:password@`` before a URL is printed."""
    return _USERINFO_RE.sub(rf"\1{REDACTED}@", url)


def _redact_env_pair(pair: str) -> str:
    """``KEY=value`` → ``KEY=***``. A bare ``KEY`` (pass the host's value through) has
    no value on the command line, so it is shown as it is."""
    key, sep, _ = pair.partition("=")
    return f"{key}={REDACTED}" if sep else pair


def redact_command(cmd: Sequence[str]) -> list[str]:
    """A printable copy of ``cmd``: every env value elided, every URL's userinfo masked.

    Every ``-e`` value goes, whatever its key. A list of secret-looking names would be
    one more thing to maintain, and the variable added next would escape it. Flags,
    image, container name and keys survive, so the line still says what ran and can be
    replayed by filling the values back in.
    """
    shown: list[str] = []
    env_value_next = False
    for arg in cmd:
        if env_value_next:
            shown.append(_redact_env_pair(arg))
            env_value_next = False
        elif arg in _ENV_FLAGS:
            shown.append(arg)
            env_value_next = True
        elif arg.startswith(_ENV_EQUALS):
            shown.append(_ENV_EQUALS + _redact_env_pair(arg.removeprefix(_ENV_EQUALS)))
        else:
            shown.append(redact_userinfo(arg))
    return shown
