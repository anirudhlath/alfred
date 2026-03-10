# Suggested Commands

## Environment Setup
```bash
uv venv --python 3.13
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Before Committing (must pass all)
```bash
ruff check . --fix
ruff format .
mypy bus/ core/ domains/ sdk/ shared/ telemetry/
pytest
```

## Individual Commands
```bash
# Linting
ruff check .            # Check for lint errors
ruff check . --fix      # Auto-fix lint errors

# Formatting
ruff format .           # Format all files
ruff format --check .   # Check formatting without changing

# Type checking
mypy bus/ core/ domains/ sdk/ shared/ telemetry/   # Strict mode

# Testing
pytest                          # Run all tests
pytest core/reflex/tests/       # Run tests for a specific module
pytest -x                       # Stop on first failure
pytest -k "test_name"           # Run specific test

# Running services
python -m core.reflex           # Start Reflex Runner
python -m bus                   # Start MQTT↔Redis Bridge
```

## System Utilities (macOS / Darwin)
```bash
git status / git diff / git log
ls, find, grep (standard unix)
```
