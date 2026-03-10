# Code Style & Conventions

## Python Style
- **Python 3.13+** features allowed (type unions with `|`, etc.)
- **Async-first:** prefer async functions and `asyncio`
- **Type hints:** mandatory, must pass `mypy --strict`
- **Quotes:** double quotes (enforced by ruff)
- **Line length:** 100 characters
- **Indent:** spaces (not tabs)
- **Imports:** sorted by ruff (isort rules via `I` selector)

## Naming
- snake_case for functions, methods, variables
- PascalCase for classes
- UPPER_SNAKE_CASE for constants
- Modules/files: snake_case

## Data Models
- All inter-component data uses **Pydantic v2** models
- Event schemas defined in `bus/schemas/events.py` (single source of truth)
- SDK re-exports compatible event types

## Patterns
- **Adapter pattern** for swappable components (voice, inference, etc.)
- **Domains contain agents** (not agents contain domains)
- **Librarian pattern:** scratchpad → nightly consolidation to preferences
- **Deterministic comms:** Pydantic JSON only between components

## Testing
- pytest + pytest-asyncio with `asyncio_mode = "auto"`
- Tests live next to source code in `tests/` subdirectories
- Test files: `test_*.py`

## What NOT to do
- Never use pip directly (use `uv`)
- Never use `Dockerfile` (use `Containerfile`)
- alfred-sdk is NOT on PyPI — copy from source for container builds
