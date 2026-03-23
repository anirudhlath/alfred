# Shared

Cross-cutting utilities used by multiple top-level packages (core, bus, domains).

- `config.py` — `AlfredConfig` dataclass, loads `.env` via python-dotenv
- `streams.py` — Redis stream/key constants (single source of truth)
- `secrets.py` — keyring wrapper for PII credentials (sync + async APIs)
- `types.py` — shared type aliases (`AioRedis`)
- `fs.py` — `atomic_write()` for safe file writes
- `logging.py` — Loguru setup with stdlib intercept
- `otel.py` — OpenTelemetry init
- `traced.py` — `@traced` decorator for span instrumentation

## Rules

- Keep this package dependency-free (no imports from core, bus, domains, or sdk)
- All Redis stream key strings MUST be defined here — never hardcode in consuming modules
- New shared utilities belong here only if used by 2+ top-level packages
