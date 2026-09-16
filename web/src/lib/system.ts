import { api, post, put } from "./api";
import { dayLabel, finiteNumber, hhmm, isoMs, pastLabel, rateText, usd } from "./format";
import type { Overview } from "./types";

// ---------------------------------------------------------------------------
// Wire shapes
// ---------------------------------------------------------------------------

/**
 * One field of an integration's credential schema — `core/integrations/base.py`'s
 * `CredentialField`. Declared here rather than in `types.ts` because this module
 * is what reads the endpoint; one shape, one declaration, one import path.
 */
export interface IntegrationField {
  label: string;
  field_type: "text" | "password" | "url";
  required: boolean;
  placeholder: string;
  default: string;
  help_text: string;
  /** Passed to the adapter and never persisted, so it is never `configured`. */
  transient: boolean;
}

/**
 * One entry of `GET /api/integrations`. `kind` is required: `build_integration_entry`
 * takes it as a positional argument and the route calls it for every adapter and
 * every registry-declared service, so the field is on every entry there is.
 */
export interface Integration {
  name: string;
  category: string;
  kind: "adapter" | "service";
  schema: { fields: Record<string, IntegrationField> };
  /** Which fields have something in the keyring. Never the values, which the route does not send. */
  configured: Record<string, boolean>;
}

/**
 * The fields a save has to carry. `PUT …/credentials` validates **before** it
 * stores: `validate_credential_body` (`core/channels/service_credentials.py`)
 * 422s on any `required && !transient` field missing from the body, whatever is
 * already in the keyring, and `CredentialField.required` defaults to true
 * (`core/integrations/base.py`). So a partial write is not a thing this API
 * offers, and a form that sent one would be unrotatable: a new token for
 * `home-service` alone comes back `422 · Missing required fields: ['url']`,
 * naming a field whose own placeholder had just said it was saved.
 */
export function requiredFields(entry: Integration): string[] {
  return Object.entries(entry.schema.fields)
    .filter(([, field]) => field.required && !field.transient)
    .map(([key]) => key);
}

/** The labels of the required fields still empty, in the schema's own order. */
export function missingLabels(entry: Integration, values: Record<string, string>): string[] {
  return requiredFields(entry)
    .filter((key) => (values[key] ?? "").trim() === "")
    .map((key) => entry.schema.fields[key].label);
}

/**
 * The standing rule under every credential form, because the API has no
 * partial write and a reader cannot be expected to know that.
 */
export const SAVE_TOGETHER =
  "Every field is sent together · retype the ones already stored, or nothing is saved.";

/** Why the save is being held, with the fields named rather than left to be hunted. */
export function missingNote(labels: readonly string[]): string {
  return `Still blank: ${labels.join(", ")} · nothing is sent until every field is filled.`;
}

/**
 * What a stored field's own box says. `saved` alone was read as "no need to
 * retype", which the route makes false for every service with more than one
 * required field.
 */
export const STORED_PLACEHOLDER = "saved · retype to save";

/**
 * A transient field is passed to the adapter and never persisted
 * (`CredentialField.transient`), so `configured` is false for it for ever and
 * the box can never say `saved`. Without this line a reader has no way to know
 * the `mfa_code` they typed last time is not still in the house.
 */
export const TRANSIENT_NOTE = "not stored · sent with this save and forgotten";

/** One domain of the Reflex attention set. `members` may act, `seen` is everything observed. */
export interface AttentionDomain {
  domain: string;
  members: string[];
  seen: string[];
}

/**
 * One live session as `GET /api/auth/sessions` sends it. `ip`, `user_agent` and
 * `created_at` are the session hash's own fields and the route defaults each to
 * `""` — an empty one is a record that never carried it, not an error.
 *
 * `expires_in` is seconds off the Redis TTL, and `_expires_in` maps a missing
 * key, a persistent key and anything non-numeric all to **0**. So `0` means
 * "no TTL reported", not "expiring now", and a row must not count it down.
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
 * `GET /api/integrations/{name}/status`.
 *
 * `detail` is the whole reason a failed row can say anything true: a service
 * that is unreachable, sick or has no endpoint declared answers **200** with
 * `healthy: false` and `detail: {error: …}` (`core/channels/web_server.py`,
 * `_service_status`), so there is no HTTP status to quote and the real reason
 * is here. A healthy service's `detail` is its own `/health` body.
 */
