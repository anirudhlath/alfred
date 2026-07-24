# Runner: fail fast when container-mode redis modules are missing

**Priority:** low

## Summary

`runner/__main__.py::_redis_command()` adds `--loadmodule` flags only for module `.so`
files that exist under `ALFRED_REDIS_MODULES_DIR`. If the dir is empty or missing (a
misbuilt image or misconfigured host), it silently starts a modules-less `redis-server`
— vector memory then fails later with cryptic `FT.*` errors instead of a clear
readiness-gate failure.

## Context / Motivation

Flagged in the Part 2 final whole-branch review. Mitigated today: the shipped image
bakes `redisearch.so`/`rejson.so` from `redis:8-bookworm` and `alfredctl smoke`
asserts `MODULE LIST` contains `search`. Only bites hand-rolled environments.

## Acceptance criteria

- When `redis-stack-server` is NOT on PATH (container path) and zero modules are found
  under `ALFRED_REDIS_MODULES_DIR`, the runner logs a clear error naming the dir and
  the expected `.so` files, and the redis ready gate fails (non-zero supervisor exit)
  instead of starting a modules-less server.
- Unit test covering the empty-modules-dir path.
