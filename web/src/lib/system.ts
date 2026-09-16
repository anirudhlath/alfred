import { api, post, put } from "./api";
import { pastLabel, usd } from "./format";
import type { AttentionDomain, CredentialField, IntegrationInfo, Overview } from "./types";

/**
 * One domain of the Reflex attention set. Re-exported rather than restated:
 * `GET /api/admin/attention` is the same endpoint the setup gate reads, and two
 * declarations of one wire shape drift apart the first time the server changes.
 */
export type { AttentionDomain };

/**
 * One field of an integration's credential schema — `core/integrations/base.py`'s
 * `CredentialField`, under the name this bench's form builder uses for it.
 */
export type IntegrationField = CredentialField;

/**
 * One entry of `GET /api/integrations`. `kind` is required here where
 * `IntegrationInfo` leaves it optional: `build_integration_entry` takes it as a
 * positional argument and the route calls it for every adapter and every
 * registry-declared service, so the field is on every entry the bench can meet.
 */
export interface Integration extends Omit<IntegrationInfo, "kind"> {
  kind: "adapter" | "service";
}

/**
 * One live session as `GET /api/auth/sessions` sends it. `ip`, `user_agent` and
 * `created_at` are the session hash's own fields and the route defaults each to
 * `""` — an empty one is a record that never carried it, not an error.
 *
 * `expires_in` is seconds off the Redis TTL, so it counts down between reads;
 * every session is an 8 h one (`_AUTH_SESSION_TTL`).
 */
export interface AuthSession {
  session_id: string;
  credential_id: string;
  device_name: string;
  channel: string;
  ip: string;
  user_agent: string;
  created_at: string;
  expires_in: number;
  current: boolean;
}

/**
 * One registered passkey as `GET /api/auth/credentials` sends it. Deliberately
 * no `public_key` and no `sign_count` — the route does not send them.
 *
 * `current` is the passkey *this session signed in with*, which is not the same
 * question as `AuthSession.current`: one phone can hold one passkey and several
 * sessions.
 */
export interface Credential {
  credential_id: string;
  device_name: string;
  transports: string[];
  created_at: string;
  last_used_at: string | null;
  current: boolean;
}

/**
 * `GET /api/integrations/{name}/status`. A service's answer also carries a
 * `detail` object — the proxied `/health` body, or `{error}` when the probe
 * could not reach it — which nothing on this bench reads yet.
 */
export interface IntegrationStatus {
  name: string;
  healthy: boolean;
  latency_ms: number | null;
}

/**
 * What a connected service's row says about itself. A closed vocabulary:
 * `queued` is this client's own claim that a save landed, `testing` is a probe
 * in flight, and the other three are what the last probe found.
 */
export type ServiceState = "ok" | "failed" | "unset" | "testing" | "queued";

/** Each state's one sentence. The row prints this and never a word of its own. */
const SERVICE_NOTES: Record<ServiceState, string> = {
  ok: "reachable",
  failed: "not answering",
  unset: "no credentials saved",
  testing: "testing…",
  queued: "saved · testing",
};

export function serviceNote(state: ServiceState): string {
  return SERVICE_NOTES[state];
}

/**
 * An ISO stamp that parses, as epoch ms. Null for anything else — the sessions
 * route defaults a missing `created_at` to `""`, and a passkey that has never
 * been used has a literal `null` `last_used_at`.
 */
const time = (value: string | null): number | null => {
  if (value === null || value.trim() === "") return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) || parsed <= 0 ? null : parsed;
};

/** A number the notes below may print: finite, and nothing else. */
const finite = (value: number | undefined): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/**
 * `passkey · web · signed in 07:02 · 192.168.1.24` — the session row's second
 * line, and `passkey · web · this device · signed in 07:02 · 192.168.1.24` for
 * the one you are reading it on.
 *
 * `passkey` is a constant because it is the only way in: every session is
 * opened by `register/complete` or `login/complete`, both of which verify a
 * WebAuthn assertion first. The clause is there so the line says what kind of
 * credential opened the session, not to leave room for a second kind.
 *
 * `now` because a session lives 8 hours and can cross midnight: a bare `23:50`
 * on a session opened last night would read as one opened in ten minutes' time.
 * The address is dropped when the server did not send one rather than printed
 * empty, which would leave the line ending in a separator.
 */