export interface IntegrationStatus {
  name: string;
  healthy: boolean;
  latency_ms: number | null;
  detail?: unknown;
}

// ---------------------------------------------------------------------------
// The service vocabulary
// ---------------------------------------------------------------------------

/**
 * What a connected service's row says about itself. A closed vocabulary:
 * `queued` is this client's own claim that a save landed, `testing` is a probe
 * in flight, and the other three are what the last probe found.
 */
export type ServiceState = "ok" | "failed" | "unset" | "testing" | "queued";

/** The second half of every `failed` note: nothing was thrown away. */
const KEPT = "stored value kept until you replace it";

/**
 * What a failed check says when the probe carried no status and the service
 * sent no words of its own. The handoff writes `401` here; that number is
 * **not** printed, because the commonest failure on this route is a 200 with
 * `healthy: false` and no status anywhere — and accusing a service of
 * rejecting a password it never saw is the same error the 404 branch below
 * exists to stop, applied to the commoner case.
 */
const UNEXPLAINED_FAILURE = "the last check came back unhealthy";

/**
 * The one status on this route that is **not** the service's answer. `GET
 * /api/integrations/{name}/status` looks the name up in the registry first and
 * 404s on its own when it is not there, so a service that has unregistered
 * since the list was read produces a 404 that the service itself never sent.
 *
 * Its own sentence rather than its own state word: the row on the right still
 * reads `failed`, and the closed vocabulary is untouched.
 */
const UNKNOWN_NAME_NOTE =
  "404 · Alfred does not know this name · " +
  "the service may have unregistered since the list was read";

/**
 * The sentence under a service's row — the handoff's four, plus `queued`. Not a
 * gloss on the state word, which the row already carries on its right: this is
 * the line that says what is stored and what happens next.
 *
 * Three sources, in order of how much they know: a status the probe really
 * reported (so a service answering 502 is not accused of rejecting a
 * password), then the words the service sent about itself, then the bare fact
 * that the check did not come back ok. Nothing is invented at any step.
 */
export function serviceNote(
  state: ServiceState,
  status: number | null = null,
  detail: string | null = null,
): string {
  switch (state) {
    case "ok":
      return "stored encrypted at rest · last check ok";
    case "failed":
      if (status === 404) return UNKNOWN_NAME_NOTE;
      if (status !== null) return `${status} from the service on the last check · ${KEPT}`;
      if (detail !== null && detail.trim() !== "") return `${detail.trim()} · ${KEPT}`;
      return `${UNEXPLAINED_FAILURE} · ${KEPT}`;
    case "unset":
      return "nothing stored · Alfred answers without this source";
    case "testing":
      return "round-trip in progress · up to 10 s";
    case "queued":
      return "saved · testing";
  }
}

// ---------------------------------------------------------------------------
// Row and card formatters
// ---------------------------------------------------------------------------

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
  const at = isoMs(session.created_at);
  const clauses = ["passkey", session.channel];
  if (session.current) clauses.push("this device");
  // Not left to `pastLabel`: an unparseable stamp reaches `dayMonth` through it
  // and prints `NaN undefined`, which is worse than saying nothing.
  clauses.push(`signed in ${at === null ? "--:--" : pastLabel(new Date(at), new Date(now))}`);
  if (session.ip.trim() !== "") clauses.push(session.ip);
  return clauses.join(" · ");
}

