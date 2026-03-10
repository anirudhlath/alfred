# Python Conventions

- Python 3.12+, use modern syntax (match/case, type unions with |, etc.)
- Async-first: use async/await for all I/O operations
- Pydantic v2 for all data models and schemas
- Type hints on all function signatures
- Ruff for linting and formatting (line-length 100)
- pytest + pytest-asyncio for testing
- No relative imports across top-level packages (core, bus, domains, sdk)
- Keep files focused — one clear responsibility per module
- Decorators for cross-cutting concerns (telemetry, validation)
