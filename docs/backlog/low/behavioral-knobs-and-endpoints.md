# Behavioral Knobs + Bind/Endpoint Configurability

## Summary

Second-tier configurability from the 2026-07-18 audit: operational knobs and
network binds a self-hoster may want, below the model/path tier in demand.

## Context / Motivation

Hardcoded today:

- Web channel binds `0.0.0.0` (`core/channels/__main__.py:44`); trigger HTTP
  server likewise, and its self-endpoint hardcodes `http://localhost:8001`
  (`core/triggers/__main__.py:282,298`). Security-conscious hosts want
  interface-specific binds (e.g. Tailscale IP only).
- Cost: alert threshold `0.8` (`core/conscious/cost.py:49`) and the per-model
  pricing table (`cost.py:32-43`) — pricing drifts; unknown models fall back
  to guesses.
- Cadences/TTLs: routine-suggestion loop `900`s
  (`core/conscious/__main__.py:334`), Reflex tool-cache TTL `300`s
  (`core/reflex/engine.py:102`), auth session `24`h / WebAuthn challenge
  `5`min (`core/identity/auth_routes.py:35-36`), session timeout is already
  env-wired.
- APNs endpoints hardcoded with only a sandbox boolean
  (`core/notifications/adapters/apns.py:21-22,54`) — fine, but document.

## Acceptance Criteria

- [ ] `CHANNELS_BIND_HOST` (default `0.0.0.0`) and trigger-server equivalents;
      trigger self-endpoint derived from bind config, not a literal.
- [ ] `COST_ALERT_THRESHOLD` env-wired; pricing table overridable via a data
      file (YAML/JSON) with the current table as shipped default.
- [ ] Cadence/TTL knobs env-wired where genuinely useful (routine-suggestion
      interval, tool-cache TTL, auth session TTL) — with the audit's "fine
      hardcoded" items (consumer block/count, scratchpad flush) explicitly
      left alone to avoid config sprawl.
- [ ] Everything added lands in `AlfredConfig` + `.env.example` per the
      config-surface-unification ticket's conventions (do that ticket first).

## Notes

- Deliberate anti-goal: not every constant becomes an env var. The bar is
  "a reasonable self-hoster would change this," per audit ranking.
