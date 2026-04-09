# D16: ALFRED_TRACE Production Flag

## Summary
No conditional trace toggle for production environments.

## Context
Need an `ALFRED_TRACE` env var to enable/disable detailed tracing without code changes.

## Acceptance Criteria
- `ALFRED_TRACE` env var controls trace verbosity
- Default off in production, on in dev
- Togglable at runtime without restart
