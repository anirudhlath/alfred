# Satellite Reconnect: Minimum-Uptime Guard Before Backoff Reset

## Summary

The bridge resets its reconnect backoff to 1s after any session that completed the
handshake (`RunSatellite` sent). A misbehaving satellite that handshakes and then
immediately drops therefore gets retried at a constant ~1s forever — a mild crash-loop
hammer on both ends.

## Context / Motivation

Noted in the voice-satellite bridge final review (2026-07). Standard practice gates the
backoff reset on a minimum session duration (e.g. only reset if the session survived
≥30s); sessions shorter than that keep climbing the backoff ladder. Bounded impact today
(1 attempt/sec, logged), fine for a small home fleet.

## Acceptance Criteria

- Backoff resets only after a session exceeding a minimum uptime (constant, e.g. 30s);
  shorter sessions continue doubling toward the 60s cap.
- Deterministic regression test (no wall-clock sleeps — extend the existing
  patched-asyncio delay-recording harness in `tests/core/channels/satellite/test_bridge.py`).
- `docs/voice-satellites.md` reconnect section updated.
