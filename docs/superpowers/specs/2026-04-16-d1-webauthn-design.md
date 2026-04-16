# D1: WebAuthn Registration & Login

**Date:** 2026-04-16
**Status:** Approved
**Priority:** Highest (prod-blocking)

## Overview

Implement WebAuthn (passkey) authentication for Alfred's web PWA. Replaces the current zero-auth model where anyone who connects gets "sir" identity at 0.7 confidence via a hardcoded `identity_claim`. After D1, the web channel enforces a hard authentication gate — no chat without a valid passkey session.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| User model | Single-user (owner only) | Alfred is a personal assistant. Multi-user with roles deferred to backlog (low). |
| Registration gate | Tailscale trusted network | Consistent with existing `require_trusted_network` pattern. Simple, matches environment. |
| Credential store | SQLite | Structured, security-critical, long-lived data. Already in the stack. |
| Auth sessions | HTTP cookie (carried on WS upgrade) | HttpOnly, automatic on WS upgrade, no client-side token management. |
| Registration UX | Replace onboarding identity placeholder (step 1) | Existing step is a placeholder. Keeps wizard cohesive. |
| Unauthenticated users | Hard gate — no chat | Personal assistant on trusted network. Guest access adds complexity for no benefit. |
| Approach | py_webauthn + Conditional UI passkeys | Battle-tested crypto library + modern passkey UX with browser autofill. |

## Data Model

### SQLite Credential Store

Database: `data/credentials.db`

```sql
CREATE TABLE webauthn_credentials (
    id          TEXT PRIMARY KEY,   -- base64url credential ID
    public_key  BLOB NOT NULL,      -- COSE public key bytes
    sign_count  INTEGER NOT NULL,   -- replay protection counter
    device_name TEXT NOT NULL,       -- user-provided label (e.g. "MacBook Pro")
    transports  TEXT NOT NULL,       -- JSON array: ["internal", "hybrid"]
    created_at  TEXT NOT NULL,       -- ISO 8601
    last_used_at TEXT NOT NULL       -- ISO 8601
);
```

### Auth Sessions (Redis)

Key: `alfred:auth:{session_id}`, TTL: 24 hours.

Fields: `authenticated` (bool), `credential_id` (text), `created_at` (ISO 8601).

### Challenge Storage (Redis)

Key: `alfred:webauthn:challenge:{challenge_id}`, TTL: 5 minutes. `challenge_id` is a server-generated random UUID created per registration/login attempt — not tied to any session. Value is the challenge bytes. Deleted immediately after verification (one-time use).

### WebAuthn User Entity

Single fixed user for the owner. Used in py_webauthn registration options:
- `user_id`: Static UUID stored in `data/credentials.db` (generated once, persisted in a `webauthn_user` table)
- `user_name`: `"sir"`
- `user_display_name`: `"Sir"`

## Credential Manager

**File:** `core/identity/credentials.py`

**Class:** `CredentialStore` — async SQLite wrapper using aiosqlite.

**Methods:**
- `save_credential(id, public_key, sign_count, device_name, transports)` — insert new credential
- `get_credential(id) -> Credential | None` — lookup by credential ID
- `list_credentials() -> list[Credential]` — all registered credentials
- `update_sign_count(id, new_count)` — update counter + `last_used_at`
- `delete_credential(id)` — remove credential
- `has_any_credential() -> bool` — check if any credential exists (first-visit detection)

Auto-creates table on first use.

## Server-Side Endpoints

**File:** `core/identity/auth_routes.py` — FastAPI APIRouter, mounted on the main app.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/auth/status` | None | Returns `{registered: bool, authenticated: bool}` |
| POST | `/api/auth/register/begin` | `require_trusted_network` | Generate registration challenge via py_webauthn |
| POST | `/api/auth/register/complete` | `require_trusted_network` | Verify attestation, save credential, create session, set cookie |
| POST | `/api/auth/login/begin` | None | Generate authentication challenge with allowCredentials |
| POST | `/api/auth/login/complete` | None | Verify assertion, update sign count, create session, set cookie |
| POST | `/api/auth/logout` | Cookie | Delete auth session, clear cookie |

### WebAuthn Configuration

- `rp_id`: Derived from request hostname (e.g. `"alfred.tail1234.ts.net"` or `"localhost"`)
- `rp_name`: `"Alfred"`
- `origin`: Derived from request (supports Tailscale HTTPS and localhost HTTP)
- `attestation`: `"none"` (no hardware attestation needed for single-user)
- `resident_key`: `"preferred"` (enables passkey / Conditional UI)
- `user_verification`: `"preferred"`

## Cookie & Session Middleware

Added to the FastAPI app. On every request (including WebSocket upgrade):

1. Read `alfred_auth` cookie
2. Validate against Redis (`alfred:auth:{session_id}`)
3. Inject `authenticated: bool` into `request.state`

The WebSocket handler reads `request.state.authenticated` and sets it on `UserRequest`.

### Cookie Properties

- `HttpOnly` — no JS access
- `SameSite=Strict` — no cross-site requests
- `Secure` — HTTPS only (disabled for localhost dev)
- `Max-Age` = 24 hours (matches Redis TTL)

## Frontend Auth Flow

### New File: `web/auth.js`

Handles all WebAuthn client-side logic: registration ceremony, login ceremony (including Conditional UI), and auth status checks.

### Page Load Flow

```
Page load
  → GET /api/auth/status
  → {registered: false}                    → show onboarding (step 1 = WebAuthn registration)
  → {registered: true, authenticated: false} → show login screen
  → {registered: true, authenticated: true}  → show chat
