# PWA Phase 5, slice 1 — Web Push

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spec §8 phase 5's first slice: *Alfred reaches the phone when the app is closed*. The house encrypts and pushes every IMPORTANT and URGENT notification to each registered PWA; the installed app subscribes from the Workshop's System › Reach card through the install and standalone gates, re-checks its subscription on every launch, and a tap on a notification opens the approval it names.

**Architecture:** A new `WebPushChannelAdapter` in the channels process (beside APNs) reads the shared `alfred:push:devices` hash, encrypts each payload with RFC 8291 `aes128gcm` (`http-ece`) and signs an RFC 8292 VAPID JWT (`PyJWT`, already a dependency) with a P-256 key the keyring holds. `POST /api/devices/register` takes a payload discriminated on `platform`, and both device routes drop to the session gate (spec §6.2). On the client, `lib/push.ts` owns the browser APIs, one react-query query keeps the subscription registered (it runs on launch and after every sign-in), the hand-written `sw.js` gains `push` and `notificationclick`, and the System bench gains the Reach card with the two gates behind it.

**Tech Stack:** Python 3.13, FastAPI, redis-py asyncio, httpx (`http2`), PyJWT ES256, `cryptography`, **new: `http-ece`**. Vite 8 · React 19 · TypeScript strict · TanStack Query 5 · react-router 7 · Vitest + Testing Library. No new npm dependencies.

**Spec:** `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md` — §6.1–§6.4 (Web Push), §8 (phase 5 row).
**Design handoff:** `docs/design/2026-09-04-pwa-client-handoff/` — `README.md:12` (copy is final, used verbatim), `README.md:199` (Reach), `README.md:206-208` (gate frame, install, standalone), `Alfred.dc.html:385-398` (Reach card markup), `:463-488` (gate frame and the Share pointer), `:664-670` (gate copy and step rings), `:775` (the four Reach states).
**Predecessors:** the phase 1–4 plans in `docs/superpowers/plans/`. Everything they export is used here exactly as spelled there.

---

## Product decisions already taken (do not reopen)

1. **Two slices** (owner, 2026-10-29). This plan is slice 1: VAPID, the adapter, registration, the worker's `push` and `notificationclick`, the System › Reach card, and the install and standalone gates. **Slice 2** is the Room's home-screen prompt row (`Alfred.dc.html:751`, README `:143`), the allowed/declined Act rows (`:752`) and the icon badge (`navigator.setAppBadge`, spec §6.3). Nothing in slice 1 builds them.
2. **Session-only gate on device registration** (owner, 2026-10-29: "Session only (spec §6.2)"). Both `POST` and `DELETE /api/devices/register` keep `require_authenticated` and lose `require_trusted_network`. Accepted trade: a stolen session cookie can register a stranger's endpoint and receive the text of important and urgent notifications. The route is shared, so the APNs payload moves with it; spec §6.2 says the route moves, not one platform.
3. **The payload is encrypted end-to-end** (spec §6.1): Apple's push relay never sees notification text. The PRD row says so.
4. **The worker caches nothing new.** `push` and `notificationclick` read no cache and fetch nothing; rule 1 of `sw.js`'s header still holds unchanged.
5. **Push is offered only in the installed app** (standalone display mode). A browser tab sees the install state. Desktop wording waits for phase 6.

### Rulings this plan makes (flag them in review)

| # | Ruling | Why | Cost if wrong |
|---|---|---|---|
| R1 | `http-ece` + `PyJWT` directly, **not `pywebpush`** | Spec §6.1 names `http-ece` and `cryptography` as the new deps; `pywebpush` would also drag `aiohttp` and `requests` into base, and its sender is sync. Verified 2026-10-29: `http_ece.encrypt(..., version="aes128gcm")` round-trips through `http_ece.decrypt`, and a PyJWT ES256 token carries the claims | Swap the two helpers in `webpush.py` for `pywebpush.WebPusher(...).encode` |
| R2 | Endpoint hosts are **allowlisted** (`PUSH_SERVICE_HOSTS`) | Without it, any session can make the house POST to an arbitrary URL on its LAN (SSRF), which drops to a session gate in decision 2 | A new browser's push service is refused with 422 until its host is added |
| R3 | At most **10** Web Push rows; the 11th evicts the oldest by `registered_at` | Caps the fan-out a stolen cookie can buy without locking a reinstalled phone out behind its own dead rows | A household with 11 PWAs loses its oldest |
| R4 | `WEBPUSH_VAPID_SUBJECT` env (a `mailto:` or `https:` URI) is required; unset means no adapter and a 404 on the public-key route | RFC 8292 `sub`; Apple refuses a token without a valid one, and the repo is public so no default address can ship | The owner adds one line to `~/code/alfred-deploy/.env` before the push works |
| R5 | The VAPID private key is generated on the first start that has a subject and stored in the keyring; a read error never generates | Spec §6.1. Generating over an unreadable keyring would orphan every subscription | Losing the keyring orphans every subscription; launch re-validation (R7) heals each phone at its next launch |
| R6 | The worker builds the deep link itself from `action_id` (`/actions/<id>` when it matches `^[A-Za-z0-9_-]{1,128}$`, else `/`) and hands it to an open window by `postMessage({type: "OPEN_PATH", path})`, opening one with `clients.openWindow(path)` only when none is open | The server never names a URL the worker opens. `postMessage` keeps the running app's sockets and state, and works on a window the worker does not control, where `WindowClient.navigate()` is refused | One more message type for the page to handle |
| R7 | Launch re-validation is a react-query query, `["push", "subscription"]`, with `staleTime: Infinity` | It must run on launch *and* after a sign-in through the Expired gate, which leaves the Room mounted and calls `invalidateQueries()` | A sign-in that does not invalidate leaves the subscription unregistered until the next launch |
| R8 | Signing out on this device `DELETE`s the server row, then unsubscribes, then logs out | A signed-out phone must stop showing the house's notifications on its lock screen; the `DELETE` needs the session, so it goes first | Push needs re-enabling after a sign-in only if permission was revoked; otherwise R7 resubscribes |
| R9 | No 115 px inset under the install gate | The handoff's 115 px is its mock Safari toolbar. A real page's layout viewport already ends at Safari's chrome; the pointer sits at `calc(4px + env(safe-area-inset-bottom, 0px))` | The phase-5 checklist measures it on the phone |

### Deviations from the handoff (deliberate)

| # | Where | Handoff | This plan ships | Why |
|---|---|---|---|---|
| D1 | Reach, standalone with no push API | — | sub `unavailable here · this browser cannot receive push`, state `off`, muted dot | An installed app on a browser without `PushManager`. The handoff draws only iOS 16.4+ |
| D2 | Reach, the house has no Web Push | — | sub `unavailable · Web Push is not set up on the house`, state `off`, muted dot | R4's 404. `Enable` would be a control that cannot work |
| D3 | Reach, permission granted, sync in flight | — | sub `checking · allowed in iOS · registering with the house`, state `—`, muted dot | `on` is a claim the house has the row; until the sync lands it is unproven |
| D4 | Reach, permission granted, sync failed | — | sub `off · allowed in iOS · the house did not take the registration`, action `Try again`, and the error in the note row | Same reason: `on` would lie |
| D5 | Standalone gate step meta | `alfred.local`, `07:02` | `window.location.hostname` (as `SignInGate` does), and the current session's `created_at` as `hhmm()`, omitted until the sessions read lands | Sample data, not copy |
| D6 | Install gate bottom | sheet stops 115 px above the bottom | full height (R9) | R9 |

## Global Constraints