/**
 * `passkey · registered 12 Aug · internal, hybrid · last used 07:02 · this device`
 * — the passkey row's second line, minus the device name, which is the row's title.
 *
 * Built by the same rule as `sessionMeta` and in the same order, because the two
 * sit on one screen: the credential kind, then when it came to be, then what it
 * is, then when it was last used, then whether it is the one in your hand. A
 * clause the server did not send is dropped rather than filled in — an
 * authenticator that reported no transports has nothing to say about them.
 *
 * `never used` is not such a clause: a passkey that has never signed in is a
 * fact worth stating, and one the sheet reads for whether to keep it.
 */
export function credentialMeta(credential: Credential, now: number): string {
  const clock = new Date(now);
  const clauses = ["passkey"];

  const registered = isoMs(credential.created_at);
  if (registered !== null) clauses.push(`registered ${dayLabel(new Date(registered), clock)}`);
  if (credential.transports.length > 0) clauses.push(credential.transports.join(", "));

  const used = isoMs(credential.last_used_at);
  clauses.push(used === null ? "never used" : `last used ${pastLabel(new Date(used), clock)}`);
  if (credential.current) clauses.push("this device");

  return clauses.join(" · ");
}

/**
 * `$0.0036` — a per-request cost. `usd()` is `toFixed(2)`, which reads a third
 * of a cent as `0.00`, so a sub-dollar average gets four places instead, trimmed
 * back to the two every other money string on this screen has: `0.0036`,
 * `0.037`, `0.50`.
 *
 * A dollar or more takes plain cents: the extra places exist to keep a fraction
 * of a cent legible, and `$1234.5678 each` is neither legible nor to the point.
 */
function perRequest(value: number): string {
  if (value >= 1) return usd(value);
  const trimmed = value.toFixed(4).replace(/0+$/, "");
  const [, decimals = ""] = trimmed.split(".");
  return decimals.length >= 2 ? trimmed : usd(value);
}

/**
 * The cap, or null when there is nothing to measure against. The single guard
 * behind both the note and the bar — kept in one place so the sentence and the
 * width can never disagree about whether a cap exists.
 */
function capOf(cost: Overview["cost"]): number | null {
  if (cost === null) return null;
  const cap = finiteNumber(cost.cap_usd);
  return cap === null || cap <= 0 ? null : cap;
}

/**
 * What the cap *does*, which is the only reason a cap is on screen at all:
 * `core/conscious/engine.py` returns the System-1 fallback saying precisely
 * this when `is_budget_exceeded()`. Copy, not data — it needs no field and it
 * is true of every house — so it is dropped only when there is no cap for it
 * to be about.
 *
 * The handoff's `resets 00:00` is deliberately *not* here: `core/conscious/
 * cost.py` rolls the day on `datetime.now(UTC)`, so the window resets at UTC
 * midnight and `00:00` is false for every household that is not on it.
 */
const AT_THE_CAP = "at the cap, the conscious mind declines and says so";

/**
 * `$1.42 of $5.00` — the spend card's own line, and the money in one place.
 * `$1.42 · no cap set` when there is nothing to measure against, and the
 * server's own silence when it reported no spend at all: a house that has
 * spent nothing today sends `cost: null`, which is a fact about the day rather
 * than a missing read.
 */
export function spendHeadline(cost: Overview["cost"]): string {
  if (cost === null) return "no spend recorded today";
  const spend = usd(finiteNumber(cost.spend_usd) ?? 0);
  const cap = capOf(cost);
  return cap === null ? `$${spend} · no cap set` : `$${spend} of $${usd(cap)}`;
}

/**
 * `38 requests · $0.0036 each · at the cap, the conscious mind declines and
 * says so` — everything about today's spend that is not the money itself,
 * with every clause the server did not send dropped rather than filled in.
 *
 * The money moved out to `spendHeadline` above: the card prints both, and a
 * note that opened with the same `$1.42 of $5.00` as the line above it was one
 * amount printed twice, six pixels apart.
 */