```

### Registration (Onboarding Step 1)

1. User sees "Register this device" with a button + device name input
2. Click → `POST /api/auth/register/begin` → get options
3. `navigator.credentials.create(options)` → browser biometric/passkey prompt
4. `POST /api/auth/register/complete` with attestation → cookie set
5. Proceed to onboarding step 2 (preferences)

### Login (Return Visits) — Conditional UI

1. Login screen renders with a text input that has `autocomplete="webauthn"`
2. On load, call `navigator.credentials.get({ mediation: "conditional" })` — browser shows passkey suggestion in the input field automatically
3. User taps suggestion → biometric prompt → assertion response
4. `POST /api/auth/login/complete` → cookie set → show chat
5. Fallback: a "Sign in with passkey" button for browsers without Conditional UI support

### Logout

Button in settings page → `POST /api/auth/logout` → clear cookie → redirect to login screen.

### Client Identity Removal

Remove the hardcoded `identity: 'sir'` from WebSocket messages. The server determines identity from the auth cookie on the WS connection — the client no longer claims identity.

## Integration with Existing Systems

### IdentityGate — No Changes

Already handles `authenticated=True` → "sir" at confidence 0.99 with `method="webauthn"`. This path will now actually be triggered.

### WebSocket Handler (`web_server.py`)

- On WS connect: read `alfred_auth` cookie from upgrade request, validate against Redis, store `authenticated` flag on connection
- On each message: set `UserRequest.authenticated` from connection auth state (instead of always `False`)
- Remove `identity_claim` from client message parsing — identity is server-side

### Session Manager — No Changes

Conversation sessions remain separate from auth sessions. A single auth session can span multiple conversation sessions.

### Onboarding Endpoint (`/api/onboarding`)

Add `require_auth` dependency — preferences should only be saved by authenticated users.

### iOS Channel — No Changes

iOS uses Face ID, not WebAuthn. The `authenticated` field on UserRequest continues to be set by the iOS-specific trust mechanism.

## Error Handling & Security

### Challenge Replay Prevention

Challenges stored in Redis with 5min TTL and deleted immediately after use. Replayed challenge → "not found" error.

### Sign Count Verification

On each login, verify the credential's sign count is strictly greater than the stored value. Reject if not (indicates cloned credential). Update stored count on success.

### Failure Modes

| Scenario | Behavior |
|----------|----------|
| Redis down during challenge | 503 — registration/login unavailable, clear error message |
| SQLite locked | Retry with backoff (aiosqlite handles this) |
| Browser doesn't support WebAuthn | Login screen shows "Your browser doesn't support passkeys" — no fallback |
| Credential deleted from SQLite | Next login fails, user must re-register from trusted network |
| Cookie expired | `/api/auth/status` returns `authenticated: false`, frontend redirects to login |
| Invalid attestation/assertion | 401 with generic "Authentication failed" (no info leakage) |

### No Rate Limiting

Tailscale network gate on registration + single-user model makes brute force impractical. Rate limiting is backlog item D10.

## Testing Strategy

### Unit Tests (`tests/core/identity/`)

- `test_credentials.py` — CredentialStore CRUD: save, get, list, update sign count, delete, `has_any_credential()` on empty/non-empty DB
- `test_auth_routes.py` — FastAPI TestClient against all 6 endpoints. Mock py_webauthn ceremony functions, verify challenge flow, cookie setting, Redis session creation/deletion
- `test_auth_middleware.py` — Cookie validation middleware: valid cookie → authenticated, expired/missing/invalid → unauthenticated, WebSocket upgrade carries cookie

### Integration Tests (`tests/integration/`)

- `test_webauthn_flow.py` — Full registration → login → WebSocket authenticated message flow using py_webauthn test helpers. Verify `UserRequest.authenticated=True` reaches the ConsciousEngine.

### Mocking Strategy

- `navigator.credentials` — browser API, not testable in pytest, covered by QA
- py_webauthn internals — trust the library, mock at the `generate_*`/`verify_*` boundary
- Redis — fakeredis (consistent with existing tests)
- SQLite — real in-memory SQLite (fast, no mock needed)

## Dependencies

| Package | Purpose |
|---------|---------|
| `py_webauthn` | Server-side WebAuthn ceremony (challenge generation, attestation/assertion verification) |
| `aiosqlite` | Async SQLite access for credential store |
