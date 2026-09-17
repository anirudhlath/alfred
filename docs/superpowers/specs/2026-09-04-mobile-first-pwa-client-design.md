# Alfred PWA Client — Design

**Status:** Approved 2026-09-04; design handoff in `docs/design/2026-09-04-pwa-client-handoff/`.
Supersedes the current `web/` Mission Control SPA.
**Date:** 2026-09-04
**Decisions taken:** replace `web/` entirely · internet-facing via nginx-proxy-manager ·
full-parity debug surface, mobile-native.

---

## 1. What we are building

One responsive client, iPhone-first, that replaces `web/`. It installs to the iOS
homescreen and behaves as a native app. It carries two personas in one shell:

- **The butler** — chat, voice, notifications, critical-action confirmations. 95% of use.
- **The engine room** — everything the admin API exposes, redesigned as mobile surfaces
  rather than shrunken desktop tables.

The design tension is that a phone-sized butler must not feel like a dashboard, and a
dashboard must not feel like a spreadsheet on a 390pt screen. Resolving it is a design
problem handed to Claude Design with full creative latitude — this document fixes the
requirements and the constraints, not the form.

### Why replace rather than add

The existing SPA is desktop-shaped: a fixed left icon rail, a right telemetry rail, and
data tables. None of those three survive a 390pt viewport, and a responsive retrofit would
leave every page carrying two layouts. The backend contract is unchanged, so the rewrite is
purely a client-layer swap: same routes, same sockets, same TanStack Query keys.

The outgoing SPA is **not** treated as prior art for the new design. Its information
architecture, colour system and component choices carry no weight; the new client is being
designed from the product and its constraints, not from what shipped before.

---

## 2. Alfred, in the terms the client must express

Alfred is an ambient, voice-first, self-hosted butler. Two minds share the work:

| | System 1 — Reflex | System 2 — Conscious |
|---|---|---|
| Model | local SLM (Ollama / vLLM) | frontier cloud LLM via OpenRouter |
| Budget | event → action **< 500 ms** | conversation, planning, judgment |
| Rights | `benign`-risk tools only, and only for entities in the attention set | full tool access; critical actions still need confirmation |

Between them sits a biologically-framed memory: **episodic** (hot Redis HNSW + cold
sqlite-vec, with significance scoring and contextual decay), **semantic** (markdown
preference/profile files), **procedural** (routines with a candidate → active → dormant → archived
lifecycle). A nightly **Librarian** consolidates. A **Trigger Engine** turns conversation
into time/schedule/sensor/composite triggers. Six supervised OS processes talk over Redis
Streams; every message is a typed Pydantic schema.

The client's job is to make that legible. Five things the UI must never blur:

1. **Which mind acted.** Reflex vs Conscious is the primary axis of "why did Alfred do
   that". The existing 7-colour source palette already encodes it — keep it.
2. **Risk tier and confirmation state.** A critical action pending confirmation is a
   5-minute-TTL, one-shot, high-stakes object. It gets dedicated UI, not a toast.
3. **Hot vs cold, detected vs confirmed.** Memory tier and routine lifecycle are the two
   places where "Alfred knows this" has degrees.
4. **Queued vs applied.** Admin controls that `XADD` to `alfred:actions` return
   `{"status":"queued"}` and cannot report the downstream outcome. The UI must say
   "queued", not "done", and trigger toggles carry `effective_within_seconds: 60`.
5. **Live vs last-known.** Offline or reconnecting, stale vitals must be stamped, never
   shown as current.

### Backend surface the client consumes