- Public repo: placeholders only (`alfred.example.com`, `192.168.1.x`, `203.0.113.x`, `mailto:you@example.com`). Never a real address, hostname or IP.
- Never quote the secret-hygiene grep literals in a committed file; they live in `~/code/alfred-deploy/PWA-EXPOSURE-RUNBOOK.md` §"Secret hygiene". Run both over the added lines before any push.
- `supported_urgencies` = IMPORTANT + URGENT; INFORMATIONAL never pushes (spec §6.1).
- `Urgency` header: IMPORTANT → `normal`, URGENT → `high`. `TTL`: URGENT `3600`, IMPORTANT `86400` (spec §6.1).
- Prune a subscription on `404` or `410` from the push service, and only on those (spec §6.1).
- Storage: hash `alfred:push:devices` (`DEVICE_TOKENS_KEY`); field = SHA-256 hex of the endpoint; value = `{platform: "web_push", endpoint, keys: {p256dh, auth}, identity, registered_at, user_agent}` (spec §6.2).
- An endpoint, a `p256dh` or an `auth` value is a credential: never logged, never returned by any route. Log the field's first 12 characters and the push host.
- `GET /api/push/public-key` is unauthenticated (spec §6.1).
- Copy from the handoff is verbatim (`README.md:12`); every string this plan invents is in the Deviations table.
- Colours from tokens only; `aria-disabled`, never `disabled`; dim by token, never by `opacity`; no new npm dependency.
- **Every `sw.js` edit lands in Task 4, in one commit** (issue #406 §2): any byte change installs a new worker on every phone.
- A test file that loads `public/sw.js` declares `const FIRED =` and guards its suites with `.skipIf(FIRED)` (`src/test/sw.test.ts:38`, enforced at `:81-90`).
- Python: `mypy --strict`, ruff line length 100, loguru, `AioRedis` from `shared.types`, stream and key constants from `shared.streams`. Worktree venv: `uv venv --python 3.13 && uv sync --all-extras`.
- Frontend gate: `cd web && npm run lint && npm run test && npm run build`. Backend gate: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict alfred_cli/ alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/ && uv run pytest -x -q`. During a task run only its own test files; run the full gate once per task before the commit.
- Commits are conventional (`feat(push): …`, `test(sw): …`, `docs(push): …`); no model identifier anywhere.

## Review Focus

1. **A key rotated under live subscriptions.** The keyring is lost or the key is regenerated: every stored subscription carries the old `applicationServerKey`. Expected: at its next launch each phone sees the mismatch, `DELETE`s its old row, unsubscribes and subscribes again (Task 5 test `resubscribes when the house key changed`).
2. **A push the worker cannot read.** No data, non-JSON data, or JSON without a title. iOS revokes a subscription that receives pushes it does not show (`userVisibleOnly`). Expected: a notification titled `Alfred` is shown every time (Task 4 tests `shows a notification for a push with no data` and `… that is not JSON`).
3. **A notification body too long for the push service.** A push service refuses a body over 4096 bytes with 413, and the notification is lost. Expected: the body is shortened with `…` until the plaintext is at most `MAX_PAYLOAD_BYTES` (Task 2 test `trims a long body to fit the push service`).
4. **A subscription a session should not be able to plant.** An endpoint on a LAN host, plain `http`, a userinfo or port, or a `p256dh` that is not a P-256 point. Expected: 422 at registration, and the house never POSTs to it (Task 1 tests on `WebPushSubscription`; Task 3 test `refuses an endpoint off the push services`).
5. **A tap on a notification while the app is open but on a gate, or not controlled by the worker.** Expected: the window is focused and told the path; the app routes there whatever gate it was on, and `/actions/<id>` opens the approval once signed in (Task 4 test `posts the path to an uncontrolled window`; Task 6 test `routes an OPEN_PATH message`).

---

## File Structure

**Backend**
- Create `core/notifications/devices.py` — the vocabulary of the device hash: platforms, the Web Push subscription model and its endpoint allowlist, the field hash, row decoding. One place, read by both adapters, both routes and the admin read.
- Create `core/notifications/vapid.py` — the VAPID key (generate, store, load), its public form, and the signed `Authorization` value.
- Create `core/notifications/adapters/webpush.py` — `WebPushChannelAdapter`: payload, encryption, headers, delivery, pruning.
- Modify `core/notifications/adapters/apns.py:124-180` — skip Web Push rows.
- Modify `core/channels/web_server.py` — device models (`:287-298`), the gate comment and lists (`:606-611`), `_init_webpush_adapter` (beside `_init_apns_adapter`, `:399-430`), the lifespan call and teardown (`:463-466`, `:537`, `:545`), the device routes (`:1112-1143`), and `GET /api/push/public-key`.
- Modify `core/channels/admin_api.py:448-464` — redact Web Push rows.
- Modify `pyproject.toml`, `uv.lock` — `http-ece>=1.2.1`, `cryptography>=46` (imported directly now).
- Create `docs/web-push.md`; modify `docs/notifications.md`, `docs/admin-api.md`, `docs/architecture.md`, `CLAUDE.md`, `core/CLAUDE.md`, `.env.example`.

**Frontend**
- Modify `web/public/sw.js` — `push`, `notificationclick`.
- Modify `web/src/test/sw-harness.ts` (+ `sw-harness.test.ts`) — the push and click levers.
- Create `web/src/test/sw-push.test.ts`.
- Create `web/src/lib/push.ts` (+ test) — every browser push API call, and the two routes.
- Create `web/src/shell/usePush.ts` (+ test) — the `Reach` state, the sync query, `enable`, `retry`.
- Create `web/src/shell/PushKeeper.tsx` (+ test) — `PushKeeper` (mounts the sync on launch) and `NotificationOpen` (routes `OPEN_PATH`).
- Modify `web/src/App.tsx`, `web/src/workshop/useSystem.ts:620-645`.
- Modify `web/src/gates/Gate.tsx` (`pointer` prop), `web/src/index.css` (`nudge`); create `web/src/gates/InstallGate.tsx`, `StandaloneGate.tsx` (+ tests); add a pointer story to `web/design-system/previews/Gate.tsx`.
- Create `web/src/workshop/ReachSection.tsx` (+ test); modify `SystemBench.tsx:716-721`, `SystemBench.test.tsx:968-981`.
- Modify `docs/web-frontend.md`, `web/README.md`, `docs/superpowers/qa/2026-09-16-pwa-phase4-ios-checklist.md:315-317`, `docs/PRD.md`, `evals/coverage.yaml`; create `docs/superpowers/qa/2026-10-29-pwa-phase5-ios-checklist.md`.

## Contract between tasks

```text
devices.py   WEB_PUSH_PLATFORM, APNS_PLATFORMS, PUSH_SERVICE_HOSTS, MAX_ENDPOINT_LEN,
             MAX_WEB_PUSH_SUBSCRIPTIONS, WebPushKeys, WebPushSubscription,
             endpoint_field(), decode_device(), is_web_push(), push_host()
vapid.py     VAPID_SUBJECT_ENV, VapidKeys(.public_key, .authorization()), generate_private_pem(),
             load_keys(), ensure_vapid_keys(), vapid_subject()
webpush.py   WebPushChannelAdapter(name="webpush"), build_payload(), MAX_PAYLOAD_BYTES
routes       POST/DELETE /api/devices/register (session only), GET /api/push/public-key
sw.js        push → showNotification(title, {body, tag, data: {path}})
             notificationclick → focus + postMessage({type: "OPEN_PATH", path}) | openWindow(path)
push.ts      PUSH_KEY_PATH, DEVICES_PATH, reachContext(), permission(), fetchPublicKey(),
             syncSubscription(), requestAndSubscribe(), forgetPush(), onOpenPath()
usePush.ts   PUSH_QUERY_KEY, type Reach, usePush() → { reach, asking, enable, retry }
```

---

### Task 1: The device vocabulary and the VAPID key

**Files:**
- Create: `core/notifications/devices.py`, `core/notifications/vapid.py`
- Modify: `pyproject.toml` (`[project.dependencies]`), `uv.lock`
- Test: `tests/core/notifications/test_devices.py`, `tests/core/notifications/test_vapid.py`

**Interfaces:**
- Consumes: `shared.secrets.aget_secret` / `aset_secret` (`shared/secrets.py:245`, `:250`).
- Produces (`devices.py`):
  - `WEB_PUSH_PLATFORM: Final = "web_push"`; `APNS_PLATFORMS: Final = frozenset({"ios", "ipados", "macos"})`
  - `PUSH_SERVICE_HOSTS: Final = ("push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com")` — a host matches when it equals one or ends with `"." + one`
  - `MAX_ENDPOINT_LEN: Final = 2048`; `MAX_WEB_PUSH_SUBSCRIPTIONS: Final = 10`
  - `class WebPushKeys(BaseModel)`: `p256dh: str`, `auth: str` — base64url, `=` padding accepted and stripped; `p256dh` must decode to a valid uncompressed P-256 point (`ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)`), `auth` to 16 bytes
  - `class WebPushSubscription(BaseModel)`: `endpoint: str`, `keys: WebPushKeys`; `model_config = ConfigDict(extra="ignore")` (a browser's `toJSON()` adds `expirationTime`). The endpoint must parse as `https`, have no userinfo, no port other than 443, at most `MAX_ENDPOINT_LEN` characters, and a host in `PUSH_SERVICE_HOSTS`
  - `endpoint_field(endpoint: str) -> str` — SHA-256 hex of the UTF-8 endpoint
  - `decode_device(value: bytes | str) -> dict[str, Any] | None` — the row as a dict, `None` for anything that is not a JSON object
  - `is_web_push(row: dict[str, Any] | None) -> bool`
  - `push_host(endpoint: str) -> str` — the endpoint's hostname
- Produces (`vapid.py`):
  - `VAPID_SUBJECT_ENV: Final = "WEBPUSH_VAPID_SUBJECT"`; `VAPID_SECRET: Final = ("webpush", "vapid_private_key")`; `VAPID_TOKEN_LIFETIME_S: Final = 43_200`
  - `vapid_subject() -> str | None` — the env value stripped, `None` when empty or when it starts with neither `mailto:` nor `https:` (log a WARNING naming the variable, never its value, in that case)
  - `generate_private_pem() -> str` — a new P-256 key, PKCS#8 PEM
  - `@dataclass(frozen=True) class VapidKeys`: `private_pem: str`, `public_key: str` (unpadded base64url X9.62 uncompressed point, 87 characters); `authorization(self, endpoint: str, subject: str, now: float) -> str` returns `f"vapid t={jwt}, k={self.public_key}"`, the JWT ES256 with claims `aud` = the endpoint's `scheme://host`, `exp` = `int(now) + VAPID_TOKEN_LIFETIME_S`, `sub` = `subject`
  - `load_keys(pem: str) -> VapidKeys` — accepts escaped `\\n` as APNs does (`apns.py:52`)
  - `async ensure_vapid_keys() -> VapidKeys` — reads `VAPID_SECRET`; when absent, generates, stores, logs INFO `Generated a VAPID key`; a read that raises propagates and nothing is generated (R5)

