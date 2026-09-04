"""The env vars that name a host service, shared by both launch paths.

``localhost`` inside a container means the container itself, not the box, so both
launchers rewrite these to the container→host gateway: ``runner/__main__.py`` for
``python -m runner``, ``alfredctl/launch.py`` for ``alfredctl up``. One tuple rather
than a copy each, because a key added to one copy only leaves the other path pointing
at the container's own localhost — a silent gap no test can catch if the key was added
to neither.

Deliberately dependency-free, and deliberately not in ``shared/config.py``:
``alfredctl/launch.py`` needs these at module scope, and importing ``shared.config``
runs ``load_dotenv()`` on the repo-root ``.env`` at import — before the CLI has read
its own ``--env-file``.
"""

from __future__ import annotations

GATEWAY_REWRITE_KEYS = (
    "OLLAMA_HOST",
    "LMSTUDIO_HOST",
    "OPENAI_COMPAT_HOST",
    "EMBEDDING_HOST",
    "HA_HOST",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)
