# Lock WebAuthn Registration After First Passkey

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** high
**Severity (audit):** high
**Source:** Public-release readiness audit 2026-07-18 (findings #4)

## Summary
WebAuthn registration is open to anyone who can reach the trusted network: there is no first-registration lock, so an added passkey silently becomes the owner. Any peer can enroll their own credential and gain full owner (`"sir"`) access with no owner-approval step. Because `anirudhlath/alfred` is already public (v0.1.0 shipped 2026-07-16), this is a live auth-bypass on a released repo — post-exposure response, not a pre-release hardening task.

## Context / Motivation
`/api/auth/register/begin` and `/register/complete` (`core/identity/auth_routes.py:94-200`) never check whether a credential already exists before enrolling a new one, and `register/complete` immediately mints an authenticated 24h session cookie for the new passkey (`core/identity/auth_routes.py:179-200`). The only barrier is `require_trusted_network`.

On a shared Tailnet, any peer can enroll their own passkey and gain full owner (`"sir"`) access. The trusted-network gate is not a sufficient control: it can be reached via finding #2 (behind a proxy) or finding #1 (by writing Redis), and there is no owner-approval step gating a second enrollment. The severity is high because a successful enrollment yields full owner clearance, and the exposed surface is on an already-public release.

## Acceptance Criteria
- [ ] Registration is locked after the first credential exists — `register/begin`/`register/complete` reject enrolling a new passkey once any credential is present, unless the caller presents an existing authenticated owner session or a one-time server-side enrollment token
- [ ] Adding further passkeys requires either an authenticated session or a one-time server-side enrollment token (not just `require_trusted_network`)
- [ ] A notification is surfaced when a new passkey is enrolled
- [ ] `register/complete` no longer auto-mints an owner session cookie for an unapproved new credential when the registration lock applies

## Related
- [Multi-User WebAuthn with Roles](../low/multi-user-webauthn-roles.md) — future multi-identity/roles work (the eventual home for enforced clearance/roles).
