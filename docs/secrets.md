# Secrets Manager

Secure credential storage for integration adapters using OS-native keychains.

## Architecture

```mermaid
graph TD
    UI[Settings Page / Onboarding] -->|PUT /api/integrations/{name}/credentials| API[REST Endpoints]
    API -->|aset_secret| Secrets[shared/secrets.py]
    Secrets -->|keyring.set_password| Keyring[OS Keychain]
    API -->|reconfigure| Registry[IntegrationRegistry]
    Registry -->|get_all_secrets| Secrets
    Registry -->|instantiate| Adapters[Integration Adapters]
    Adapters -->|credentials_schema| Schema[CredentialSchema]
```

## How It Works

1. Each `Integration` adapter declares a `credentials_schema` (`CredentialSchema`) listing its credential fields with types, labels, and validation rules.
2. `shared/secrets.py` wraps the `keyring` library — all credentials stored under service name `"alfred"` with key format `"{integration}.{field}"`.
3. `IntegrationRegistry.get()` auto-populates adapter constructor kwargs from keyring when no explicit kwargs are provided.
4. REST endpoints on the web server provide CRUD operations restricted to the trusted network (localhost + Tailscale CGNAT).
5. The frontend settings page and onboarding wizard render credential forms dynamically from the adapter schemas.

## Credential Fields

| Adapter | Fields | Notes |
|---------|--------|-------|
| `apple_calendar` | `caldav_url`, `username`, `password` | App-specific password required |
| `robinhood` | `username`, `password`, `mfa_code` (transient) | MFA not persisted |
| `weather` | none | Open-Meteo is keyless |
| `apple_health` | none | Local endpoint, no auth |

## Keyring Backends

| Environment | Backend | Notes |
|-------------|---------|-------|
| macOS (dev) | Keychain Access | Automatic |
| Linux (prod) | SecretService | GNOME Keyring / KDE Wallet |
| Containers | Host D-Bus mount | `-v /run/user/1000/bus:/run/user/1000/bus` + `DBUS_SESSION_BUS_ADDRESS` env var |
| Tests | In-memory mock | `keyring.set_keyring()` |

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/integrations` | none | List integrations with schema + configured status |
| PUT | `/api/integrations/{name}/credentials` | trusted network | Save credentials to keyring |
| DELETE | `/api/integrations/{name}/credentials` | trusted network | Clear credentials |
| GET | `/api/integrations/{name}/status` | none | Run health check |

## Security

- Credential values are never returned in GET responses — only boolean configured status
- PUT/DELETE endpoints restricted to the trusted network via `Depends(require_trusted_network)` (localhost + Tailscale CGNAT `100.64.0.0/10`)
- Password fields are never pre-filled in the UI
- Transient fields (e.g. MFA codes) are passed to the adapter but not persisted

## Sovereign Service Credentials (kind=service)

Integration adapters run in-process; sovereign services (home-service,
signal-bridge, ...) are separate processes that declare credential needs at
registration time via the SDK:

- `AlfredClient(credentials_schema=..., credentials_endpoint=...)` embeds a
  `CredentialSchema` (field shape identical to `core/integrations/base.py`,
  guarded by `sdk/tests/test_schema_compatibility.py`) and an absolute
  `credentials_endpoint` URL in the `alfred:tool_registry` manifest.
- `AlfredClient.register()` publishes a `ServiceRegistered` event to
  `alfred:events` AFTER the registry hset.

Core stays the single credential authority (`core/channels/service_credentials.py`):

- `GET /api/integrations` merges adapters (`"kind": "adapter"`) with
  registry-declared services (`"kind": "service"`, `category` = `"service"`);
  the schema-driven `IntegrationCard` renders both with no special-casing.
- `PUT /api/integrations/{name}/credentials` (service, trusted network):
  validate against the registry schema → store non-transient fields in the OS
  keyring (namespace = service name) → POST the flat field dict to the
  service's `credentials_endpoint`. Push failure → HTTP 502, but the keyring
  write persists and is re-pushed on the service's next registration.
- `GET /api/integrations/{name}/status` (service) proxies the service's
  `/health`. Healthy iff HTTP 200, top-level `status == "ok"`, and every
  nested component dict with a `"state"` key reports `"connected"`. The
  `/health` URL is resolved via `urljoin(endpoint, "/health")` against the
  service's registered endpoint host — services MUST expose `/health` at the
  root of that host (not under a sub-path) for the status proxy to work.
- Self-healing re-push: the channels process consumes `ServiceRegistered`
  from `alfred:events` (consumer group `channels-credentials`) and re-pushes
  stored credentials — services keep credentials in memory only and recover
  automatically on restart. Event-driven; no polling.

### Operational notes

- **First-deploy replay:** the `channels-credentials` consumer group is
  created at stream id `0` (not `$`), so on first deploy the worker replays
  the *entire* existing `alfred:events` history, not just events published
  after it starts. This is intentional — any `ServiceRegistered` events
  already on the stream get a credential push on first boot instead of
  waiting for the next real registration. The re-push is idempotent (it's
  just a POST of the current keyring contents to `credentials_endpoint`), so
  replaying old `ServiceRegistered` entries is safe.
