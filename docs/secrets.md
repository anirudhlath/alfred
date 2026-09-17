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
4. REST endpoints on the web server provide CRUD operations: the writes are restricted to a trusted network *and* an authenticated passkey session, the reads to a session only (see Security below).
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
| GET | `/api/integrations` | session | List integrations with schema + configured status |
| PUT | `/api/integrations/{name}/credentials` | session + trusted network | Save credentials to keyring |
| DELETE | `/api/integrations/{name}/credentials` | session + trusted network | Clear credentials |
| GET | `/api/integrations/{name}/status` | session | Run health check |

`GET /api/integrations/{name}/status` answers for both kinds. For an **adapter** it
calls the in-process `health_check()`; for a **service** it proxies `/health` (below).
Either way the response carries `latency_ms` — wall-clock milliseconds around the probe
alone, rounded to one decimal. On the adapter path `IntegrationRegistry.get()` runs
*before* the clock starts, so the first call after a boot or a reconfigure is not billed
for constructing the adapter and reading the keyring; a construction that fails reports
`healthy: false` with `latency_ms: null`, since nothing was probed. A probe that raises
is reported as unhealthy rather than 500ing — on the service path with the error under
`detail.error` (an unexpected exception type is logged as well, then reported the same
way), on the adapter path as a bare `healthy: false`.

## Security

- Credential values are never returned in GET responses — only boolean configured status
- PUT/DELETE endpoints are double-gated: `Depends(require_trusted_network)` **and**
  `Depends(require_authenticated)` (the `alfred_auth` passkey session cookie) — credential
  writes are credential-equivalent, and "on the LAN" is not an identity on an
  internet-facing host. Both gates come from one shared `_CREDENTIAL_GATES` list, so a new
  credential-equivalent route cannot pick up half the pair. The network gate runs first,
  so an anonymous caller from an untrusted network gets 403 without the session ever being
  consulted (401-vs-403 would otherwise leak whether a stolen cookie is still live)
- That 403 names only the observed peer IP when the caller is anonymous. The operator
  guidance — the `ALFRED_TRUSTED_NETWORKS` env-var name, an example CIDR, the Tailscale
  hint — is appended only for an authenticated caller, so a stranger cannot read the
  perimeter's configuration out of an error body
- The default trusted set is `_LAN_DEFAULT_RANGES` + Tailscale CGNAT `100.64.0.0/10`:
  `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`
  (link-local), `::1/128`, `fc00::/7` (IPv6 ULA), `fe80::/10` (IPv6 link-local).
  `ALFRED_TRUSTED_NETWORKS` (comma-separated CIDRs) *extends* that set;
  `ALFRED_TRUSTED_NETWORKS_STRICT=1` *drops the LAN defaults*, leaving loopback +
  Tailscale + whatever you listed — set it whenever the host is exposed to the internet,
  or every RFC1918 peer a reverse proxy can present counts as trusted
- The two reads (`GET /api/integrations`, `GET .../status`) require a session but are
  deliberately NOT network-gated — the PWA reads them from the public host. They disclose
  every `credentials_schema` and per-field `configured` map, plus proxied `/health` payloads
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
  registry-declared services (`"kind": "service"`, `category` = `"service"`), and any
  client is expected to render both from the declared schema with no special-casing.
  PWA phase 1 has no integrations screen: the only credential surface is the setup
  gate's second step (`web/src/gates/SetupGate.tsx`), which writes home-service and
  nothing else. The Workshop that browses the full list is phase 2.
- `PUT /api/integrations/{name}/credentials` (service, session + trusted network):
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
  The response also carries `latency_ms`: wall-clock milliseconds around the
  probe alone (the manifest lookup is excluded), rounded to one decimal, and
  `null` when the manifest declares no usable endpoint — there was nothing to
  probe, not a zero-cost probe. A probe that fails still reports the elapsed
  time, with the error under `detail.error`.
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