export function spendNote(cost: Overview["cost"]): string {
  if (cost === null) return "";

  const clauses: string[] = [];
  const requests = finiteNumber(cost.request_count);
  if (requests !== null) clauses.push(`${requests} ${requests === 1 ? "request" : "requests"}`);
  const average = finiteNumber(cost.avg_usd);
  if (average !== null) clauses.push(`$${perRequest(average)} each`);
  // Only where there is a cap to decline at. A house with none never meets it.
  if (capOf(cost) !== null) clauses.push(AT_THE_CAP);

  return clauses.join(" · ");
}

/**
 * How full the spend bar is, in `[0, 1]`. Zero when there is no cost to draw and
 * zero when there is no cap to measure against — the one place the division
 * happens, so no view can produce a `NaN` width from it.
 */
export function spendFraction(cost: Overview["cost"]): number {
  const cap = capOf(cost);
  if (cost === null || cap === null) return 0;
  const spend = finiteNumber(cost.spend_usd) ?? 0;
  return Math.min(1, Math.max(0, spend / cap));
}

// ---------------------------------------------------------------------------
// Derivations the bench renders from
// ---------------------------------------------------------------------------

/**
 * One status probe's fetch state — as much of a react-query result as the two
 * derivations below read, so both are testable without a `QueryClient`.
 *
 * `data` and `isError` are deliberately separate facts: react-query keeps the
 * last successful answer after a later attempt fails, so a probe can be `isError`
 * and still be holding a `healthy: true` from ten minutes ago.
 */
export interface ProbeState {
  data: IntegrationStatus | undefined;
  /** No answer has ever come back — the first probe is still in flight. */
  isPending: boolean;
  /** The last attempt failed to get an answer at all. */
  isError: boolean;
  /** The status that failure carried, when a server sent one. */
  status: number | null;
}

/** As much of a credential save as `serviceRows` reads. */
export interface SavingState {
  saving: boolean;
  savedAt: number | null;
}

/** What one service's row knows about itself. */
export interface ServiceRow {
  state: ServiceState;
  /**
   * The last probe's round trip in ms; null whenever there is no answer to time.
   *
   * Deliberately **not** drawn on the row (deviation: the handoff puts latency
   * on the Health grid and nowhere else). A row's state word can come from a
   * *save* rather than from the probe, and `210 ms  testing` beside a
   * credential that is being replaced times a round trip against the old one.
   * Kept on the contract because the Health grid's home card is derived from
   * the same probe and `useSystem.test.tsx` pins it.
   */
  latency: number | null;
  /** The status behind a `failed`, for the row's note. Null when nobody sent one. */
  status: number | null;
  /**
   * What the service said about itself on the last check, when it said
   * anything: the `detail.error` of a 200 with `healthy: false`. This is the
   * only thing that explains the commonest failure on this route.
   */
  detail: string | null;
}

/**
 * Which word a service's row wears, and the two numbers under it. The order of
 * the ladder is the point: a save this client sent outranks a probe, a probe
 * still in flight outranks its last answer, and an integration with nothing
 * stored says so rather than reporting the health of a connection it was never
 * given the credentials for.
 *
 * `probes` is positional against `list` — one probe per entry, in order, which
 * is how `useQueries` hands them back.
 */
export function serviceRows(
  list: readonly Integration[],
  probes: readonly (ProbeState | undefined)[],
  saves: Readonly<Record<string, SavingState>>,
): Record<string, ServiceRow> {
  const rows: Record<string, ServiceRow> = {};
  list.forEach((entry, index) => {
    const probe = probes[index];
    rows[entry.name] = {
      state: serviceState(entry, probe, saves[entry.name]),
      // A failed attempt has no round trip of its own, and the retained `data`
      // beside it belongs to an earlier one. Reporting it would put `210 ms`
      // next to the word `failed` — two readings of one probe, disagreeing.
      latency: probe === undefined || probe.isError ? null : (probe.data?.latency_ms ?? null),
      status: probe?.status ?? null,
      // Same reading as the latency beside it: a retained `detail` belongs to
      // the probe that produced it, not to the attempt that has just failed.
      detail: probe === undefined || probe.isError ? null : detailText(probe.data?.detail),
    };
  });
  return rows;
}