- [ ] **Step 1: Add the dependencies.** `uv add "http-ece>=1.2.1" "cryptography>=46"`. Expected: both under `[project.dependencies]`, `uv.lock` updated, `uv run python -c "import http_ece"` succeeds.

- [ ] **Step 2: Write the failing tests** in `tests/core/notifications/test_devices.py`:

```python
APPLE = "https://web.push.apple.com/QGuQyavXutnMH8"
P256DH = <a real key: b64url of ec.generate_private_key(SECP256R1()).public_key() X962 uncompressed, unpadded>
AUTH = <b64url of os.urandom(16), unpadded>

def test_accepts_each_push_service(): for host in ("web.push.apple.com", "fcm.googleapis.com",
    "updates.push.services.mozilla.com", "wns2-par02p.notify.windows.com"): WebPushSubscription(...) validates
def test_ignores_expiration_time(): model_validate({"endpoint": APPLE, "expirationTime": None, "keys": {...}})
@pytest.mark.parametrize("endpoint", ["http://web.push.apple.com/x", "https://192.168.1.20/x",
    "https://evilpush.apple.com.example.com/x", "https://notpush.apple.com/x",
    "https://user@web.push.apple.com/x", "https://web.push.apple.com:8443/x",
    "https://web.push.apple.com/" + "a" * 2048])
def test_refuses_an_endpoint_off_the_push_services(endpoint): raises ValidationError
def test_strips_padding(): keys with "=" / "==" validate and come back unpadded
def test_refuses_a_p256dh_that_is_not_a_point(): 65 bytes starting 0x04 but off the curve → ValidationError
def test_refuses_an_auth_of_the_wrong_length(): 15 bytes → ValidationError
def test_endpoint_field_is_sha256_hex(): == hashlib.sha256(APPLE.encode()).hexdigest()
def test_decode_device_tolerates_garbage(): b"not json" → None; b"[1]" → None; b'{"platform":"ios"}' → {...}
def test_is_web_push(): {"platform": "web_push"} True; {"platform": "ios"} False; None False
```

Note `notpush.apple.com` must be refused: the suffix match is on a label boundary, so `push.apple.com` matches `web.push.apple.com` but not `notpush.apple.com`.

And `tests/core/notifications/test_vapid.py`:

```python
def test_public_key_is_87_char_b64url_point(): len == 87, decodes to 65 bytes starting 0x04
def test_authorization_verifies(): header starts "vapid t=", ends f", k={keys.public_key}";
    jwt.decode(token, public_key, algorithms=["ES256"], audience="https://web.push.apple.com")
    has sub == "mailto:you@example.com" and exp == int(now) + 43_200
def test_audience_drops_the_path(): endpoint "https://fcm.googleapis.com/fcm/send/abc" → aud "https://fcm.googleapis.com"
def test_load_keys_accepts_escaped_newlines()
async def test_ensure_generates_once(): first call stores a PEM in the (InMemoryKeyring) keyring; second returns the same public_key
async def test_ensure_never_generates_over_an_unreadable_keyring(monkeypatch): aget_secret raises → the error propagates, aset_secret never called
@pytest.mark.parametrize("value, expected", [("", None), ("you@example.com", None),
    ("mailto:you@example.com", "mailto:you@example.com"), (" https://alfred.example.com ", "https://alfred.example.com")])
def test_vapid_subject(monkeypatch, value, expected)
```

- [ ] **Step 3: Run them to see them fail.** `uv run pytest tests/core/notifications/test_devices.py tests/core/notifications/test_vapid.py -q`. Expected: `ModuleNotFoundError: core.notifications.devices`.

- [ ] **Step 4: Implement `devices.py` and `vapid.py`** to the Interfaces above. Field validators on the models; `urllib.parse.urlsplit` for the endpoint; `cryptography` for the key work; `jwt.encode(..., algorithm="ES256")` for the token.

- [ ] **Step 5: Run the two files.** Expected: all pass. Then the full backend gate.

- [ ] **Step 6: Commit.** `git add pyproject.toml uv.lock core/notifications/devices.py core/notifications/vapid.py tests/core/notifications/test_devices.py tests/core/notifications/test_vapid.py && git commit -m "feat(push): device vocabulary and VAPID key"`

---

### Task 2: `WebPushChannelAdapter`, and APNs leaves Web Push rows alone

