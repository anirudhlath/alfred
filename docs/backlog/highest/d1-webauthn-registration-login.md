# D1: WebAuthn Registration + Login

## Summary
Implement WebAuthn-based authentication for the web PWA.

## Context
Prod-blocking security requirement. Needs `navigator.credentials` API on the frontend, server-side registration/authentication endpoints, and a credential store.

## Acceptance Criteria
- WebAuthn registration flow (create credential)
- WebAuthn login flow (verify credential)
- Credential storage backend
- PWA prompts for registration on first visit