/**
 * One readable line out of a `detail` object, or nothing. Only a string field
 * is taken — a `/health` payload is arbitrary and JSON-dumping it into a
 * sentence would put a brace where a reason should be.
 */
function detailText(detail: unknown): string | null {
  if (detail === null || typeof detail !== "object") return null;
  const record = detail as Record<string, unknown>;
  for (const key of ["error", "detail", "message"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim() !== "") return value.trim();
  }
  return null;
}

function serviceState(
  entry: Integration,
  probe: ProbeState | undefined,
  save: SavingState | undefined,
): ServiceState {
  // `saved · testing` only once the server has taken the credentials; while the
  // `PUT` itself is still in flight nothing has been saved yet.
  if (save?.saving === true) return save.savedAt === null ? "testing" : "queued";
  if (probe === undefined || probe.isPending) return "testing";
  const fields = Object.keys(entry.schema.fields);
  // `every`, not `some`: a half-filled form is configured enough to probe, and
  // saying `nothing stored` over a service that is answering would be wrong.
  if (fields.length > 0 && fields.every((field) => entry.configured[field] !== true)) {
    return "unset";
  }
  // Load-bearing: without it the retained `data` above reads `ok` for a service
  // whose latest probe could not reach it at all.
  if (probe.isError) return "failed";
  return probe.data?.healthy === true ? "ok" : "failed";
}

/**
 * One stat card on the health grid. `alive` is a flag rather than a word the
 * view matches on: a card that dims because the string it was handed happened
 * not to read `alive` is a screen one rename away from lying.
 */
export interface HealthCell {
  value: string;
  note: string;
  alive: boolean;
}

/** The four cards, in the order the grid draws them. No GPU figure, no service count. */
export interface Health {
  bus: HealthCell;
  reflex: HealthCell;
  rate: HealthCell;
  home: HealthCell;
}

export interface HealthInput {
  overview: Overview | undefined;
  /**
   * `GET /api/integrations` has answered. Without it there is no telling a house
   * with no home service from one whose registry has simply not been read yet,
   * and only the first of those is a fact about the house.
   */
  registryRead: boolean;
  /** The home service's probe; absent when the registry does not carry one. */
  home: ProbeState | undefined;
}

/**
 * The four cards, derived here so the view holds no logic and every branch is
 * reachable from a plain function call.
 *
 * Nothing on this grid claims anything before the read that would settle it: a
 * note is never more confident than the value beside it, and `not read yet` is
 * a different sentence from `not registered`.
 */
export function healthGrid({ overview, registryRead, home }: HealthInput): Health {
  const read = overview !== undefined;
  const connected = overview?.redis.connected === true;
  const streamCount = Object.keys(overview?.streams ?? {}).length;
  const reflex = overview?.reflex;
  const lastMs = finiteNumber(reflex?.last_ms);

  return {
    bus: {
      value: connected ? "alive" : "unknown",
      note: read
        ? `bus · redis · ${streamCount} ${streamCount === 1 ? "stream" : "streams"}`
        : "bus · redis · not read yet",
      alive: connected,
    },
    reflex: {
      value: lastMs === null ? "—" : `${Math.round(lastMs)} ms`,
      note: !read
        ? "reflex · not read yet"
        : reflex?.model
          ? `reflex · ${reflex.model}`
          : "reflex · no model reported",
      alive: lastMs !== null,
    },
    rate: {
      value: rateText(overview),
      // A description of the measure, not a claim about the figure, so it is
      // true before the first read as well as after it.
      note: "event rate · 5-min mean",
      alive: streamCount > 0,
    },
    home: homeCell(registryRead, home),
  };
}

/**
 * The fourth card reads the *probe*, never the credential word: `unset` is the
 * right thing on a Services row and the wrong thing here, where a service that
 * is up and answering in 210 ms would otherwise be reported by the state of its
 * keyring.
 */
