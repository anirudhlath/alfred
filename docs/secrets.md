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
4. REST endpoints on the web server provide CRUD operations restricted to localhost.
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
| PUT | `/api/integrations/{name}/credentials` | localhost | Save credentials to keyring |
| DELETE | `/api/integrations/{name}/credentials` | localhost | Clear credentials |
| GET | `/api/integrations/{name}/status` | none | Run health check |

## Security

- Credential values are never returned in GET responses — only boolean configured status
- PUT/DELETE endpoints restricted to localhost via `Depends(require_localhost)`
- Password fields are never pre-filled in the UI
- Transient fields (e.g. MFA codes) are passed to the adapter but not persisted