**Files:**
- Create: `core/notifications/adapters/webpush.py`
- Modify: `core/notifications/adapters/apns.py:124-180`
- Test: `tests/core/notifications/test_webpush_adapter.py`, `tests/core/notifications/test_apns_adapter.py`

**Interfaces:**
- Consumes: Task 1's `devices.py` and `VapidKeys`; `ChannelAdapter`, `ChannelRegistry` (`core/notifications/channels.py:14`, `:30`); `Notification`, `Urgency` (`schema.py:13-34`); `DEVICE_TOKENS_KEY`.
- Produces:
  - `@ChannelRegistry.register() class WebPushChannelAdapter(ChannelAdapter)`: `name = "webpush"`, `supported_urgencies = {Urgency.IMPORTANT, Urgency.URGENT}`
  - `__init__(self, redis: AioRedis, keys: VapidKeys, subject: str, client: httpx.AsyncClient | None = None)` — a lazy `httpx.AsyncClient(http2=True, timeout=10.0)` when none is given, as APNs does (`apns.py:61-67`)
  - `async deliver(self, notification: Notification) -> None`; `async close(self) -> None` (closes only a client it created)
  - `URGENCY_HEADER: Final = {Urgency.IMPORTANT: "normal", Urgency.URGENT: "high"}`; `TTL_SECONDS: Final = {Urgency.URGENT: 3600, Urgency.IMPORTANT: 86400}`; `PRUNE_STATUSES: Final = frozenset({404, 410})`; `MAX_PAYLOAD_BYTES: Final = 3072`
  - `build_payload(notification: Notification) -> bytes` — compact JSON `{"title", "body", "urgency", "id", "action_id"}`; `urgency` is the enum value, `id` the `notification_id`, `action_id` is `metadata["pending_action_id"]` when it is a `str`, else `null`. Over `MAX_PAYLOAD_BYTES`, the body is shortened and ends in `…` until the encoding fits
  - `encrypt(plaintext: bytes, p256dh: str, auth: str) -> bytes` — a fresh ephemeral P-256 key and a 16-byte random salt per call; `http_ece.encrypt(plaintext, salt=..., private_key=..., dh=<decoded p256dh>, auth_secret=<decoded auth>, version="aes128gcm")`

Each send POSTs the encrypted body to the endpoint with headers `Authorization: keys.authorization(endpoint, subject, time.time())`, `Content-Encoding: aes128gcm`, `Content-Type: application/octet-stream`, `TTL`, `Urgency`. A 2xx is delivered (INFO, field prefix and host); 404/410 `hdel`s the field (INFO `Pruned stale web push subscription {field[:12]}… ({host})`); any other status is WARNING with the status and at most 200 characters of the response text; an exception is ERROR. Sends are gathered; one failure never stops another. Rows that are not Web Push, or that `decode_device` cannot read, are skipped.

- [ ] **Step 1: Write the failing tests** in `test_webpush_adapter.py`. Use `fakeredis.aioredis.FakeRedis` for the hash and `httpx.MockTransport` for the push service; build each subscription from a real receiver key so the test can decrypt with `http_ece.decrypt(body, private_key=ua_key, auth_secret=auth, version="aes128gcm")`.

```python
async def test_delivers_an_urgent_notification(): one row → one POST to its endpoint;
    headers Urgency "high", TTL "3600", Content-Encoding "aes128gcm";
    Authorization verifies against keys.public_key with aud "https://web.push.apple.com";
    decrypted JSON == {"title": ..., "body": ..., "urgency": "urgent", "id": n.notification_id, "action_id": None}
async def test_important_is_normal_for_a_day(): Urgency "normal", TTL "86400"
def test_informational_is_not_supported(): not WebPushChannelAdapter.supports_urgency(Urgency.INFORMATIONAL)
async def test_carries_the_pending_action_id(): metadata {"pending_action_id": "abc-123"} → action_id "abc-123"
async def test_ignores_a_non_string_action_id(): metadata {"pending_action_id": 7} → action_id None
async def test_trims_a_long_body_to_fit_the_push_service(): body "x" * 10_000 → len(plaintext) <= 3072, body ends "…"
@pytest.mark.parametrize("status", [404, 410])
async def test_prunes_a_dead_subscription(status): field gone from DEVICE_TOKENS_KEY afterwards
@pytest.mark.parametrize("status", [400, 403, 413, 429, 500])
async def test_keeps_the_row_on_other_failures(status)
async def test_one_failure_does_not_stop_the_rest(): two rows, transport raises for one → the other still POSTed
async def test_skips_apns_and_unreadable_rows(): rows {"platform": "ios"} and b"garbage" → no POST
async def test_never_logs_the_endpoint_or_keys(caplog/loguru sink): no log line contains the endpoint, p256dh or auth
```

And in `test_apns_adapter.py`, beside the prune test at `:116`:

```python
async def test_skips_web_push_rows(): hgetall returns an APNs token row and a web_push row →
    client.post called once, with the APNs token's URL
```

- [ ] **Step 2: Run them to see them fail.** `uv run pytest tests/core/notifications/test_webpush_adapter.py tests/core/notifications/test_apns_adapter.py -q`. Expected: import error, and the APNs test fails with two posts.

- [ ] **Step 3: Implement** `webpush.py` to the Interfaces, and in `apns.py:126-131` keep only the fields whose decoded row `is_web_push` rejects (an APNs row that is not JSON stays a token, as today).

- [ ] **Step 4: Run the two files**, then the full backend gate. Expected: pass.

- [ ] **Step 5: Commit.** `git commit -m "feat(push): Web Push channel adapter"` (with both test files and both adapters).

---

### Task 3: Registration, the public key, the admin read, startup — and the backend docs

