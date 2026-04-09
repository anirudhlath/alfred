# D17: Eval Pytest Auto-Discovery

## Summary
Eval scenarios are loadable but not parameterized as pytest items.

## Context
Would enable running evals as part of the standard pytest suite with `pytest evals/`.

## Acceptance Criteria
- Eval scenarios discovered as pytest parametrized tests
- Standard pytest reporting for eval results
- Can still run via `python -m evals` CLI