| Group | Endpoints |
|---|---|
| Auth | `GET /api/auth/status`, `POST /api/auth/{register,login}/{begin,complete}`, `POST /api/auth/logout` |
| Chat | `WS /ws` — `text`/`audio` up; `session`/`transcription`/`response`/`notification`/`error` down |
| Telemetry | `WS /ws/telemetry` — subscribe/unsubscribe over 8 catalog streams; no history replay (cursor `$`) |
| Observability | `GET /api/admin/{overview,streams,streams/{name},triggers,sessions,devices,notifications/deferred,memory/{episodic,semantic,routines,scratchpad}}` |
| Controls | `POST /api/admin/{dnd,notifications/drain,librarian/run,triggers/{id}/enabled,triggers/{id}/fire}`, `DELETE /api/admin/sessions/{id}` |
| Integrations | `GET /api/integrations`, `PUT`/`DELETE /api/integrations/{name}/credentials`, `GET /api/integrations/{name}/status` |
| Actions | `POST /api/actions/{request_id}/confirm` |
| Devices | `POST`/`DELETE /api/devices/register` |
| Onboarding | `POST /api/onboarding` |
| Health | `GET /health` (service healthcheck — the SPA's own page is `/system`) |

Streams in the catalog: `events`, `actions`, `user_requests`, `user_responses`,
`reflex_observations`, `notifications`, `home_state`, `home_action_results`.

---

## 3. Phase 0 — security prerequisites (blocking)

None of this is optional once a public DNS record points at the box.

### 3.1 The proxy defeats the trust gate

`require_trusted_network` trusts loopback + RFC1918 + Tailscale CGNAT and reads
`request.client.host`. Behind nginx-proxy-manager that value is NPM's Docker-bridge address
inside `172.16.0.0/12` — permanently trusted. The endpoints gated by *only* that check are:

- `POST /api/auth/register/begin` and `/register/complete` — **passkey registration**.
  `exclude_credentials` blocks re-registering the same authenticator, not a new one. A
  stranger registers their own passkey, receives a valid `alfred_auth` session, and is
  "sir": full chat, full admin API, full home control.
- `PUT` / `DELETE /api/integrations/{name}/credentials` — overwrite the HA token, the
  OpenRouter key, any sovereign service credential.
- `POST` / `DELETE /api/devices/register` — APNs token injection/removal.

Required changes:

1. Run the channels process with `--proxy-headers --forwarded-allow-ips=<npm-container-ip>`
   so `request.client.host` resolves from `X-Forwarded-For` rather than the socket peer.
2. Make NPM emit a single, trustworthy `X-Forwarded-For`. **The hop count matters:** with
   Cloudflare proxying (§3.6) the chain is client → Cloudflare → NPM → uvicorn, so
   nginx's `$remote_addr` is a *Cloudflare* address, not the user. nginx must be told to
   recover the real client from `CF-Connecting-IP` first, and to trust that header only
   from Cloudflare's ranges — otherwise anyone can spoof it. See the config in §3.5.
3. Set `ALFRED_TRUSTED_NETWORKS_STRICT=1` and list the home LAN CIDR explicitly in
   `ALFRED_TRUSTED_NETWORKS`. Blanket RFC1918 trust must not survive exposure.

Result: registration and credential writes become home-network-only, which is the correct
posture. **Login is not trusted-network gated** — so the flow is *enrol the passkey once at
home, then sign in from anywhere*, which is exactly the product behaviour we want.

### 3.2 Admin reads must work remotely

The admin router applies `require_trusted_network` **and** `require_authenticated`. After
3.1, every debug surface returns 403 from outside the house — which kills the stated
requirement of full-parity debugging from the phone.

**Decision:** split the gate by verb.

| Class | Gate | Rationale |
|---|---|---|
| Admin **reads** (11 endpoints) | `require_authenticated` only | Protected by a hardware-bound, phishing-resistant passkey. Read-only observability. |
| Admin **controls** (6 endpoints) | `require_authenticated` only | Curated, reversible, and already mirrored by things Alfred does itself. |
| Registration, credential writes, device register, voice enroll | `require_authenticated` **+** `require_trusted_network` | Credential-equivalent. Home network only. |

Compensating controls: session TTL cut from 24h to 8h with silent passkey re-auth; a
`POST /api/auth/logout` "sign out all devices"; rate limiting at NPM on `/api/auth/*`.

### 3.3 WebAuthn RP ID is pinned to the hostname

Passkeys bind to the RP ID, derived from the `Host` header (`auth_routes.py:43`). Every
credential currently in `data/credentials.db` is bound to a LAN IP or `localhost` and will
**not** work on `alfred.<domain>`. Plan: choose the public hostname first, register there
once from home, and never change it. Document that the hostname is now load-bearing
cryptographic state.

### 3.4 Static caching fights the service worker

`NoCacheStaticMiddleware` stamps `no-cache, no-store, must-revalidate` on every `.js`,
`.css`, `.html`. With hashed Vite asset names that is both unnecessary and actively harmful
to a PWA — the shell re-downloads on every cold launch over cellular. Fix per the existing
backlog item `docs/backlog/low/web-asset-cache-headers.md`: `immutable, max-age=31536000`
for `/assets/*`, `no-cache` for `index.html` only.

### 3.5 NPM proxy host configuration

Proxy host `alfred.<domain>` → `http://<host-lan-ip>:8081`. Real values live in the
operator runbook next to `.env`, outside this repository.

- **Websockets Support: ON.** Without it `/ws` and `/ws/telemetry` fail the upgrade — the
  single most common cause of a working page with a dead butler.
- Force SSL, HTTP/2, HSTS. Certificate via Let's Encrypt **DNS-01** using a scoped
  Cloudflare API token (Zone.DNS:Edit on the zone); NPM has the Cloudflare DNS
  plugin built in. DNS-01 avoids HTTP-01 round-tripping through the orange cloud.
- Cloudflare SSL/TLS mode must be **Full (strict)** so the Cloudflare → NPM leg is also
  verified against that certificate.

Advanced tab:

```nginx
# Recover the true client IP: trust CF-Connecting-IP, but only from Cloudflare.
# Full current list: https://www.cloudflare.com/ips-v4
set_real_ip_from 173.245.48.0/20;
set_real_ip_from 103.21.244.0/22;
set_real_ip_from 104.16.0.0/13;
set_real_ip_from 172.64.0.0/13;
set_real_ip_from 131.0.72.0/22;
# ... remainder of the published Cloudflare ranges ...
real_ip_header    CF-Connecting-IP;
real_ip_recursive on;

# Hand uvicorn exactly one value — the real client. Never append.
proxy_set_header X-Forwarded-For   $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Host              $host;

proxy_read_timeout 120s;   # publish_and_wait allows 60s for a System 2 turn
```

- Response headers: HSTS, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a
  CSP whose `connect-src` includes `'self'` and `wss://alfred.<domain>`.
- Optional edge hardening: a Cloudflare rate-limiting rule on `/api/auth/*`.

### 3.6 DNS and Cloudflare exposure

Verified 2026-09-04: the host's public address is globally routable — **not CGNAT** —
and inbound 443 already reaches NPM through the Cloudflare proxy for the other hosts on
this box.

`alfred.<domain>` currently exists as a **DNS-only (grey cloud)** record pointing at a
private LAN address — a private address published in public DNS. It must change.

**Plan:** add `alfred.<domain>` to the `DOMAINS` list of the existing `cloudflare-ddns`
container, which runs `PROXIED=true`, so the record becomes orange-cloud and IP-tracked
automatically — the same treatment the other hosts already get, and no new machinery.

Consequences of proxying, all accepted deliberately:

| Effect | Consequence |
|---|---|
| Home IP hidden behind Cloudflare | Wanted — this box runs the home, an LLM, and now an admin surface |
| DDoS absorption + edge rate limiting | Wanted, especially on `/api/auth/*` |
| Real client IP moves to `CF-Connecting-IP` | Drives the nginx config in §3.5 |
| **Proxied WebSockets are closed after ~100s idle** | The chat socket idles for hours. Without a keepalive the client reconnects endlessly. Add an application-level ping every 30s on both sockets. Web Push (§6) means proactive delivery no longer depends on a live socket, but the churn is still worth removing |

---

## 4. iOS PWA constraints that drive the design

These are not polish items; each one visibly breaks the app if ignored.

| # | Constraint | Design consequence |
|---|---|---|
| 1 | Service worker, WebAuthn, `getUserMedia`, and installability all require a secure context | HTTPS via NPM is a hard prerequisite, not a nicety |
| 2 | iOS ignores SVG for the homescreen icon; the current manifest ships SVG only | Ship `apple-touch-icon` PNGs at 180×180 (opaque, no alpha — iOS does not composite transparency), plus 192/512 maskable for the manifest |
| 3 | Notch / Dynamic Island / home indicator | `viewport-fit=cover` + `env(safe-area-inset-*)`; the tab bar sits above the home indicator, the header below the island |
| 4 | `100vh` is wrong in Safari — the dynamic toolbar changes it | Use `100dvh`/`100svh` with a `--app-height` JS fallback |
| 5 | The software keyboard covers fixed-position elements | `visualViewport` listener pins the composer; `interactive-widget=resizes-content` in the viewport meta |
| 6 | Rubber-band scroll makes a standalone app feel like a web page | `overscroll-behavior: none` on the shell, preserved inside scroll containers |
| 7 | iOS blocks `Audio.play()` without a direct gesture, more strictly than desktop | One shared `AudioContext`, unlocked on the first tap anywhere in the app, through which all TTS and URGENT notification audio plays. Resolves `docs/qa-backlog/audio-context-unlock-before-first-urgent-notification.md` |
| 8 | Safari's `MediaRecorder` has no WebM; the current `VoiceButton` hard-codes `audio/webm;codecs=opus` and dies | Feature-detect `audio/mp4` → `audio/aac` → browser default. Backend already accepts aac/m4a/wav. Resolves `docs/backlog/medium/webapp-voice-safari-codec.md` |
| 9 | Web Push on iOS works only for PWAs **already added to the homescreen**, and `Notification.requestPermission()` must be called from a user gesture | Permission cannot be requested on a first web visit. The install step is a prerequisite the app has to explain and sequence. See §6 |
| 9b | iOS has no `beforeinstallprompt` — there is no programmatic "install" button | The app must *teach* the Share → Add to Home Screen gesture, detect standalone mode, and only then offer notifications |
| 10 | iOS suspends and kills standalone PWAs aggressively | Rehydrate on `visibilitychange`: reconnect both sockets, re-send `session_id` from `localStorage`, refetch vitals, and reconcile the feed gap via `GET /api/admin/streams/{name}` (the telemetry socket starts at `$` and replays nothing) |
| 11 | `navigator.vibrate` is unsupported in iOS Safari | Do not design around haptics. Confirmation weight comes from motion and layout, not touch feedback |
| 12 | Standalone mode has no browser chrome | Every sheet and detail view needs its own explicit dismiss; external links get an in-app treatment |

---

## 5. Client requirements (form is the designer's call)

Sections 5 and 6 previously prescribed an information architecture, a colour system
and screen-by-screen layouts carried over from the outgoing SPA. That was overreach:
navigation, visual language and screen structure are being designed fresh with full
creative latitude. What follows is what the client must *do*, not how it must look.

### 5.1 Capability inventory

Everything below has a backing endpoint in section 2 and must be reachable somewhere.

| Area | What the user must be able to do |
|---|---|
| Conversation | Send text; hold-to-talk voice with a returned transcription; hear spoken replies; see which tools Alfred ran for a given answer; see the response's mood label; resume a session across app restarts |
| Notifications | Receive three urgency levels inline; set and clear DND with or without an expiry; see and drain the deferred queue |
| Confirmations | Approve a pending critical action within its 300s fuse, seeing exactly what will run; observe expiry |
| Triggers | Browse time/schedule/sensor/composite triggers; enable/disable; fire manually; distinguish one-shot from recurring; see last-fired |
| Memory | Vector-search episodic memory; read semantic markdown documents; inspect routines with lifecycle and confidence; read the scratchpad |
| Activity | Watch eight live event streams; filter; pause; page back through history; inspect a single event's full payload |
| Causality | Answer "why did Alfred just do that" — correlate one conversation turn with the system activity it caused |
| Health | See connection state, Redis, today's spend against `DAILY_COST_CAP_USD`, DND, inference backend probes, event rate |
| Operations | List and terminate sessions; list devices; view and edit integration credentials with health; run the Librarian |
| Identity | Register and sign in with a passkey; manage credentials; first-run onboarding |

### 5.2 Honesty requirements

These are correctness constraints, not stylistic preferences. Each maps to a real
property of the backend that the UI can misrepresent.

1. **Queued is not applied.** Six admin controls `XADD` to `alfred:actions` and return
   `{"status":"queued"}`; the API cannot report the downstream outcome. Trigger toggles
   additionally carry `effective_within_seconds: 60`.
2. **Live is not last-known.** Offline or reconnecting state must be stamped with the
   time it was last true.
3. **Hot is not cold; detected is not confirmed.** Memory tier and routine lifecycle are
   the two places where "Alfred knows this" has degrees.
4. **Which mind acted.** Reflex and Conscious have different rights and different failure
   modes; a user diagnosing behaviour needs to tell them apart across eight event sources
   in a fast-moving feed.
5. **Browsing does not perturb.** Admin episodic search runs with `update_stats=False` so
   it cannot skew the decay signal the Librarian reads. This is non-obvious and worth
   surfacing.
6. **DND without an expiry never auto-drains.** A silently growing deferred queue is
   otherwise invisible.

### 5.3 Design problems

Open problems handed to the designer, not solved here:

- One app serving three registers — a calm ambient butler, an urgent irreversible
  decision, and dense developer debugging — without three disjoint visual languages.
- Making an opaque two-model AI legible at a glance.
- Debugging density that genuinely works at 393pt, not a table that survived a media query.
- A high-stakes confirmation that cannot misfire on a touchscreen, with no haptics
  available to add weight.

## 6. Web Push

Decided: **yes**, in addition to the existing APNs path. APNs continues to serve the native
`alfred-ios` app; Web Push serves this PWA. They coexist behind one dispatcher.

### 6.1 Backend

Alfred's notification layer is already an ABC-plus-registry, so this is an additive adapter
rather than a change to the dispatcher.

- `core/notifications/adapters/webpush.py` — `WebPushChannelAdapter`, decorated with
  `@ChannelRegistry.register()`, mirroring `APNsChannelAdapter`. `supported_urgencies` =
  IMPORTANT + URGENT. INFORMATIONAL stays in-app and on Signal; a push for every
  informational event would train the user to ignore the channel.
- **VAPID keypair** generated once and persisted: private key through `shared/secrets.py`
  (keyring), public key served at `GET /api/push/public-key` (unauthenticated — it is a
  public key, and the client needs it before subscribing).
- **Delivery** is RFC 8291 `aes128gcm` payload encryption plus an RFC 8292 VAPID JWT,
  POSTed to the subscription endpoint. `pywebpush` covers both; new deps are `http-ece`
  and `cryptography` (`PyJWT[crypto]` and `httpx[http2]` are already in base for APNs).
  Worth stating in the PRD: the payload is encrypted end-to-end from Alfred to the browser,
  so Apple's push relay cannot read notification content. That is a real local-first
  property, not a footnote.
- **Urgency mapping** onto the Web Push `Urgency` header: IMPORTANT → `normal`,
  URGENT → `high`. `TTL` 3600s for urgent, 86400s for important.
- **Pruning:** delete the subscription on `404`/`410` from the push service, mirroring the
  existing APNs 410 auto-prune. Browsers rotate endpoints; a stale one is permanent.

### 6.2 Storage and registration

Reuse the `alfred:push:devices` hash so `GET /api/admin/devices` keeps showing one unified
device list across both platforms.

- Field: SHA-256 of the endpoint URL (stable and short; raw endpoints are long).
- Value: `{platform: "web_push", endpoint, keys: {p256dh, auth}, identity, registered_at, user_agent}`.

`POST /api/devices/register` takes a discriminated payload — an APNs `device_token` or a
Web Push `subscription` object — keyed on `platform`.

**Gate change:** device registration moves from `require_trusted_network` to
`require_authenticated`. This is strictly stronger than today, where it has *no* auth at
all, and it lets the user enable notifications from a phone that is away from home — which
matters, since the natural moment to enable them is right after installing.

### 6.3 Client

- Service worker handles `push` (→ `showNotification`, decrypted payload) and
  `notificationclick` (→ focus an existing client or open one, deep-linked to the relevant
  surface — a pending approval must land on that approval, not the home screen).
- `navigator.setAppBadge()` for unread count; supported in installed iOS PWAs.
- The subscription is re-validated on every launch: browsers silently rotate or drop
  endpoints, so a client that subscribes once and never checks goes quiet without warning.

### 6.4 The sequencing problem this creates

iOS will not expose `PushManager` or `Notification.requestPermission()` until the app has
been added to the homescreen, and there is no programmatic install prompt on iOS. So the
real-world path is:

open in Safari → sign in with a passkey → *learn and perform* Share → Add to Home Screen →
reopen from the homescreen → tap to enable notifications → grant the iOS permission.

That is six steps, two of which happen outside the app's control, and one of which is a
gesture the user has to be taught. Designing that sequence so it does not feel broken is a
genuine problem, and it is handed to the design phase rather than solved here.

## 7. Technical architecture

Keep the stack — it is current, working, and the rewrite is a UI-layer swap.

Vite 8 · React 19 · TypeScript 6 · Tailwind v4 (`@theme inline`) · TanStack Query 5 ·
react-router 7 · Vitest. Add `vite-plugin-pwa` (Workbox) for the manifest and service worker.

Carried over unchanged: `lib/ws.ts` `ReconnectingSocket` (exponential backoff to 8s, code
4001 terminates without retry), `chat-socket.ts`, `telemetry-socket.ts` with subscription
replay on reconnect, the split `AlfredStatusContext` / `AlfredFeedContext`, and the `api()`
401 → `/login` redirect.

New:

- `lib/audio.ts` — the single unlocked `AudioContext` and a first-gesture unlock hook.
- `lib/recorder.ts` — codec-negotiating `MediaRecorder` wrapper.
- `lib/viewport.ts` — `visualViewport` and `dvh` handling.
- `lib/trace.ts` — correlation engine for the causality requirement (§5.1).
- `lib/lifecycle.ts` — `visibilitychange` rehydration: sockets, session, vitals, feed gap.

**Service worker policy.** Precache the app shell; cache-first for hashed assets;
**network-only** for `/api/*` and both sockets. Live system state must never be served from
cache — a stale health page is worse than an honest offline state. Offline renders
last-known vitals with an explicit "as of HH:MM · offline" stamp.

**Testing.** Vitest + Testing Library for component and hook behaviour; the socket clients
get contract tests against recorded server frames; Playwright drives the install-and-launch
path in a mobile Safari viewport. Every one of the twelve iOS constraints in §4 gets a test
or a documented manual QA step.

**Causality correlation — known limit.** The "why did Alfred do that" requirement is
satisfied client-side by correlating `session_id` and `request_id` across `user_requests`,
`actions`, `user_responses` and `events` within a time window. That is adequate for a
single-user house and degrades gracefully, but it is heuristic. A backend correlation ID
propagated through the event schemas would make it exact; file that as a follow-up rather
than blocking the client on it.

---

## 8. Build sequence

| Phase | Content | Gate |
|---|---|---|
| **0** | Backend security: proxy headers, strict trusted nets, admin gate split, asset cache headers, WS ping, NPM proxy host | Nothing is exposed publicly until this merges |
| **0b** | Backend additions the design assumes (§10 table): pending-action reads, auth sessions + passkeys + pairing code, overview fields, cost counters, routine confidence history, attention-set API | Client work can start against a real API |
| **1** | App shell per the approved design: safe areas, viewport/keyboard handling, navigation, auth + passkey flow, then conversation — chat, voice, notifications, confirmations | Butler is usable on the phone |
| **2** | Live event feed, filters, pagination, single-event inspection, causality correlation | Debugging is usable on the phone |
| **3** | Memory, triggers/DND, health and operations surfaces | Full capability parity with the old SPA |
| **4** | PWA: manifest, icons, service worker, offline states | Installs and behaves as native |
| **5** | Web Push: VAPID, adapter, registration, service-worker handlers, install-and-enable sequence | Alfred reaches the phone when the app is closed |
| **6** | Desktop composition (three columns ≥1100 px, sheet-over-bench ≥760 px) and keyboard shortcuts | Same client, laptop-sized |

`web/dist/` is served by `core/channels/spa.py` exactly as today; the container's `webbuild`
stage is unchanged. The CI `spa` gate continues to build `web/`.

## 9. Open questions

**Resolved 2026-09-04.** Web Push is in scope alongside APNs (§6). The hostname is
`alfred.<domain>`, moving from its current grey-cloud LAN record onto the existing
`cloudflare-ddns`-managed proxied set (§3.6) — and because the WebAuthn RP ID is pinned to
it (§3.3), that name is now load-bearing cryptographic state and must not change again.
Existing passkeys bound to LAN addresses will not carry over; re-register once from home.

**Still open:**

1. **The native iOS app.** Once this PWA reaches parity — including push — does
   `alfred-ios` retire, or stay as the Face ID / APNs path? Keeping both means maintaining
   two clients and two push adapters for one user.
2. **Correlation IDs.** The causality view is heuristic until a correlation ID is
   propagated through the event schemas (§7). Worth doing properly, but not a blocker.

---

## 10. Design handoff reconciliation (provisional — re-verify after pending merges)

Claude Design delivered the handoff on 2026-09-04 (`Project_setup_questions.zip`:
`README.md`, `Alfred.dc.html`, `Alfred Desktop.dc.html`, `Alfred Design System.dc.html`,
`00 Architecture and Reasoning.dc.html`, `support.js`). It answers every design problem in
§5.3: **the Room** (one timeline, no tab bar), **the Door** (full-height inverted sheet,
slide-with-travel confirmation, 300 s fuse ring, expiry tombstone), and **the Workshop**
(Activity · Memory · Triggers · System benches over a shared stream spine), plus identity
gates and the six-step iOS install→permission rail from §6.4. Fidelity is high; copy and
the closed status vocabulary (`queued`, `applied`, `last true HH:MM`, `unknown since HH:MM`,
`hot / cold`, `candidate · active · dormant · archived`, `expired · not done`,
`takes effect within 60 s`) are final and must be used verbatim.

Checked against `master` at `56d9c03` (after #192 passive observation and #193 embedding backend). Confirmed matches: moods
(`bus/schemas/events.py:120`), routine lifecycle (`core/memory/routines/store.py:73`),
`TriggerFired.fired_by`, `ReflexObservation{trigger_event, action, result,
decision_context}`, trigger kinds time/sensor/composite (schedule = cron time), confirm
404 on consumed/expired, trigger toggle `effective_within_seconds: 60`, corrupt-trigger
500, episodic 503 while the embedder loads, `update_stats=False` on admin recall,
deferred-queue items are full `Notification` JSON.

### Gaps — design assumption vs backend

| Design assumes | Backend today | Proposed resolution | Size |
|---|---|---|---|
| Door recoverable on cold launch / notification tap; fuse from `expires_at` | Pending actions reach only a *live* `/ws` notification; `alfred:pending:*` has no read route; TTL not exposed | `GET /api/actions/pending` (SCAN + TTL) and `GET /api/actions/{id}` (404 → tombstone) | small |
| System › Sessions: `passkey · pwa · signed in 07:02 · 192.168.1.24` + End | `GET /api/admin/sessions` lists *conversation* sessions; auth sessions store only `credential_id`, `created_at` | `GET/DELETE /api/auth/sessions`; record `ip`, `user_agent`, `channel` at login; `current` flag | small |
| Devices & identity: passkey list | `CredentialStore.list_credentials()` exists, no route | `GET /api/auth/credentials` | small |
| "Add a passkey on another device" · pairing code · `Pairing window closes 21:16` | Nothing; after Phase 0 registration is trusted-network-only | One-time code minted by an authenticated session (TTL 5 min) accepted by `register/begin` as an alternative to the network gate | medium |
| Health: reflex latency + model, HA latency, ev/s; spend `38 requests · avg £0.037` | Overview: `inference.{ollama,lmstudio}` booleans; `CostState{spend_usd, cap_usd}` | Overview gains `reflex.{last_ms, p50_ms, model}` (from recent `reflex_observations` timestamps), `rate_5m` per stream, integration `latency_ms`; `CostState.request_count` | small |
| Routine confidence sparkline "last 8 consolidations" | `RoutineSpec` has `confidence` + `consecutive_misses`, no history | Drop the sparkline; show value + misses. History is a Librarian change, later | — |
| Setup gate step 3 "What may the reflex touch?" | Attention set is YAML-seeded; no API | Two-step setup (passkey → HA token) for now | — |
| Door reason sentence ("You asked me to let the cleaner in…") | `ActionRequest` has no reason; the notification body is `Alfred wants to run '…' on … — confirm?` | Show the notification body; a `reason` in `metadata` is a conscious-engine follow-up | — |
| Maintenance: `last 03:00 · 42 reviewed` | `Librarian.consolidate()` returns stats but persists nothing | Persist the last-run summary to Redis; expose on overview | small |
| App-level WS ping (Cloudflare closes proxied sockets after ~100 s idle) | `/ws` answers unknown types with an error frame | No-op `{"type":"ping"}` on `/ws` and `/ws/telemetry` | tiny |
| Sign-in gate foot `Passkey · iPhone 15 Pro · registered 12 Aug` | `GET /api/auth/status` returns `registered` only — correct on a public host | Client remembers the last-used device name in `localStorage`; nothing pre-auth from the server | — |

Two consequences of the merges for the client, no endpoint change: `ReflexObservation.action`
/ `result` may now be `None` (the Reflex records what it saw and did not act on). The Room
renders only observations with an action; the Activity bench and the causal thread render
passive ones as an RX row reading "watched, took no action". And episodic search returns
503 whenever the embedding backend is unreachable (vLLM `:8001`), not only while a local
model loads — the same `model: 503` state, reached more often.

Composes from existing endpoints, no backend change: Room history (merged
`user_requests` / `user_responses` / `reflex_observations` / `notifications` pages),
Door **Applied** (telemetry `home_action_results` with the same `request_id`), trigger row
flip on re-read, Held-back count (`overview.counts.deferred`), credentials Save & test
(`PUT` then `GET …/status`), theme-by-time-of-day.

**Decided 2026-09-04: build everything the design assumes.** Re-verified against `56d9c03`
after #192/#193 merged (no gap closed), then decided. The backend scope is therefore
Phase 0 security **plus**:

| Addition | Where |
|---|---|
| `GET /api/actions/pending`, `GET /api/actions/{id}` (TTL from Redis; 404 = consumed/expired) | `core/channels/web_server.py`, `core/routing/pending.py` |
| `ActionRequest.reason: str \| None`, passed into the confirmation notification `metadata` and populated by the conscious engine's tool call when available | `bus/schemas/events.py`, `core/routing/domain_router.py`, conscious tool schema |
| Auth sessions: record `ip`, `user_agent`, `channel` at login; `GET /api/auth/sessions` (with `current`), `DELETE /api/auth/sessions/{id}`, `POST /api/auth/logout?all=1`; TTL 24h → 8h | `core/identity/auth_routes.py` |
| Passkeys: `GET /api/auth/credentials`, `DELETE /api/auth/credentials/{id}` (never the last one) | `core/identity/auth_routes.py`, `credentials.py` |
| Cross-device passkey: `POST /api/auth/pairing` mints a one-time 6-digit code (TTL 5 min, authenticated caller); `register/begin` accepts `pairing_code` as an alternative to the trusted-network gate; code consumed on `register/complete` | `core/identity/auth_routes.py` |
| Overview: `reflex.{model, last_ms, p50_ms}` from recent `reflex_observations`, per-stream `rate_5m`, `librarian.{last_run_at, reviewed, next_run_at}`; integration status gains `latency_ms` | `core/channels/admin_api.py`, `core/librarian/consolidator.py` (persist last-run summary) |
| `CostState.request_count` and `avg_usd` | `core/conscious/cost.py` |
| `RoutineSpec.confidence_history: list[float]` (last 8, appended per consolidation) | `core/memory/schemas.py`, Librarian |
| Attention set: `GET /api/admin/attention` (domains, members, seen) and `PUT /api/admin/attention/{domain}` (allow / ask) — backs setup step 3 and System › Reflex | `core/reflex/attention.py`, `core/channels/admin_api.py` |
| No-op `{"type":"ping"}` on `/ws` and `/ws/telemetry` | `core/channels/web_server.py`, `telemetry_ws.py` |
| Web Push (§6): adapter, VAPID, `GET /api/push/public-key`, `POST/DELETE /api/devices/register` discriminated by `platform` | `core/notifications/adapters/webpush.py` |

The Room renders only reflex observations that carry an `action`; passive ones surface in
the Activity bench and the causal thread as "watched, took no action".