export function sessionMeta(session: AuthSession, now: number): string {
  const at = time(session.created_at);
  const clauses = ["passkey", session.channel];
  if (session.current) clauses.push("this device");
  clauses.push(`signed in ${at === null ? "--:--" : pastLabel(new Date(at), new Date(now))}`);
  if (session.ip.trim() !== "") clauses.push(session.ip);
  return clauses.join(" · ");
}

/**
 * `internal, hybrid · last used 07:02` — the passkey row's second line.
 *
 * Both clauses can be absent in the record and neither is left blank: a passkey
 * registered by an authenticator that reported no transports says so, and one
 * that has never signed in says `never used` rather than an empty stamp.
 *
 * `now` for the same reason `sessionMeta` takes it, and more so: a passkey's
 * last use can be weeks old, where a bare clock would be a lie about today.
 */
export function credentialMeta(credential: Credential, now: number): string {
  const transports =
    credential.transports.length === 0
      ? "no transports recorded"
      : credential.transports.join(", ");
  const at = time(credential.last_used_at);
  const used = at === null ? "never used" : `last used ${pastLabel(new Date(at), new Date(now))}`;
  return `${transports} · ${used}`;
}

/**
 * `$0.0036` — a per-request cost. `usd()` is `toFixed(2)`, which reads a third
 * of a cent as `0.00`, so the average gets four places instead, trimmed back to
 * the two every other money string on this screen has: `0.0036`, `0.037`,
 * `0.50`. An average below a hundredth of a cent still reads `0.00`, which is
 * the honest answer at that scale.
 */
function perRequest(value: number): string {
  const trimmed = value.toFixed(4).replace(/0+$/, "");
  const [, decimals = ""] = trimmed.split(".");
  return decimals.length >= 2 ? trimmed : usd(value);
}

/**
 * `$0.42 of $5.00 today · 118 requests · $0.0036 each` — the spend card's note,
 * with every clause the server did not send dropped rather than filled in.
 *
 * `cost` is null on a house that has spent nothing today, which is a fact about
 * the day and not a missing read. A cap of zero — or one that is not a number —
 * is not divided by: the note says there is no cap instead, and `spendFraction`
 * draws an empty bar.
 */
export function spendNote(cost: Overview["cost"]): string {
  if (cost === null) return "no spend recorded today";

  const spend = finite(cost.spend_usd) ?? 0;
  const cap = finite(cost.cap_usd);
  const clauses =
    cap === null || cap <= 0
      ? [`$${usd(spend)} today`, "no cap set"]
      : [`$${usd(spend)} of $${usd(cap)} today`];

  const requests = finite(cost.request_count);
  if (requests !== null) clauses.push(`${requests} requests`);
  const average = finite(cost.avg_usd);
  if (average !== null) clauses.push(`$${perRequest(average)} each`);

  return clauses.join(" · ");
}

/**
 * How full the spend bar is, in `[0, 1]`. Zero when there is no cost to draw
 * and zero when there is no cap to measure against — the one place the division
 * happens, so no view can produce a `NaN` width from it.
 */
export function spendFraction(cost: Overview["cost"]): number {
  if (cost === null) return 0;
  const cap = finite(cost.cap_usd);
  const spend = finite(cost.spend_usd) ?? 0;
  if (cap === null || cap <= 0) return 0;
  return Math.min(1, Math.max(0, spend / cap));
}

/** `GET /api/auth/sessions` — newest first, the caller's own marked `current`. */
export async function fetchAuthSessions(): Promise<AuthSession[]> {
  const body = await api<{ sessions?: AuthSession[] }>("/api/auth/sessions");
  return body.sessions ?? [];
}

/**
 * `DELETE /api/auth/sessions/{id}` — applied, not queued: the route deletes the
 * Redis key and answers. Ending your own also clears the cookie, which is why
 * the next read of anything is a 401 and the Expired gate rises over the bench.
 *
 * The id is encoded even though the route validates its shape: building a path
 * out of a server-supplied string without encoding it is the habit, not the
 * exception.
 */
