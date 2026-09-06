# Client should mark LAN-only writes instead of failing them at the server

## Summary
Credential writes (`PUT/DELETE /api/integrations/{name}/credentials`) and device-token
writes (`POST/DELETE /api/devices/register`) require a passkey session **and** a trusted
network. A signed-in user reaching Alfred over the public host still sees the SAVE and
CLEAR buttons on every integration card (`web/src/pages/IntegrationCard.tsx:125-147`);
pressing SAVE always 403s, and the toast shows the raw operator 403 text.

## Context
The session gate is satisfied on the public host, so the client cannot tell the two
failure modes apart until the request comes back. The 403 body deliberately withholds
operator guidance from anonymous callers, but a signed-in operator gets the full text
(env-var name, example CIDR) surfaced in a UI toast, which is more than the moment needs.
The coming PWA Workshop has the same surface and should not repeat this.

## Acceptance Criteria
- The client knows whether the current origin is LAN-only-write capable (e.g. an
  `/api/auth/status` field, or a cheap probe) and disables SAVE/CLEAR with a
  "LAN only — open Alfred over Tailscale to change credentials" label rather than
  offering a button that always fails.
- A 403 from a credential or device-token write renders a short human message, not the
  raw operator `detail` string.
- The same affordance is specified for the PWA Workshop before it ships.
