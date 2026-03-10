# Task Completion Checklist

After completing any coding task, run these checks in order:

1. **Lint:** `ruff check . --fix`
2. **Format:** `ruff format .`
3. **Type check:** `mypy bus/ core/ domains/ sdk/ shared/ telemetry/`
4. **Tests:** `pytest`

All four must pass before considering a task complete.

If editing SDK code, also check schema compatibility between `bus/schemas/events.py` and `sdk/alfred_sdk/events.py`.
