# Messaging Gateway Pattern for Channel Expansion

## Summary
Generalize channel onboarding so adding a new messaging platform (Telegram, Discord, WhatsApp, email) costs only an adapter + credentials — one gateway surface fronting all conversational platforms, instead of a bespoke bridge process per platform.

## Context
Hermes Agent runs a single gateway process that fronts Telegram, Discord, Slack, WhatsApp, Signal, and email, with cross-platform conversation continuity. Alfred already has the right primitives — `ChannelRegistry` with `@ChannelRegistry.register()` adapters, `USER_REQUESTS_STREAM` inbound, `NOTIFICATION_DISPATCH_STREAM` outbound with per-process delivery workers — but each new platform currently implies a new bespoke bridge (signal-bridge is its own repo/process with its own inbound forwarding).

The borrowable idea is the *gateway pattern*: a single sovereign messaging-gateway service (SDK-coupled only, per Pillar 2) that hosts N platform adapters, normalizes inbound messages to `UserRequest`, and runs one delivery worker consumer group for outbound notifications. New platform = new adapter class + CredentialSchema entry in the secrets manager, no new process or repo.

Alfred's current channels (Signal, web PWA, iOS) cover the user's daily use, so this is expansion rather than core intelligence — low priority.

## Acceptance Criteria
- Design doc: gateway service architecture — where it lives (extend channels process vs. new sovereign app), how identity claims map per platform (Signal phone-number trust model generalized)
- Platform adapters self-register via the existing `ChannelRegistry` decorator pattern; no hardcoded platform lists
- Each adapter declares a `CredentialSchema` so tokens (e.g. Telegram bot token) flow through the keyring-based secrets manager and settings UI
- Inbound messages from any platform publish `UserRequest` to `USER_REQUESTS_STREAM` with correct channel + identity metadata
- Outbound delivery via one gateway consumer group on `NOTIFICATION_DISPATCH_STREAM`
- Proof of pattern: one new platform implemented end-to-end (Telegram is the cheapest — long-poll bot API, no phone pairing)
- signal-bridge migration into the gateway evaluated (not required in first pass)

## Dependencies
- Notification dispatch stream + consumer groups — already built
- Secrets manager CredentialSchema (D25) — already built
- Identity gate: per-platform identity claim confidence needs a decision for non-Signal platforms
