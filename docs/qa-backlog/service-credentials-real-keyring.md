# Real OS Keyring Storage for Service Credentials

**Feature:** Sovereign-service credential storage (`shared/secrets.py` via `core/channels/service_credentials.py`)
**Priority:** high
**Type:** integration

## Prerequisites
- macOS dev machine with Keychain Access available
- Alfred running WITHOUT the `InMemoryKeyring` test fixture — i.e. run `python -m core.channels` directly (not `pytest`), so the real `keyring` backend (macOS Keychain) is active
- A stub or Plan-2 service that declares a `credentials_schema` + `credentials_endpoint` in its SDK registration (home-service does not declare one yet on this branch — Plan 1 only; use a small stub `AlfredClient` registration or wait for Plan 2's home-service update)

## Test Steps
1. Register the stub/Plan-2 service against a live Alfred core (`AlfredClient(credentials_schema=..., credentials_endpoint=...)` then `register()`).
2. `PUT /api/integrations/{service_name}/credentials` with valid field values via curl or the Settings UI.
3. Open macOS Keychain Access and search for service name `"{service_name}"` (per `shared/secrets.py` namespacing) — confirm entries exist for each non-transient field with key format `"{field}"`.
4. Restart the `core.channels` process (kill + relaunch) and repeat the `GET /api/integrations` call — confirm `configured: true` still reflects the persisted keyring values (i.e. no re-entry needed).
5. Delete credentials via `DELETE /api/integrations/{service_name}/credentials` and confirm the Keychain entries are removed.
6. On first PUT, watch for any macOS "Alfred wants to access keychain items" permission prompt — confirm it does not block or crash the request (or note the exact UX if it does).

## Expected Result
- Credentials persist in the real macOS Keychain under the service's namespace, survive process restart, and are cleanly removed on DELETE.
- No hang, crash, or unhandled prompt blocks the async keyring calls (all keyring I/O runs via `asyncio.to_thread` per `shared/secrets.py`).

## Notes
- ALL automated tests (`tests/core/channels/test_service_credentials.py`, root `conftest.py` autouse `_mock_keyring`) use `InMemoryKeyring` — real Keychain read/write/prompt behavior, cross-restart persistence, and any OS permission dialog are completely unverified by CI.
- Also worth eyeballing on Linux prod target (SecretService/GNOME Keyring or KDE Wallet) if available, since the container D-Bus mount path (`docs/secrets.md` Keyring Backends table) is a separate untested variable.
