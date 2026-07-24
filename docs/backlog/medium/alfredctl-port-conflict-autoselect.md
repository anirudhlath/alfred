# alfredctl: detect port conflicts and auto-select a free host port

**Priority:** medium

## Summary

`alfredctl up` (Docker/Podman) publishes `-p 8081:8081` unconditionally. If another
process already listens on host `:8081` (e.g. a native `python -m runner` dev stack),
Docker can still bind partially (IPv6-only) or fail, and `alfredctl smoke`'s
`http://localhost:8081` checks can silently hit the *other* process instead of the
container.

## Context / Motivation

Found during the Part 2 containerized smoke: a native dev runner was listening on
`*:8081` while the container smoke ran against `localhost:8081`. The exec-based checks
were unaffected, but HTTP checks were ambiguous until re-run on `--port 18081`.
The design spec (§8, worktree isolation) already called for this: "the launcher
auto-selects a free host port for :8081 and prints it".

## Acceptance criteria

- Before publishing, `alfredctl up` checks whether the requested host port is free;
  if taken, either auto-selects the next free port (printing it clearly) or fails
  with an explicit message naming the conflicting listener.
- `alfredctl smoke` resolves the ACTUAL published port from the runtime
  (`docker port <name>`) instead of assuming 8081.
- `alfredctl urls` does the same (drop the `--port` guess).