function homeCell(registryRead: boolean, home: ProbeState | undefined): HealthCell {
  if (!registryRead) return { value: "—", note: "home assistant · not read yet", alive: false };
  if (home === undefined) {
    return { value: "—", note: "home assistant · not registered", alive: false };
  }
  if (home.isPending) return { value: "—", note: "home assistant · testing", alive: false };
  // No latency on this branch: the number react-query is still holding belongs
  // to an earlier probe, not to the one that just failed.
  if (home.isError) return { value: "failed", note: "home assistant · not answering", alive: false };

  const latency = finiteNumber(home.data?.latency_ms);
  const trip =
    latency === null ? "no round trip measured" : `${Math.round(latency)} ms`;
  const healthy = home.data?.healthy === true;
  return { value: healthy ? "ok" : "failed", note: `home assistant · ${trip}`, alive: healthy };
}

/**
 * The same four cards once the reads have stopped landing (handoff §8, the
 * offline grid). Every value is blanked and every label says `unknown`,
 * because the numbers `healthGrid` last derived are now a photograph: a
 * dimmed `210 ms` is still a latency claim about a service that may be down,
 * and that is the §5.2 failure with the brightness turned down.
 *
 * Distinct from `healthGrid`'s own `not read yet`, which is the *never*-read
 * case and is not the same sentence. `readAt` is when the house was last
 * heard from, so the bus card names the moment the rest of the grid stopped
 * being evidence — the same instant the section's stamp prints.
 */
export function staleGrid(readAt: number): Health {
  const since = `unknown since ${hhmm(readAt)}`;
  return {
    bus: { value: "?", note: `bus · ${since}`, alive: false },
    reflex: { value: "?", note: "reflex · unknown", alive: false },
    rate: { value: "?", note: "event rate · unknown", alive: false },
    home: { value: "—", note: "home assistant · unknown", alive: false },
  };
}

/**
 * The write landed and the read behind it did not. The switch is still showing
 * the last thing the server said, which is now out of date — and saying so is
 * the only honest thing left, because nothing else on the row can tell.
 */
export const DND_UNCONFIRMED = "Set, but the house has not confirmed it yet.";

/**
 * The name the home service registers under, so nothing matches on a guess:
 * `tests/core/channels/test_double_gate.py` pins it, and the setup gate imports
 * this same constant.
 */
export const HOME_SERVICE = "home-service";

// ---------------------------------------------------------------------------
// Reads and writes
// ---------------------------------------------------------------------------

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

/**
 * `POST /api/auth/logout` — ends the caller's own session and clears the cookie.
 *
 * Not `DELETE /api/auth/sessions/{id}` on your own row: this route needs no id,
 * which is the whole point of the control it sits under — a reader signing this
 * device out should not have to find themselves in a list first. `?all=1` is
 * deliberately not sent; signing every device out is a different decision from
 * signing this one out, and there is no screen asking for it.
 *
 * Nothing here raises the Expired gate. The cookie is gone, so the next read of
 * anything 401s and `api` raises it — one path out, whichever request gets
 * there first.
 *
 * **A refusal is not a reprieve.** `auth_routes.logout` builds its 503 response
 * and then calls `_clear_session_cookie` on it unconditionally, so a reader who
 * taps this during a Redis blip is signed out of this device whatever the
 * status line says. The caller must treat any answer as signed out.
 */
export async function logoutSession(): Promise<void> {
  await post<{ status: string }>("/api/auth/logout");
}

/**
 * What a *refused* sign-out still did. Appended to the server's own words
 * rather than replacing them: the store being down is news, and so is the fact
 * that it changed nothing about this device being signed out.
 */
export const SIGNED_OUT_ANYWAY =
  "signed out here either way · the house may hold the session until it expires";

