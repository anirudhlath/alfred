# PWA phase 1 follow-ups

**Priority:** low
**Source:** the four Mission Control backlog tickets the phase 1 hard cut made moot
(`web-frontend-followups.md`, `voice-enrollment-card-polish.md`,
`lan-only-writes-affordance.md`, `web-activity-virtualized-list.md`), rewritten for the
client that replaced them. Those files are deleted; everything below is what survived
the cut.

## 1. Credential writes are LAN-only, and the client still cannot tell

Carried over whole from `lan-only-writes-affordance.md` — the surface moved, the problem
did not. Credential writes (`PUT/DELETE /api/integrations/{name}/credentials`) and
device-token writes (`POST/DELETE /api/devices/register`) need a passkey session **and**
a trusted network. The setup gate's second step (`web/src/gates/SetupGate.tsx`) `PUT`s
home-service credentials with no idea whether the current origin can write at all, so a
first run over the public host gets a 403 — which now raises the **Denied gate** over the
setup gate, mid-wizard. That is a worse failure than the old toast, not a better one.

**Acceptance:**
- The client knows whether the current origin is LAN-only-write capable (e.g. an
  `/api/auth/status` field, or a cheap probe) and says so *before* the step — "open
  Alfred on the house network to add credentials" — rather than letting the write fail.
- A 403 from a credential or device-token write renders a short human message; the raw
  operator `detail` (env-var name, example CIDR) is not surfaced in the UI.
- The Workshop specifies the same affordance before it ships its own credential cards.

## 2. `web/src/lib/types.ts` still hand-mirrors the bus and admin schemas

From `web-frontend-followups.md` §3, unchanged by the rewrite: the TS contract
(`ChatServerMessage`, `TelemetryMessage`, `Overview`, `StreamPage`, `PendingAction`,
`ActionResultEvent`) is hand-written against the Pydantic models and the admin response
shapes, with no drift guard in either direction. A server-side field rename type-checks
green on both sides and fails at runtime. Largely unavoidable across Python↔TS.
**Acceptance (optional):** a comment on the mirrored bus schemas pointing at `types.ts`;
consider a `datamodel-code-generator` step as a future task.

## 3. Stream names are hardcoded client-side

From `web-frontend-followups.md` §1, much reduced. The three-way duplication is gone with
`AlfredProvider` and `ActivityPage`; what remains is two small hardcoded sets —
`ROOM_STREAMS` in `web/src/lib/history.ts` and `RESULT_STREAM` in
`web/src/door/DoorProvider.tsx` — naming streams that `core/channels/stream_catalog.py`
owns. Renaming a stream there leaves the client silently reading nothing; no test or type
catches it. **Acceptance:** a catalog assertion in the SPA CI test, or expose the catalog
(`GET /api/admin/stream-catalog`) and derive the names.

## 4. The Workshop's Activity view will need virtualization

From `web-activity-virtualized-list.md`. The page it was written about is deleted, and
the Room's timeline is bounded — four stream pages of 50, plus live rows — so nothing
needs virtualizing today. The finding still stands for phase 2: mounting every entry of a
live feed degrades scroll on low-end hardware past ~500 nodes, and
`@tanstack/react-virtual` or `react-window` is the answer when the Activity view returns.
Log it against that plan rather than the current client.

## 5. Voice enrollment has no home in the client

From `voice-enrollment-card-polish.md`. `web/src/pages/VoiceEnrollmentCard.tsx` is
deleted and phase 1 has no enrollment surface; `docs/voice-satellites.md` still describes
the old settings card. When the Workshop reinstates enrollment, the three gaps the
original ticket found are worth building in from the start rather than fixing after:
the mic control disabled while a submit is in flight, the error state cleared as soon as
a new sample is recorded, and the status region announced via `aria-live="polite"`.
