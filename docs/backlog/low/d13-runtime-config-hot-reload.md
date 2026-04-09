# D13: Runtime Config Hot-Reload

## Summary
`RUNTIME_CONFIG_KEY` defined in `streams.py` but unused.

## Context
Config changes require restart. Should support hot-reload via Redis pub/sub on the config key.

## Acceptance Criteria
- Config changes published to RUNTIME_CONFIG_KEY
- Services subscribe and reload affected config
- No restart required for supported config changes