export async function endAuthSession(id: string): Promise<void> {
  await api<{ deleted: boolean }>(`/api/auth/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/** `GET /api/auth/credentials` — every registered passkey, no secrets on the row. */
export async function fetchCredentials(): Promise<Credential[]> {
  const body = await api<{ credentials?: Credential[] }>("/api/auth/credentials");
  return body.credentials ?? [];
}

/**
 * `POST /api/auth/pairing` — a one-shot code for registering a passkey on a new
 * device, valid five minutes. Session-gated only, deliberately: the device
 * doing the minting is often the one that is away from the home network, so the
 * code's own budget stands in for the network half of the gate.
 *
 * The reply also carries `ttl_seconds`; `expires_at` is the same fact as an
 * instant, and an instant is what the note shows.
 */
export function mintPairingCode(): Promise<{ code: string; expires_at: string }> {
  return post<{ code: string; expires_at: string }>("/api/auth/pairing");
}

/**
 * The name the home service registers under, so nothing on this bench matches
 * on a guess: `tests/core/channels/test_double_gate.py` pins it, and the setup
 * gate looks it up the same way.
 */
export const HOME_SERVICE = "home-service";

/**
 * `GET /api/integrations` — a **bare array**, not an envelope. The one endpoint
 * on this bench that answers that way (`core/channels/web_server.py`), so the
 * unwrap every neighbour does would read `undefined` here.
 */
export function fetchIntegrations(): Promise<Integration[]> {
  return api<Integration[]>("/api/integrations");
}

/**
 * `GET /api/integrations/{name}/status` — an adapter's in-process health check,
 * or a service's proxied `/health`. Never throws for an unhealthy integration:
 * the route answers 200 with `healthy: false`. A throw here is the request
 * failing, which the caller reads as `failed` all the same.
 */
export function fetchIntegrationStatus(name: string): Promise<IntegrationStatus> {
  return api<IntegrationStatus>(`/api/integrations/${encodeURIComponent(name)}/status`);
}

/**
 * `PUT /api/integrations/{name}/credentials` — the one write on this bench
 * behind two gates: a session *and* the trusted network. Off the home network
 * it answers 403 while every read on the bench still works (deviation 12).
 */
export async function saveCredentials(
  name: string,
  values: Record<string, string>,
): Promise<void> {
  await put<{ status: string }>(
    `/api/integrations/${encodeURIComponent(name)}/credentials`,
    values,
  );
}

/**
 * `GET /api/admin/attention` — every domain the Reflex has observed. A 503 here
 * is the store being unreachable, which the route deliberately distinguishes
 * from an empty list; the caller must not flatten the two.
 */
export async function fetchAttention(): Promise<AttentionDomain[]> {
  const body = await api<{ domains?: AttentionDomain[] }>("/api/admin/attention");
  return body.domains ?? [];
}

/**
 * `PUT /api/admin/attention/{domain}` — add to (`allow`) or sticky-remove from
 * (`ask`) one domain's set. Answers with that domain read back, so the caller
 * needs no second read; the route applies `ask` after `allow`, so an entity in
 * both ends up removed.
 */
export function putAttention(
  domain: string,
  allow: string[],
  ask: string[],
): Promise<AttentionDomain> {
  return put<AttentionDomain>(`/api/admin/attention/${encodeURIComponent(domain)}`, {
    allow,
    ask,
  });
}

/**
 * `POST /api/admin/dnd` — a direct write, unlike the trigger controls: the
 * route sets or deletes the Redis key itself and answers with the state. The
 * switch may move on this.
 *
 * `until` is omitted rather than sent as null, so "clear it" and "on, with no
 * expiry" are one rule: the key is only ever present when there is an instant
 * to put in it.
 */
export async function setDnd(active: boolean, until: string | null): Promise<void> {
  await post<{ active: boolean }>("/api/admin/dnd", {
    active,
    ...(until === null ? {} : { until }),
  });
}

/**
 * `POST /api/admin/notifications/drain` — queued, never applied: the route
 * publishes an internal action and the notifier picks it up when it next reads
 * the queue. Nothing here learns that it sent anything.
 */
export async function drainDeferred(): Promise<void> {
  await post<{ status: string }>("/api/admin/notifications/drain");
}

/** `POST /api/admin/librarian/run` — queued the same way; the run reports on the events stream. */
export async function runLibrarian(): Promise<void> {
  await post<{ status: string }>("/api/admin/librarian/run");
}
