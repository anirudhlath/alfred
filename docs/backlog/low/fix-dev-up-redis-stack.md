# Fix dev-up.sh Redis Stack startup

**Priority:** low

## Summary

`scripts/dev-up.sh` fails on current Homebrew: `brew services start redis-stack` errors with
`No available formula with the name "redis-stack"`.

## Context / Motivation

The Redis Stack Homebrew packaging changed — the install on this machine provides a
`redis-stack-server` binary (cask `redis-stack-server`, currently 7.4.0-v8) but no
brew-services-managed `redis-stack` formula. Found during live E2E verification of the
web app rebuild (2026-06-11): the script exits 1 and Redis never starts. Workaround used:
run `redis-stack-server` directly in the background.

## Acceptance Criteria

- `bash scripts/dev-up.sh` starts Redis Stack (with RediSearch) on a current Homebrew install
- Script handles both the old formula and the new `redis-stack-server` binary/cask
- `redis-cli ping` returns PONG and `FT._LIST` works after the script completes
