# Python Conventions

- Python 3.13+, use modern syntax (match/case, type unions with |, etc.)
- Async-first: use async/await for all I/O operations
- Pydantic v2 for all data models and schemas
- Type hints on all function signatures
- `uv` for all package management — never use pip directly
- `ruff` for linting AND formatting (line-length 100)
- `mypy --strict` for static type checking — all code must pass
- pytest + pytest-asyncio for testing
- No relative imports across top-level packages (core, bus, domains, sdk)
- Keep files focused — one clear responsibility per module
- Decorators for cross-cutting concerns (telemetry, validation)