/** `GET /api/auth/credentials` — every registered passkey, no secrets on the row. */
export async function fetchCredentials(): Promise<Credential[]> {
  const body = await api<{ credentials?: Credential[] }>("/api/auth/credentials");
  return body.credentials ?? [];
}

/**
 * `POST /api/auth/pairing` — a one-shot code for registering a passkey on a new
 * device, valid five minutes. Session-gated only, deliberately: the device doing
 * the minting is often the one that is away from the home network, so the code's
 * own budget stands in for the network half of the gate.
 *
 * `signal` because this is the one write whose *success* can do harm after the
 * caller has gone: a code minted for a screen that has closed is live on the
 * server for five minutes with no surface able to show it. Aborting an unmounted
 * mint is the most the client can do; the code's own TTL is the backstop for one
 * that had already landed.
 */
export function mintPairingCode(signal?: AbortSignal): Promise<{ code: string; expires_at: string }> {
  return api<{ code: string; expires_at: string }>("/api/auth/pairing", { method: "POST", signal });
}

/**
 * `GET /api/integrations` — a **bare array**, not an envelope. The one endpoint
 * on this bench that answers that way (`core/channels/web_server.py`), so the
 * unwrap every neighbour does would read `undefined` here.
 *
 * The array is checked rather than asserted, which is what `body.x ?? []` does
 * for the four neighbours. Without it a 200 carrying a JSON *object* — an error
 * envelope from a proxy in front of Alfred is the realistic one — reaches
 * `list.map` in `useSystem` and throws during render, and a render that throws
 * takes the whole Workshop with it. `null` and a 204 were always safe; this is
 * the case that was not.
 */
export async function fetchIntegrations(): Promise<Integration[]> {
  const body = await api<Integration[] | null>("/api/integrations");
  return Array.isArray(body) ? body : [];
}

/**
 * `GET /api/integrations/{name}/status` — an adapter's in-process health check,
 * or a service's proxied `/health`. A sick service is a **200** with
 * `healthy: false`, not a throw; a throw here is the request itself failing,
 * which is a different fact and retried like any other transport failure.
 */
export function fetchIntegrationStatus(name: string): Promise<IntegrationStatus> {
  return api<IntegrationStatus>(`/api/integrations/${encodeURIComponent(name)}/status`);
}

/**
 * `PUT /api/integrations/{name}/credentials` — the one write on this bench
 * behind two gates: a session *and* the trusted network. Off the home network it
 * answers 403 while every read on the bench still works (deviation 12).
 */
export async function saveCredentials(
  name: string,
  values: Record<string, string>,
  signal?: AbortSignal,
): Promise<void> {
  await api<{ status: string }>(`/api/integrations/${encodeURIComponent(name)}/credentials`, {
    method: "PUT",
    body: JSON.stringify(values),
    signal,
  });
}

/** How long a credential save is given before the row stops waiting on it. */
export const SAVE_TIMEOUT_MS = 20_000;

/**
 * The one refusal with no server behind it. Nothing in this tree sets a fetch
 * timeout, so a socket the house accepts and never answers would otherwise
 * leave the row reading `Testing…`, refusing every press, for the life of the
 * Workshop. It says what it does not know: the `PUT` may have landed.
 */
export const SAVE_TIMED_OUT =
  "No answer in 20 s · whether it was stored is unknown · try again.";

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
 * (`ask`) one domain's set. Answers with that domain read back, so a caller that
 * succeeds needs no second read; the route applies `ask` after `allow`, so an
 * entity in both ends up removed.
 *
 * A *refusal* is another matter: the route writes one entity at a time and is
 * explicit that the writes are not transactional, so a failure can leave part of
 * the change applied and the caller must go and look.
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
 * `POST /api/admin/dnd` — a direct write, unlike the trigger controls: the route
 * sets or deletes the Redis key itself and answers with the state. The switch
 * may move on this, once a read has confirmed it.
 *
 * `until` is omitted rather than sent as null, so "clear it" and "on, with no
 * expiry" are one rule: the key is only ever present when there is an instant to
 * put in it.
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