**Files:**
- Modify: `core/channels/web_server.py` (`:287-298`, `:606-611`, `:399-430`, `:463-466`, `:537`, `:545`, `:1112-1143`), `core/channels/admin_api.py:448-464`
- Modify: `tests/core/channels/conftest.py` (`live_channels_client` also patches `_init_webpush_adapter`), `tests/core/channels/test_double_gate.py:22-32`, `tests/core/channels/test_device_registration.py`, `tests/core/channels/test_admin_api.py`
- Create: `docs/web-push.md`. Modify: `docs/notifications.md` (`:9`, `:28-34`, `:83`, `:104-120`, `:213`), `docs/admin-api.md` (`:40`, `:318-331`), `docs/architecture.md:832-848`, `CLAUDE.md` (Secrets & Credentials' device-registration line and the Gotchas line on `_CREDENTIAL_GATES`), `core/CLAUDE.md` (Notifications › adapters), `.env.example` (a Web Push block after APNs, `:117-121`)

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces:
  - Models: `DeviceRegistration` keeps its fields with `platform: Literal["ios", "ipados", "macos"]`; new `WebPushRegistration(platform: Literal["web_push"], subscription: WebPushSubscription)`; the route takes `Annotated[DeviceRegistration | WebPushRegistration, Field(discriminator="platform")]`. `DeviceUnregistration` gains `endpoint: str | None` (max `MAX_ENDPOINT_LEN`), `device_token` becomes optional, and a model validator demands exactly one of the two.
  - Both device routes: `dependencies=[Depends(require_authenticated)]`. `_CREDENTIAL_GATES` keeps its list and loses the device routes from its comment, which now says they are session-only by spec §6.2 and the owner's decision of 2026-10-29.
  - Web Push `POST`: field `endpoint_field(endpoint)`, value per Global Constraints with `identity: "sir"` (as the WebSocket handler derives it, `web_server.py:745`) and `user_agent` truncated to `_MAX_USER_AGENT_LEN` (import it from `core/identity/auth_routes.py`). When the field is new and `MAX_WEB_PUSH_SUBSCRIPTIONS` Web Push rows exist, the oldest by `registered_at` is deleted first. INFO `Registered web push subscription ({field[:12]}…, host={host})`. Answers `{"status": "ok"}`.
  - Web Push `DELETE`: `hdel(endpoint_field(endpoint))`; `{"status": "ok"}` whether or not a row existed.
  - `GET /api/push/public-key` (no dependencies): `{"public_key": app.state.vapid_public_key}`, or 404 `Web Push not configured` while it is `None`. `create_app` sets `app.state.vapid_public_key = None`.
  - `async _init_webpush_adapter(app: FastAPI, pool: AioRedis) -> None`: no subject → INFO `Web Push not configured ({VAPID_SUBJECT_ENV} unset), skipping adapter` and return; else `ensure_vapid_keys()`, import the adapter module, `ChannelRegistry.set_instance("webpush", adapter)`, set `app.state.vapid_public_key`, INFO `Web Push adapter registered`. Called in the lifespan beside APNs, in its own `try/except` that warns; teardown closes it as APNs is closed.
  - `GET /api/admin/devices`: a Web Push row becomes `{"device_token": field[:12], "platform", "identity", "registered_at", "user_agent", "push_host": push_host(endpoint)}` and nothing else; APNs rows are unchanged.

- [ ] **Step 1: Write the failing tests.**

In `test_double_gate.py`, remove both device routes from `DOUBLE_GATED` and add:

```python
@pytest.mark.parametrize(("method", "body"), [("POST", <APNs body as at :28>), ("DELETE", {"device_token": "aabb…"})])
def test_device_routes_take_a_session_from_anywhere(app, method, body):
    _client(app, signed_in=True) (untrusted peer) → 200
@pytest.mark.parametrize(...same...)
def test_device_routes_still_need_a_session(app, method, body):
    _client(app, signed_in=False, peer=("192.168.1.20", 12345)) → 401
```

In `test_device_registration.py`:

```python
def test_registers_a_web_push_subscription(): POST {"platform": "web_push", "subscription": {...}} → 200;
    hget(DEVICE_TOKENS_KEY, endpoint_field(endpoint)) == {"platform": "web_push", "endpoint", "keys", "identity": "sir",
    "registered_at": <ISO>, "user_agent": <the request's>}
def test_re_registering_replaces_the_row(): two POSTs of one endpoint → HLEN 1
def test_refuses_an_endpoint_off_the_push_services(): endpoint "https://192.168.1.20/x" → 422, HLEN 0
def test_evicts_the_oldest_past_ten(): 10 rows with ascending registered_at, then an 11th → HLEN 10, oldest field gone
def test_apns_rows_do_not_count_toward_the_cap()
def test_unregisters_by_endpoint(): DELETE {"endpoint": ...} → row gone
@pytest.mark.parametrize("body", [{}, {"device_token": "aa"*16, "endpoint": APPLE}])
def test_unregister_needs_exactly_one(body): → 422
def test_public_key_unconfigured(): GET /api/push/public-key → 404 {"detail": "Web Push not configured"}
def test_public_key_needs_no_session(): app.state.vapid_public_key = "B" * 87 → 200 {"public_key": "B"*87}, signed out
async def test_init_skips_without_a_subject(monkeypatch): env unset → no "webpush" instance, vapid_public_key None
async def test_init_registers_with_a_subject(monkeypatch): "mailto:you@example.com" → instance registered, key set
```

In `test_admin_api.py` (beside `:738-790`):

```python
def test_devices_redacts_web_push(): a web_push row → the device has no "endpoint" and no "keys",
    has push_host "web.push.apple.com" and device_token == field[:12]; the response text contains neither the endpoint nor p256dh
```

- [ ] **Step 2: Run them to see them fail.** `uv run pytest tests/core/channels/test_double_gate.py tests/core/channels/test_device_registration.py tests/core/channels/test_admin_api.py -q`. Expected: 403s on the session-only tests, 422 on the Web Push body, 404 on the key route.

- [ ] **Step 3: Implement** the route, model, startup and admin changes to the Interfaces.

- [ ] **Step 4: Run the three files**, then the full backend gate. Expected: pass.

- [ ] **Step 5: Write the docs.**
  - `docs/web-push.md`: what it is (spec §6), a mermaid sequence (phone subscribes → `POST /api/devices/register` → hash; dispatcher → `channels-delivery` → adapter → push service → phone), the row model, the key's life (R4, R5, rotation healed by launch re-validation), the allowlist and the cap (R2, R3), the urgency and TTL table, pruning, the payload contract shared with `sw.js`, and the operator step: set `WEBPUSH_VAPID_SUBJECT` in the deploy `.env`.
  - `.env.example`: `# --- Web Push (optional; the PWA's notifications) ---` then `WEBPUSH_VAPID_SUBJECT=                # mailto:you@example.com or https://alfred.example.com — unset disables Web Push`.
  - `docs/notifications.md`: Web Push in the architecture diagram, the IMPORTANT and URGENT rows of the urgency table, and the adapter list, the discriminated registration body and its session-only gate in Device Registration, the field hash in Redis Keys, a link to `docs/web-push.md`.
  - `docs/admin-api.md`: the device routes leave the double gate at `:40`; the devices read's redaction at `:318-331`.
  - `docs/architecture.md:832-848`: the adapter beside APNs.
  - `CLAUDE.md`: the device-registration line names both payloads and the session gate; the Gotchas line listing the credential-equivalent writes drops `/api/devices/register` and says why; one Gotcha for the allowlist and for never logging an endpoint. `core/CLAUDE.md`: `webpush.py` in the adapters list and the `channels-delivery` diagram.

- [ ] **Step 6: Commit.** `git commit -m "feat(push): Web Push registration, public key and admin redaction"`

---

### Task 4: The worker shows pushes and opens what they name

**Files:**
- Modify: `web/public/sw.js` (after the `message` listener, `:771-788`; and its header, `:1-29`)
- Modify: `web/src/test/sw-harness.ts` (the `Harness` interface `:440-548`, `windowClient` `:885-901`, the scope `:903-1043`, `extendable` `:1058-1064`), `web/src/test/sw-harness.test.ts`
- Create: `web/src/test/sw-push.test.ts`
- Modify: `web/README.md:165` (the worker's line count)

**Interfaces:**
- Consumes: the payload from Task 2's `build_payload`.
- Produces (the worker): on `push`, `event.waitUntil(self.registration.showNotification(title, {body, tag, data: {path}}))` — `title` is the payload's when it is a non-empty string, else `"Alfred"`; `body` the payload's string or `""`; `tag` the payload's `id` when it is a string, else omitted; `path` is `/actions/<action_id>` when `action_id` matches `/^[A-Za-z0-9_-]{1,128}$/`, else `/`. Unreadable data (none, or `json()` throws) shows the fallback. On `notificationclick`, the notification is closed, then within `waitUntil`: `self.clients.matchAll({type: "window", includeUncontrolled: true})`; the first window on `self.location.origin` is focused (a rejected `focus()` is ignored) and sent `postMessage({type: "OPEN_PATH", path})`; with none, `self.clients.openWindow(path)`. A `path` that is not one the worker would have built is replaced by `/`.
- Produces (the harness): `push(payload?: unknown, options?: {raw?: string}): Promise<void>` — no argument means `event.data === null`; `raw` hands `text()` that string and makes `json()` parse it (so a non-JSON `raw` throws); `notificationClick(data: unknown): Promise<void>`; and records `shown: {title: string; options: NotificationOptions}[]`, `closedNotifications: number`, `opened: string[]` (absolute URLs), `focused: string[]` and `posted: {url: string; data: unknown}[]` (by page URL); lever `refuseFocus(url: string)`. `extendable` takes the event's extra fields rather than only `data`.

- [ ] **Step 1: Write the failing harness tests** in `sw-harness.test.ts`: `push()` with no payload gives `data === null`; `push(obj)` gives `json()` → `obj`; `push(undefined, {raw: "x"})` gives a `json()` that throws; `notificationClick` delivers `notification.data` and counts `close()`; a window client's `focus()` and `postMessage()` are recorded, and `refuseFocus` makes `focus()` reject; `openWindow` resolves and records the absolute URL.

- [ ] **Step 2: Write the failing worker tests** in `sw-push.test.ts`, loading `public/sw.js` as `sw.test.ts` does, with `const FIRED = …` and every suite `.skipIf(FIRED)`:

```ts
it("shows the house's title and body, tagged by id")
it("deep-links a notification that names an action")              // data.path === "/actions/abc-123"
it("links home for an action id it would not route")               // "../x", "a/b", 129 chars → "/"
it("shows a notification for a push with no data")                 // title "Alfred", body ""
it("shows a notification for a push that is not JSON")
it("shows a notification for JSON with no title")
it("waits for showNotification inside waitUntil")                  // push() resolves after shown is recorded
it("focuses an open window and posts it the path")                 // focused, posted [{type: "OPEN_PATH", path}], opened []
it("posts the path to an uncontrolled window")                     // page.controller !== "this" → still focused + posted
it("posts even when focus is refused")
it("opens a window when none is open")                             // opened [origin + "/actions/abc-123"]
it("opens home for a path it did not build")                       // data {path: "https://203.0.113.9/"} → "/"
it("closes the notification it was tapped on")
it("still never answers /api/* (rule 1 is unchanged)")             // one request to /api/push/public-key → undefined
```

- [ ] **Step 3: Run them to see them fail.** `cd web && npx vitest run src/test/sw-harness.test.ts src/test/sw-push.test.ts`. Expected: the levers do not exist; no notification is shown.

- [ ] **Step 4: Implement the harness levers**, then the two listeners in `sw.js`, through `self.` only (header rule 3). Add one paragraph to the header saying the worker also shows pushes and opens what they name, caches nothing for either, and where the payload contract lives (`docs/web-push.md`). Leave `VERSION`, `NEVER` and `NEVER_UNDER` untouched.

- [ ] **Step 5: Run the whole worker suite**: `npx vitest run src/test/sw.test.ts src/test/sw-server-routes.test.ts src/test/sw-harness.test.ts src/test/sw-push.test.ts src/test/sw-kill.test.ts`. Expected: pass, and the `finds every file that reads the worker's bytes standing down` test finds `sw-push.test.ts` too. Update the line count at `web/README.md:165`. Then the frontend gate.

- [ ] **Step 6: Commit `sw.js` and its tests together.** `git commit -m "feat(sw): show pushes and open the approval they name"`

---

### Task 5: `lib/push.ts` — the browser's push APIs and the two routes

**Files:**
- Create: `web/src/lib/push.ts`, `web/src/lib/push.test.ts`

**Interfaces:**
- Consumes: `api`, `post`, `ApiError` (`lib/api.ts:111-119`); `sessionChannel() === "pwa"` (`lib/webauthn.ts:24-28`) as the standalone test, so push and sign-in never disagree about it.
- Produces:
  - `PUSH_KEY_PATH = "/api/push/public-key"`; `DEVICES_PATH = "/api/devices/register"`; `READY_TIMEOUT_MS = 10_000`
  - `type ReachContext = "tab" | "unsupported" | "capable"`; `reachContext(enabled = import.meta.env.PROD): ReachContext` — `"tab"` when not standalone; `"unsupported"` when standalone and any of `serviceWorker in navigator`, `PushManager in window`, `Notification in window` is missing, or when `enabled` is false (dev registers no worker, `lib/sw.ts:128`); else `"capable"`
  - `permission(): NotificationPermission` — `Notification.permission`, `"default"` where `Notification` is absent
  - `fetchPublicKey(): Promise<string | null>` — `null` on a 404, rethrows anything else
  - `syncSubscription(publicKey: string): Promise<void>` — `navigator.serviceWorker.ready` raced against `READY_TIMEOUT_MS` (rejects with `Error("no service worker")`); `pushManager.getSubscription()`; a subscription whose `options.applicationServerKey` differs byte-for-byte from `publicKey` is `DELETE`d at the house (best effort) and unsubscribed; with none left, `subscribe({userVisibleOnly: true, applicationServerKey: <publicKey bytes>})`; finally `post(DEVICES_PATH, {platform: "web_push", subscription: sub.toJSON()})`. A null `applicationServerKey` is kept as it is.
  - `requestAndSubscribe(): Promise<NotificationPermission>` — calls `Notification.requestPermission()` **first, before any await**, so it stays inside the tap iOS requires; on `"granted"`, `fetchPublicKey()` then `syncSubscription()` (a `null` key throws `ApiError(404, "Web Push not configured")`)
  - `forgetPush(): Promise<void>` — the current subscription, if any: `api(DEVICES_PATH, {method: "DELETE", body: JSON.stringify({endpoint})})` with its error swallowed, then `unsubscribe()`; never rejects
  - `onOpenPath(fn: (path: string) => void): () => void` — listens to `navigator.serviceWorker` `message` events and calls `fn` for `{type: "OPEN_PATH", path}` when `path` is `/` or matches `ACTION_PATH` (`door/useActionRoute.ts:9`, exported from there for this); anything else is ignored

- [ ] **Step 1: Write the failing tests** in `push.test.ts`, faking `navigator.serviceWorker` with `Object.defineProperty` as `lib/sw.test.ts:4-8` does, `PushManager`/`Notification` with `vi.stubGlobal`, `matchMedia` per test, and `fetch` with `vi.stubGlobal`:

```ts
it("is a tab outside standalone")
it("is unsupported in standalone without PushManager")
it("is unsupported in dev")                                         // reachContext(false)
it("reads a 404 key as unconfigured")                               // fetchPublicKey() → null
it("subscribes with the house key and registers")                   // subscribe called with userVisibleOnly + key bytes; POST body {platform: "web_push", subscription}
it("re-registers a subscription it already holds")                  // no subscribe; one POST
it("resubscribes when the house key changed")                       // DELETE {endpoint: old}, unsubscribe, subscribe, POST new
it("resubscribes even when the old row's DELETE fails")
it("gives up when no worker is ready")                              // fake timers; rejects "no service worker" at 10 000 ms
it("asks for permission before anything else")                      // requestPermission called before any fetch
it("does not subscribe when permission is refused")                 // "denied" → no fetch, resolves "denied"
it("forgets: DELETEs, then unsubscribes, and never rejects")        // DELETE fails → unsubscribe still called, resolves
it("hears OPEN_PATH for routes it knows and nothing else")          // "/actions/abc" and "/" heard; "https://203.0.113.9/", "/actions/a/b", {type: "X"} ignored
```

- [ ] **Step 2: Run them to see them fail.** `cd web && npx vitest run src/lib/push.test.ts`. Expected: module not found.

- [ ] **Step 3: Implement** `push.ts`, and export `ACTION_PATH` from `door/useActionRoute.ts`.

- [ ] **Step 4: Run the file**, then the frontend gate. Expected: pass.

- [ ] **Step 5: Commit.** `git commit -m "feat(web): push subscription client"`

---

### Task 6: `usePush`, the launch sync, the tap's route, and sign-out

**Files:**
- Create: `web/src/shell/usePush.ts`, `web/src/shell/usePush.test.tsx`, `web/src/shell/PushKeeper.tsx`, `web/src/shell/PushKeeper.test.tsx`
- Modify: `web/src/App.tsx:20-38`, `web/src/workshop/useSystem.ts:620-645` (+ its test)

**Interfaces:**
- Consumes: Task 5; `onVisible` (`lib/lifecycle.ts`) for re-reading the permission when the app comes back from Settings.
- Produces:
  - `PUSH_QUERY_KEY = ["push", "subscription"] as const`
  - `type Reach = {kind: "install"} | {kind: "unavailable"} | {kind: "unconfigured"} | {kind: "ask"} | {kind: "checking"} | {kind: "on"} | {kind: "denied"} | {kind: "failed"; detail: string}`
  - `usePush(): {reach: Reach; asking: boolean; enable: () => void; retry: () => void}`. The permission is read with `useSyncExternalStore(onVisible, permission)`. The sync is `useQuery({queryKey: PUSH_QUERY_KEY, queryFn, enabled: context === "capable" && permission === "granted", staleTime: Infinity, refetchOnWindowFocus: false})`, whose `queryFn` is `fetchPublicKey()` then, for a key, `syncSubscription(key)`, and resolves to `"on"` or `"unconfigured"`. `reach` is, in order: `tab` → install; `unsupported` → unavailable; permission `denied` → denied; `default` → ask; granted with the query pending → checking; resolved `"unconfigured"` → unconfigured; resolved `"on"` → on; errored → failed with `errorText(error)` (`lib/api.ts:20`). `enable()` sets `asking`, calls `requestAndSubscribe()`, then `invalidateQueries({queryKey: PUSH_QUERY_KEY})`; a 404 from it makes `reach` unconfigured; it must call `requestAndSubscribe` synchronously in the tap. `retry()` refetches the query.
  - `PushKeeper(): null` — calls `usePush()` so the sync runs on launch; rendered inside `AuthGate`, beside `DoorProvider`'s children.
  - `NotificationOpen(): null` — `onOpenPath(path => navigate(path))` in an effect; rendered inside `BrowserRouter`, outside `AuthGate`.
  - `useSystem`'s `signOut` awaits `forgetPush()` before `logoutSession()` (R8).

- [ ] **Step 1: Write the failing tests.** `usePush.test.tsx`, with `lib/push` mocked by `vi.mock` and a fresh `QueryClient` (`QUERY_DEFAULTS`):

```ts
it("is install in a tab")
it("is unavailable when the installed app has no push")
it("is ask before permission, denied after a refusal")
it("is checking while the sync is in flight, then on")
it("is failed with the error when the sync fails, and retry refetches")
it("is unconfigured when the house has no key")
it("enables: asks, then syncs, and goes on")
it("enables without awaiting before the permission request")        // requestAndSubscribe called synchronously by enable()
it("re-reads the permission when the app comes back")               // permission flips to denied, visibilitychange → denied
it("syncs again after every sign-in")                               // queryClient.invalidateQueries() → syncSubscription called twice
```

`PushKeeper.test.tsx`:

```ts
it("syncs on launch when permission is granted")
it("routes an OPEN_PATH message")                                   // MemoryRouter + NotificationOpen; posted "/actions/abc" → location.pathname "/actions/abc"
```

`useSystem.test.tsx`: `it("forgets push before signing out")` — call order `forgetPush` then the logout `POST`.

- [ ] **Step 2: Run them to see them fail.** `npx vitest run src/shell/usePush.test.tsx src/shell/PushKeeper.test.tsx src/workshop/useSystem.test.tsx`.

- [ ] **Step 3: Implement** the hook and the two components, wire them into `App.tsx`, and change `signOut`.

- [ ] **Step 4: Run the files**, then the frontend gate. Expected: pass.

- [ ] **Step 5: Commit.** `git commit -m "feat(web): keep the push subscription registered"`

---

### Task 7: The install and standalone gates

**Files:**
- Modify: `web/src/gates/Gate.tsx` (a `pointer` prop), `web/src/gates/Gate.test.tsx`, `web/src/index.css` (`@keyframes nudge`, and the reduced-motion block at `:499`)
- Create: `web/src/gates/InstallGate.tsx`, `InstallGate.test.tsx`, `StandaloneGate.tsx`, `StandaloneGate.test.tsx`
- Modify: `web/design-system/previews/Gate.tsx` (one story with the pointer)

**Interfaces:**
- Consumes: `Gate`, `StepList` (`variant: "progress"`), `hhmm` (`lib/format`).
- Produces:
  - `GateProps.pointer?: string` — the label of an accent pointer pinned at `bottom: calc(4px + env(safe-area-inset-bottom, 0px))`, centred, gap 2: the label in 500 11px mono over an 11px chevron (2px right and bottom borders, `rotate(45deg)`), `animation: nudge 1.4s ease-in-out infinite` (`Alfred.dc.html:485`); `aria-hidden`, since the steps say the same thing
  - `@keyframes nudge` — `translateY(0)` ↔ `translateY(6px)` (handoff `README.md:122`); still under `prefers-reduced-motion: reduce`
  - `InstallGate({onDismiss}: {onDismiss: () => void})` — `Alfred.dc.html:669` verbatim: kicker `reaching you · 3 of 6 · in Safari`, title `Add me to the home screen.`, the body, steps `Tap Share, below` (current) · `Scroll to "Add to Home Screen"` (todo) · `Tap Add, then open Alfred from there` (todo), no primary, secondary `Not now`, the foot, `pointer="Share"`
  - `StandaloneGate({signedInAt, busy, onAllow, onDismiss}: {signedInAt: string | null; busy: boolean; onAllow: () => void; onDismiss: () => void})` — `Alfred.dc.html:670` verbatim: kicker, title `You came in the front way.`, body, the six steps with steps 1–4 done, 5 current, 6 todo (the handoff's `gateStep: 4`); meta `Open in Safari` → `window.location.hostname`, `Sign in with Face ID` → `hhmm(signedInAt)` when given (D5), `Open from the home screen` → `now`; primary `Let me reach you` (`busy` while asking); secondary `Not yet`; the foot

- [ ] **Step 1: Write the failing tests.**

```ts
// Gate.test.tsx
it("draws no pointer unless asked")
it("draws the pointer hidden from assistive tech")                   // text "Share", closest [aria-hidden="true"]
// InstallGate.test.tsx
it("says the handoff's words")                                       // kicker, title, body, three steps, foot — exact strings
it("offers no primary, and Not now dismisses")
it("marks the first step current")                                   // data-step-state current, todo, todo
// StandaloneGate.test.tsx
it("says the handoff's words")
it("marks steps one to four done and five current")
it("names this host and the sign-in time")                           // meta hostname; "07:02"-style hhmm of signedInAt
it("leaves the sign-in time out until it is known")                  // signedInAt null → no meta on step 2
it("asks from the primary and holds it while asking")                // onAllow once; busy → aria-busy, a second press does nothing
it("Not yet dismisses")
```

- [ ] **Step 2: Run them to see them fail.** `npx vitest run src/gates/Gate.test.tsx src/gates/InstallGate.test.tsx src/gates/StandaloneGate.test.tsx`.

- [ ] **Step 3: Implement** the prop, the keyframes, the two gates and the preview story.

- [ ] **Step 4: Run the files**, `npx tsc -p design-system/tsconfig.previews.json --noEmit`, then the frontend gate. Expected: pass.

- [ ] **Step 5: Commit.** `git commit -m "feat(web): install and standalone gates"`

---

### Task 8: System › Reach

**Files:**
- Create: `web/src/workshop/ReachSection.tsx`, `web/src/workshop/ReachSection.test.tsx`
- Modify: `web/src/workshop/SystemBench.tsx:716-721` (Reach between Quiet and Sessions), `web/src/workshop/SystemBench.test.tsx:968-981` (the h3 list), `web/src/workshop/IntegrationRow.tsx:37-93` (export `WORD_COLOUR` and `StateDot`)

**Interfaces:**
- Consumes: `usePush()` (Task 6), `InstallGate`, `StandaloneGate` (Task 7), `Layer` (`level="gate"`, as `AuthGate.tsx:195-205`), `SystemSection`, `SystemRow` (`SystemFrame.tsx`), `system.sessions` for the current session's `created_at`.
- Produces: `ReachSection({sessions}: {sessions: Sessions})` — `SystemSection title="Reach"`, one row `Push notifications` (14.5) over the sub (mono 11, muted). Right side, by `reach.kind`:

| kind | sub | right |
|---|---|---|
| install | `unavailable in Safari · add to home screen first` | action `Show me how` → install gate |
| ask | `off · not yet asked · iOS asks once` | action `Enable` → standalone gate |
| on | `on · this iPhone · important and urgent only · badge = waiting approvals + unread urgent` | `on`, green dot |
| denied | `off · declined in iOS · this app cannot flip it` | `off`, accent dot; note row `Settings › Alfred › Notifications › Allow. iOS holds this switch now; nothing in here can flip it, and Alfred will not raise it again.` |
| unavailable | D1 | `off`, muted dot |
| unconfigured | D2 | `off`, muted dot |
| checking | D3 | `—`, muted dot |
| failed | D4 | action `Try again` → `retry()`; note row = `detail` |

The action is an accent 500 13px text button, 44px tall, `margin-right: -8px` (`Alfred.dc.html:390`). The state word and dot are Connected services' own, not new ones: export `WORD_COLOUR` and `StateDot` from `IntegrationRow.tsx` and map `on` → `ok`, `denied` → `failed`, every other kind → `unset` (filled `--green-text`, accent outline, muted outline; the handoff's raw green fails contrast as a dot, `IntegrationRow.tsx:64-75`). The note row is `padding 10px 0 12px`, a top `--line` border, mono 11/1.6, muted. The standalone gate's primary calls `enable()`, is busy while `asking`, and the gate closes once `asking` ends; `Not yet` and `Not now` only close their gate.

- [ ] **Step 1: Write the failing tests** in `ReachSection.test.tsx`, with `usePush` mocked per test:

```ts
it.each([...the eight kinds]) ("says %s in the handoff's words")     // exact sub, action label or state word
it("Show me how opens the install gate, and Not now closes it")
it("Enable opens the standalone gate at step five")
it("Let me reach you enables, holds while asking, and closes when done")
it("Try again retries")
it("shows the denied note only when denied")
it("shows the failure in the note row")
```

And in `SystemBench.test.tsx:968-981`, the h3 list becomes `Health, Quiet, Reach, Sessions, Connected services, Devices & identity, Reflex, Maintenance`.

- [ ] **Step 2: Run them to see them fail.** `npx vitest run src/workshop/ReachSection.test.tsx src/workshop/SystemBench.test.tsx`.

- [ ] **Step 3: Implement** `ReachSection` and mount it.

- [ ] **Step 4: Run the files**, then the frontend gate. Expected: pass.

- [ ] **Step 5: Commit.** `git commit -m "feat(web): System › Reach"`

---

### Task 9: The PRD, coverage, the frontend docs, and the phone checklist

**Files:**
- Modify: `docs/PRD.md` (date at `:3`; a new row after the install row, `:77`), `evals/coverage.yaml` (an entry after `:17`), `docs/web-frontend.md` (`:22-26`, `:1353`, `:1698-1709`, plus a Web Push section), `web/README.md` (`:122-123`, `:145-152`), `docs/superpowers/qa/2026-09-16-pwa-phase4-ios-checklist.md:315-317`
- Create: `docs/superpowers/qa/2026-10-29-pwa-phase5-ios-checklist.md`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - PRD row: `| Web app — reaches the phone when the app is closed: Web Push for important and urgent notifications, encrypted end-to-end from Alfred to the phone so Apple's push relay cannot read them; enabled from the Workshop's System › Reach through the install and standalone gates; a tap opens the approval it names | In review | docs/web-push.md, plan 2026-10-29-pwa-phase5-web-push-slice1.md |`, and the date bumped to the day of the commit.
  - Coverage entry: `- {id: 4.1.pwa-push, section: "4.1", prd: "Web app — reaches the phone when the app is closed", not_llm: "push delivery, the worker's push handlers and the Reach card", tests: [tests/core/notifications/test_webpush_adapter.py, tests/core/channels/test_device_registration.py, web/src/test/sw-push.test.ts, web/src/lib/push.test.ts, web/src/workshop/ReachSection.test.tsx]}`
  - The checklist, in the phase-4 checklist's shape (unrun, run against `https://alfred.example.com`, record device, iOS and date). Steps: set `WEBPUSH_VAPID_SUBJECT` and deploy; Safari tab → Reach reads the install state, `Show me how` opens the install gate, and **record** where the Share pointer sits against Safari's toolbar (R9); add to the home screen and open → Reach reads `off · not yet asked`; `Enable` → the gate shows `now` on step 4 and this host on step 1; `Let me reach you` → the iOS dialog; Allow → `checking` then `on`, and `GET /api/admin/devices` shows one `web_push` row with `push_host` `web.push.apple.com` and no endpoint; with the app closed, a confirmation (URGENT) and a Reflex ask (IMPORTANT) each arrive on the lock screen; tapping the confirmation opens its Door, cold and with the app in the background; an INFORMATIONAL notification does not arrive; relaunch re-registers (the row's `registered_at` moves); Settings › Alfred › Notifications off → relaunch → Reach reads `denied` with its note; sign out on this device → the row is gone and nothing more arrives; a second install on another phone → two rows.
  - The phase-4 checklist's "Nothing lies" lines (`:315-317`) say the Reach card and both gates exist now, and that the Room's prompt row and the notification permission prompt in the Room arrive in slice 2.

- [ ] **Step 1: Write the PRD row, the coverage entry, the doc edits and the checklist.**

- [ ] **Step 2: Run the coverage gate.** `uv run pytest tests/evals/test_prd_coverage.py -q`. Expected: pass, with `4.1.pwa-push` found.

- [ ] **Step 3: Run both full gates**, then both secret-hygiene greps from the runbook over `git diff origin/master -U0 | grep '^+'`. Expected: clean.

- [ ] **Step 4: Commit.** `git commit -m "docs(push): PRD row, coverage and the phase-5 phone checklist"`

---

## Self-review

- **Spec coverage.** §6.1: adapter (T2), urgencies (T2), VAPID keyring + public route (T1, T3), aes128gcm + VAPID JWT (T1, T2), Urgency + TTL (T2), prune on 404/410 (T2), PRD's end-to-end line (T9). §6.2: shared hash, field hash, row value (T3), discriminated payload (T3), session gate (T3). §6.3: `push` + `notificationclick` with the approval deep link (T4, T6), re-validation every launch (T5, T6); `setAppBadge` is slice 2 by decision 1. §6.4: the rail's install and standalone gates (T7, T8); the Room's prompt is slice 2. §8: "VAPID, adapter, registration, service-worker handlers, install-and-enable sequence" — all here except slice 2's three items.
- **Type consistency.** `endpoint_field`, `is_web_push`, `decode_device`, `push_host` are used by T2 and T3 as T1 defines them; `VapidKeys.authorization(endpoint, subject, now)` matches T2's call; `PUSH_QUERY_KEY`, `Reach`, `usePush()` match between T6 and T8; `ACTION_PATH` is exported in T5 and used by `onOpenPath`; `OPEN_PATH` is the same string in T4 and T5.
- **Review Focus.** Each of the five lines names its test in its owning task (T5, T4, T2, T1/T3, T4/T6).
