import {
  authSession,
  credential,
  firstRunOverviewFixture,
  integration,
  overviewFixture,
  SYSTEM_NOW,
  SYSTEM_SIGNED_IN_AT,
} from "@/test/fixtures";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  credentialMeta,
  healthGrid,
  missingLabels,
  missingNote,
  requiredFields,
  SAVE_TOGETHER,
  serviceNote,
  serviceRows,
  sessionMeta,
  spendFraction,
  spendHeadline,
  spendNote,
  staleGrid,
  staleSpend,
  STORED_PLACEHOLDER,
  TRANSIENT_NOTE,
  type Integration,
  type ProbeState,
} from "./system";

/** 23:50 the night before `SYSTEM_NOW` — an 8 h session can only cross one midnight. */
const LAST_NIGHT = new Date(2026, 8, 15, 23, 50, 0).toISOString();

/**
 * A month past everything in this file. Every formatter below takes `now` as an
 * argument, and a suite run against the real clock cannot tell that apart from
 * one that reaches for `Date.now()` itself — on the day the fixtures were
 * written the two agree. Under this clock they do not: a stamp read against the
 * machine gains the day it fell on, and the assertions say what they mean.
 */
const WRONG_CLOCK = new Date(2026, 9, 20, 9, 0, 0);

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(WRONG_CLOCK);
});

afterEach(() => {
  vi.useRealTimers();
});

/** The spend the overview reports on a busy day, and the cap it is measured against. */
const COST = {
  date: "2026-09-16",
  spend_usd: 0.42,
  cap_usd: 5,
  request_count: 118,
  avg_usd: 0.0036,
};

describe("sessionMeta", () => {
  it("names the channel, the sign-in time and the address", () => {
    expect(sessionMeta(authSession({ channel: "web", ip: "192.168.1.24" }), SYSTEM_NOW)).toBe(
      "passkey · web · signed in 07:02 · 192.168.1.24",
    );
  });

  it("reads the clock it is handed rather than the machine's", () => {
    // The same session, read from a month later: the line says which day.
    expect(sessionMeta(authSession(), WRONG_CLOCK.getTime())).toBe(
      "passkey · web · signed in 07:02 16 Sep · 192.168.1.24",
    );
  });

  it("leaves out an address the server did not send", () => {
    const meta = sessionMeta(authSession({ ip: "" }), SYSTEM_NOW);
    expect(meta).toBe("passkey · web · signed in 07:02");
    expect(meta.endsWith(" · ")).toBe(false);
  });

  it("does not print an address of nothing but spaces", () => {
    expect(sessionMeta(authSession({ ip: "   " }), SYSTEM_NOW)).toBe(
      "passkey · web · signed in 07:02",
    );
  });

  it("says this device for the current session, before the sign-in time", () => {
    expect(sessionMeta(authSession({ current: true }), SYSTEM_NOW)).toBe(
      "passkey · web · this device · signed in 07:02 · 192.168.1.24",
    );
  });

  it("names the channel the server reported, whatever it is", () => {
    expect(sessionMeta(authSession({ channel: "signal" }), SYSTEM_NOW)).toContain("passkey · signal");
  });

  it("names the day for a session that signed in before midnight", () => {
    expect(sessionMeta(authSession({ created_at: LAST_NIGHT }), SYSTEM_NOW)).toBe(
      "passkey · web · signed in 23:50 yesterday · 192.168.1.24",
    );
  });

  it("does not invent a clock for a record with no sign-in time", () => {
    // The route defaults the field to `""` when the session hash has none.
    expect(sessionMeta(authSession({ created_at: "" }), SYSTEM_NOW)).toBe(
      "passkey · web · signed in --:-- · 192.168.1.24",
    );
  });

  it("does not print NaN for a stamp that will not parse", () => {
    const meta = sessionMeta(authSession({ created_at: "not a date" }), SYSTEM_NOW);
    expect(meta).toContain("signed in --:--");
    expect(meta).not.toContain("NaN");
  });
});

describe("credentialMeta", () => {
  it("builds the passkey row by the same rule as the session row", () => {
    expect(
      credentialMeta(
        credential({ transports: ["internal", "hybrid"], current: true }),
        SYSTEM_NOW,
      ),
    ).toBe("passkey · registered 12 Aug · internal, hybrid · last used 07:02 · this device");
  });

  it("leaves out this device for a passkey that did not open this session", () => {
    expect(credentialMeta(credential(), SYSTEM_NOW)).toBe(
      "passkey · registered 12 Aug · internal · last used 07:02",
    );
  });

  it("reads the clock it is handed rather than the machine's", () => {
    expect(credentialMeta(credential(), WRONG_CLOCK.getTime())).toBe(
      "passkey · registered 12 Aug · internal · last used 07:02 16 Sep",
    );
  });

  it("says never used rather than an empty stamp", () => {
    expect(credentialMeta(credential({ last_used_at: null }), SYSTEM_NOW)).toBe(
      "passkey · registered 12 Aug · internal · never used",
    );
  });

  it("drops the transports clause the authenticator did not report", () => {
    expect(credentialMeta(credential({ transports: [] }), SYSTEM_NOW)).toBe(
      "passkey · registered 12 Aug · last used 07:02",
    );
  });

  it("drops the registered clause for a record with no creation time", () => {
    expect(credentialMeta(credential({ created_at: "" }), SYSTEM_NOW)).toBe(
      "passkey · internal · last used 07:02",
    );
  });

  it("names the day a passkey was last used, when it was not today", () => {
    expect(credentialMeta(credential({ last_used_at: LAST_NIGHT }), SYSTEM_NOW)).toBe(
      "passkey · registered 12 Aug · internal · last used 23:50 yesterday",
    );
  });

  it("says earlier today for a passkey registered this morning", () => {
    expect(
      credentialMeta(
        credential({ created_at: new Date(SYSTEM_SIGNED_IN_AT).toISOString() }),
        SYSTEM_NOW,
      ),
    ).toBe("passkey · registered earlier today · internal · last used 07:02");
  });
});

describe("spendHeadline", () => {
  it("states the spend against the cap", () => {
    expect(spendHeadline(COST)).toBe("$0.42 of $5.00");
  });

  it("says there is no cap rather than dividing by one", () => {
    expect(spendHeadline({ ...COST, cap_usd: 0 })).toBe("$0.42 · no cap set");
    expect(spendHeadline({ ...COST, cap_usd: -5 })).toBe("$0.42 · no cap set");
    expect(spendHeadline({ ...COST, cap_usd: Number.NaN })).toBe("$0.42 · no cap set");
    // Zero is the only number that means "no cap": a household that sets a
    // one-dollar ceiling has set one, and hiding it would hide the sentence
    // about what happens when the day reaches it.
    expect(spendHeadline({ ...COST, cap_usd: 1 })).toBe("$0.42 of $1.00");
  });

  it("says no spend recorded today for a null cost", () => {
    expect(spendHeadline(null)).toBe("no spend recorded today");
  });

  it("reads a missing spend as nothing spent rather than as no figure", () => {
    expect(spendHeadline({ ...COST, spend_usd: Number.NaN })).toBe("$0.00 of $5.00");
    expect(spendHeadline({ ...COST, spend_usd: Number.NaN })).not.toContain("NaN");
  });
});

describe("spendNote", () => {
  it("counts the requests and says what the cap does", () => {
    expect(spendNote(COST)).toBe(
      "118 requests · $0.0036 each · at the cap, the conscious mind declines and says so",
    );
  });

  // Copy, not data: `core/conscious/engine.py` returns the System-1 fallback
  // saying exactly this when the budget is spent, so it needs no field — but a
  // house with no cap never meets one, and the sentence goes with it.
  it("drops the cap sentence when there is no cap to meet", () => {
    const note = spendNote({ ...COST, cap_usd: 0 });
    expect(note).toBe("118 requests · $0.0036 each");
    expect(note).not.toContain("NaN");
    expect(spendFraction({ ...COST, cap_usd: 0 })).toBe(0);
  });

  it("drops the clauses the server did not send", () => {
    expect(spendNote({ date: "2026-09-16", spend_usd: 0.42, cap_usd: 5 })).toBe(
      "at the cap, the conscious mind declines and says so",
    );
  });

  it("says nothing at all about a day with no cost recorded", () => {
    expect(spendNote(null)).toBe("");
  });

  it("treats a negative cap and one that is not a number as no cap at all", () => {
    expect(spendNote({ ...COST, cap_usd: -5 })).not.toContain("at the cap");
    expect(spendFraction({ ...COST, cap_usd: -5 })).toBe(0);
    expect(spendNote({ ...COST, cap_usd: Number.NaN })).not.toContain("at the cap");
    expect(spendFraction({ ...COST, cap_usd: Number.NaN })).toBe(0);
  });

  it("prints a per-request cost the two-decimal form would round away", () => {
    // `usd()` is `toFixed(2)`, so it reads $0.0036 as "0.00" — the clause needs
    // four places, trimmed back to the two every other money string here has.
    expect(spendNote({ ...COST, avg_usd: 0.037 })).toContain("$0.037 each");
    expect(spendNote({ ...COST, avg_usd: 0.5 })).toContain("$0.50 each");
    expect(spendNote({ ...COST, avg_usd: 0 })).toContain("$0.00 each");
    // Four places and no more: a fifth is a tenth of a hundredth of a cent,
    // which is a number nobody can act on at the width this clause is read at.
    expect(spendNote({ ...COST, avg_usd: 0.000123 })).toContain("$0.0001 each");
  });

  it("keeps a per-request cost of a dollar or more to plain cents", () => {
    // The extra places exist to keep a fraction of a cent legible, and
    // `$1234.5678 each` is neither legible nor to the point.
    expect(spendNote({ ...COST, avg_usd: 1.2345 })).toContain("$1.23 each");
    expect(spendNote({ ...COST, avg_usd: 1 })).toContain("$1.00 each");
  });

  it("counts one request in the singular", () => {
    expect(
      spendNote({ date: "2026-09-16", spend_usd: 0.01, cap_usd: 5, request_count: 1 }),
    ).toContain("1 request ·");
  });

  it("counts a day with no requests rather than dropping the clause", () => {
    expect(
      spendNote({ date: "2026-09-16", spend_usd: 0, cap_usd: 5, request_count: 0 }),
    ).toContain("0 requests");
  });
});

describe("staleGrid", () => {
  // The went-stale case, which is not the never-read case: `healthGrid` says
  // `not read yet` before the first answer, and this says `unknown` after the
  // answers stop. A dimmed `210 ms` is still a latency claim.
  it("blanks every value and dates the moment the house went quiet", () => {
    const grid = staleGrid(new Date(2026, 8, 16, 21, 14, 0).getTime());
    expect(Object.values(grid).map((cell) => cell.value)).toEqual(["?", "?", "?", "—"]);
    expect(Object.values(grid).map((cell) => cell.note)).toEqual([
      "bus · unknown since 21:14",
      "reflex · unknown",
      "event rate · unknown",
      "home assistant · unknown",
    ]);
  });

  it("leaves nothing alive to draw a live dot from", () => {
    const grid = staleGrid(Date.now());
    expect(Object.values(grid).every((cell) => !cell.alive)).toBe(true);
  });
});

describe("staleSpend", () => {
  // The card the grid sits above, under the same silence and by the same
  // argument: `$1.42 of $5.00` and `38 requests · $0.0036 each` are the two
  // things a reader takes from this card, and a dimmed claim is still a claim.
  it("blanks the money and dates the silence from the same instant the grid does", () => {
    const readAt = new Date(2026, 8, 16, 21, 14, 0).getTime();
    expect(staleSpend(readAt)).toEqual({
      headline: "?",
      note: "cloud spend · unknown since 21:14",
    });
    // The same minute the bus card names — one silence, not two.
    expect(staleGrid(readAt).bus.note).toContain("unknown since 21:14");
  });
});

describe("spendFraction", () => {
  it("measures the spend against the cap", () => {
    expect(spendFraction({ date: "2026-09-16", spend_usd: 1.25, cap_usd: 5 })).toBe(0.25);
  });

  it("clamps a day that ran past the cap", () => {
    expect(spendFraction({ date: "2026-09-16", spend_usd: 7, cap_usd: 5 })).toBe(1);
  });

  it("draws nothing at all when there is no cost to draw", () => {
    expect(spendFraction(null)).toBe(0);
  });

  it("does not draw a negative bar", () => {
    expect(spendFraction({ date: "2026-09-16", spend_usd: -2, cap_usd: 5 })).toBe(0);
  });

  it("draws an empty bar for a day whose spend the server did not report", () => {
    // A missing figure is nothing spent so far as this screen can tell; any
    // other default draws a proportion of a cap out of a field that is absent.
    expect(spendFraction({ ...COST, spend_usd: Number.NaN })).toBe(0);
  });
});

describe("serviceNote", () => {
  it("says what is stored and what happens next, for each state", () => {
    expect(serviceNote("ok")).toBe("stored encrypted at rest · last check ok");
    expect(serviceNote("unset")).toBe("nothing stored · Alfred answers without this source");
    expect(serviceNote("testing")).toBe("round-trip in progress · up to 10 s");
    expect(serviceNote("queued")).toBe("saved · testing");
  });

  it("quotes the status the probe actually reported", () => {
    // A service answering 502 is not accused of rejecting a password.
    expect(serviceNote("failed", 502)).toBe(
      "502 from the service on the last check · stored value kept until you replace it",
    );
  });

  // `_service_status` answers **200 with `healthy: false` and the reason in
  // `detail`** for a service that is unreachable or sick, so `status` is null on
  // the commonest failure of all. Printing a made-up `401` there sends the
  // reader to check a password that was never the problem.
  it("passes on the service's own reason when there was no status", () => {
    expect(serviceNote("failed", null, "connection refused")).toBe(
      "connection refused · stored value kept until you replace it",
    );
    // Whitespace is not a reason.
    expect(serviceNote("failed", null, "   ")).toBe(serviceNote("failed"));
  });

  it("says only what it knows when the probe carried neither", () => {
    expect(serviceNote("failed")).toBe(
      "the last check came back unhealthy · stored value kept until you replace it",
    );
    expect(serviceNote("failed", null)).toBe(serviceNote("failed"));
    expect(serviceNote("failed", null, null)).toBe(serviceNote("failed"));
    // No number anywhere: a status the service never sent is an accusation.
    expect(serviceNote("failed")).not.toMatch(/\d/);
  });

  // The status outranks the detail: a real number from the wire is the more
  // precise fact, and the two never disagree in practice.
  it("prefers a real status to a reason when it has both", () => {
    expect(serviceNote("failed", 502, "connection refused")).toBe(
      "502 from the service on the last check · stored value kept until you replace it",
    );
    expect(serviceNote("failed", 404, "connection refused")).toBe(serviceNote("failed", 404));
  });

  // The one status that is not the service's. `GET /api/integrations/{name}/status`
  // 404s from Alfred's own route when the registry no longer carries the name,
  // so blaming the service for it would send the reader to check a box that
  // answered nothing at all.
  it("does not blame the service for Alfred's own 404", () => {
    expect(serviceNote("failed", 404)).toBe(
      "404 · Alfred does not know this name · " +
        "the service may have unregistered since the list was read",
    );
    expect(serviceNote("failed", 404)).not.toContain("from the service");
  });

  // A new sentence, not a new state word: the row on the right still reads
  // `failed`, and the closed vocabulary is untouched.
  it("keeps the 404 inside the failed state rather than inventing a word", () => {
    expect(serviceNote("failed", 404).startsWith("404 · ")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// serviceRows
// ---------------------------------------------------------------------------

const HOME: Integration = integration();
const WEATHER: Integration = integration({
  name: "weather",
  category: "weather",
  kind: "adapter",
  schema: {
    fields: {
      api_key: {
        label: "API key",
        field_type: "password",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
    },
  },
  configured: { api_key: true },
});

const probe = (overrides: Partial<ProbeState> = {}): ProbeState => ({
  data: { name: "home-service", healthy: true, latency_ms: 210 },
  isPending: false,
  isError: false,
  status: null,
  ...overrides,
});

const rowOf = (
  entry: Integration,
  state: ProbeState | undefined,
  save?: { saving: boolean; savedAt: number | null },
) => serviceRows([entry], [state], save ? { [entry.name]: save } : {})[entry.name];

describe("serviceRows", () => {
  it("reports a healthy probe's word", () => {
    expect(rowOf(HOME, probe())).toEqual({ state: "ok", status: null, detail: null });
  });

  it("calls a service that answered healthy false failed, and sends no status with it", () => {
    // A sick service is a 200 with `healthy: false`: an answer, so there is no
    // status behind the word — unlike the branch below.
    expect(rowOf(HOME, probe({ data: { name: "home-service", healthy: false, latency_ms: 18 } })))
      .toEqual({ state: "failed", status: null, detail: null });
  });

  it("drops what the service last said when the latest attempt could not reach it", () => {
    // react-query keeps the last good answer behind a failure; reporting its
    // `detail` would explain the word `failed` with a sentence from the probe
    // before it.
    expect(rowOf(HOME, probe({ isError: true, status: 502 }))).toEqual({
      state: "failed",
      status: 502,
      // A transport failure has no body, so there is nothing the service said.
      detail: null,
    });
  });

  it("says testing while the first probe is still in flight", () => {
    expect(rowOf(HOME, probe({ data: undefined, isPending: true }))).toEqual({
      state: "testing",
      status: null,
      detail: null,
    });
  });

  it("says testing for an entry with no probe of its own yet", () => {
    expect(rowOf(HOME, undefined)).toEqual({
      state: "testing",
      status: null,
      detail: null,
    });
  });

  it("says unset when nothing at all is stored", () => {
    const entry = integration({ configured: { url: false, token: false } });
    expect(rowOf(entry, probe({ data: { name: "home-service", healthy: false, latency_ms: null } })).state)
      .toBe("unset");
  });

  it("probes a half-filled form rather than calling it unset", () => {
    const entry = integration({ configured: { url: true, token: false } });
    // Configured enough to try: saying `nothing stored` over a service that is
    // answering would be wrong.
    expect(rowOf(entry, probe()).state).toBe("ok");
  });

  it("says unset for a one-field service with nothing in its one field", () => {
    // `fields.length > 0` and not `> 1`: a service whose whole credential is a
    // single API key is the common shape, and it is exactly the one an
    // off-by-one here would quietly exempt from ever reading `nothing stored`.
    const entry = integration({
      schema: { fields: { token: integration().schema.fields.token } },
      configured: { token: false },
    });
    expect(rowOf(entry, probe()).state).toBe("unset");
  });

  it("does not call a service with no credential schema unset", () => {
    const entry = integration({ schema: { fields: {} }, configured: {} });
    expect(rowOf(entry, probe()).state).toBe("ok");
  });

  it("does not claim unset before the probe that would settle it has answered", () => {
    const entry = integration({ configured: { url: false, token: false } });
    expect(rowOf(entry, probe({ data: undefined, isPending: true })).state).toBe("testing");
  });

  it("says testing while the credentials are still going, and queued once they land", () => {
    // `saved · testing` only once the server has taken them; while the `PUT` is
    // in flight nothing has been saved yet.
    expect(rowOf(HOME, probe(), { saving: true, savedAt: null }).state).toBe("testing");
    expect(rowOf(HOME, probe(), { saving: true, savedAt: 1 }).state).toBe("queued");
  });

  it("lets a settled save fall back to what the probe found", () => {
    expect(rowOf(HOME, probe(), { saving: false, savedAt: 1 }).state).toBe("ok");
  });

  it("keeps each entry's probe to its own row", () => {
    const rows = serviceRows(
      [WEATHER, HOME],
      [probe({ data: { name: "weather", healthy: true, latency_ms: 42 } }), probe({ isError: true, status: 503 })],
      {},
    );
    expect(rows["weather"]).toEqual({ state: "ok", status: null, detail: null });
    expect(rows["home-service"]).toEqual({
      state: "failed",
      status: 503,
      detail: null,
    });
  });

  it("holds a row for every entry and nothing else", () => {
    expect(Object.keys(serviceRows([WEATHER, HOME], [probe(), probe()], {}))).toEqual([
      "weather",
      "home-service",
    ]);
    expect(serviceRows([], [], {})).toEqual({});
  });
});

describe("serviceRows · what the service said", () => {
  it("carries the reason a 200 gave for being unhealthy", () => {
    const sick = probe({
      data: {
        name: "home-service",
        healthy: false,
        latency_ms: null,
        detail: { error: "connection refused" },
      },
    });
    expect(rowOf(HOME, sick).detail).toBe("connection refused");
  });

  it("takes the first of the keys the route is known to use, and trims it", () => {
    const withKey = (detail: unknown) =>
      rowOf(HOME, probe({ data: { name: "home-service", healthy: false, latency_ms: null, detail } }))
        .detail;
    expect(withKey({ detail: "no endpoint configured" })).toBe("no endpoint configured");
    expect(withKey({ message: "timed out" })).toBe("timed out");
    expect(withKey({ error: "  refused  " })).toBe("refused");
    // `error` is the key `_service_status` actually sets, so it wins.
    expect(withKey({ message: "timed out", error: "refused" })).toBe("refused");
  });

  it("says nothing rather than guessing at a shape it does not recognise", () => {
    const withKey = (detail: unknown) =>
      rowOf(HOME, probe({ data: { name: "home-service", healthy: false, latency_ms: null, detail } }))
        .detail;
    expect(withKey(undefined)).toBeNull();
    expect(withKey(null)).toBeNull();
    expect(withKey("refused")).toBeNull();
    expect(withKey({ error: 503 })).toBeNull();
    expect(withKey({ error: "   " })).toBeNull();
    expect(withKey({})).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The credential form's rules
// ---------------------------------------------------------------------------

/** Two required fields, one required-but-transient, one optional. */
const FOUR: Integration = integration({
  schema: {
    fields: {
      username: {
        label: "Username",
        field_type: "text",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
      password: {
        label: "Password",
        field_type: "password",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
      mfa_code: {
        label: "MFA code",
        field_type: "text",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: true,
      },
      note: {
        label: "Note",
        field_type: "text",
        required: false,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
    },
  },
  configured: {},
});

describe("requiredFields", () => {
  // `validate_credential_body` 422s on any `required && !transient` field absent
  // from the body, whatever the keyring already holds — so this is the exact set
  // the form must have before it may send anything.
  it("is what the route will refuse a body without", () => {
    expect(requiredFields(FOUR)).toEqual(["username", "password"]);
  });

  // A transient field is never persisted, so insisting on it would make every
  // later rotation of another field impossible without an MFA code to hand.
  it("leaves out a field the house will not keep", () => {
    expect(requiredFields(FOUR)).not.toContain("mfa_code");
  });

  it("holds the schema's own order, so the form's marks match its boxes", () => {
    expect(requiredFields(integration())).toEqual(["url", "token"]);
  });
});

describe("missingLabels", () => {
  it("names what is still blank, in the reader's words and not the wire's", () => {
    expect(missingLabels(FOUR, {})).toEqual(["Username", "Password"]);
    expect(missingLabels(FOUR, { username: "ada" })).toEqual(["Password"]);
  });

  it("counts whitespace as blank", () => {
    expect(missingLabels(FOUR, { username: "   ", password: "hunter2" })).toEqual(["Username"]);
  });

  it("is empty once every required field has something in it", () => {
    expect(missingLabels(FOUR, { username: "ada", password: "hunter2" })).toEqual([]);
    // An optional field left blank does not hold the save.
    expect(missingLabels(FOUR, { username: "ada", password: "hunter2", note: "" })).toEqual([]);
  });
});

describe("the form's standing copy", () => {
  // The route takes the whole body or nothing, so the screen must say so before
  // the reader types rather than after the 422.
  it("says the save is all-or-nothing", () => {
    expect(SAVE_TOGETHER).toBe(
      "Every field is sent together · retype the ones already stored, or nothing is saved.",
    );
  });

  it("names the blank fields and says nothing is sent until they are filled", () => {
    expect(missingNote(["Username", "Password"])).toBe(
      "Still blank: Username, Password · nothing is sent until every field is filled.",
    );
    expect(missingNote(["Password"])).toBe(
      "Still blank: Password · nothing is sent until every field is filled.",
    );
  });

  // `saved` alone read as "no need to retype", which is the one thing the route
  // will not allow.
  it("says a stored field still has to be retyped", () => {
    expect(STORED_PLACEHOLDER).toBe("saved · retype to save");
    expect(STORED_PLACEHOLDER).toContain("retype");
  });

  it("says outright that a transient field is not kept", () => {
    expect(TRANSIENT_NOTE).toBe("not stored · sent with this save and forgotten");
  });
});

// ---------------------------------------------------------------------------
// healthGrid
// ---------------------------------------------------------------------------

describe("healthGrid", () => {
  it("claims nothing at all before the reads that would settle it", () => {
    const health = healthGrid({ overview: undefined, registryRead: false, home: undefined });
    expect(health.bus).toEqual({ value: "unknown", note: "bus · redis · not read yet", alive: false });
    expect(health.reflex).toEqual({ value: "—", note: "reflex · not read yet", alive: false });
    expect(health.rate).toEqual({
      value: "— ev/s",
      note: "event rate · 5-min mean",
      alive: false,
    });
    expect(health.home).toEqual({
      value: "—",
      note: "home assistant · not read yet",
      alive: false,
    });
  });

  it("reads the bus, the reflex and the rate off the overview", () => {
    const health = healthGrid({
      overview: overviewFixture,
      registryRead: true,
      home: probe(),
    });
    expect(health.bus).toEqual({ value: "alive", note: "bus · redis · 3 streams", alive: true });
    expect(health.reflex).toEqual({ value: "380 ms", note: "reflex · reflex-3b", alive: true });
    expect(health.rate).toEqual({
      value: "2.1 ev/s",
      note: "event rate · 5-min mean",
      alive: true,
    });
  });

  it("says unknown for a bus that has answered and is not connected", () => {
    const health = healthGrid({
      overview: { ...overviewFixture, redis: { connected: false } },
      registryRead: true,
      home: probe(),
    });
    // The note is a fact about a read that happened; only the value is in doubt.
    expect(health.bus).toEqual({ value: "unknown", note: "bus · redis · 3 streams", alive: false });
  });

  it("counts the streams the overview really carries", () => {
    const health = healthGrid({
      overview: { ...overviewFixture, streams: {} },
      registryRead: true,
      home: undefined,
    });
    expect(health.bus.note).toBe("bus · redis · 0 streams");
    // No streams is Redis down, whatever the connected flag says about itself.
    expect(health.rate).toEqual({
      value: "— ev/s",
      note: "event rate · 5-min mean",
      alive: false,
    });
  });

  // One stream is a bus that is carrying something. `> 0` and not `> 1`: a
  // household with a single stream is the smallest working Alfred there is.
  it("calls the rate alive on a house carrying a single stream", () => {
    const health = healthGrid({
      overview: {
        ...overviewFixture,
        streams: { events: overviewFixture.streams.events },
      },
      registryRead: true,
      home: undefined,
    });
    expect(health.bus.note).toBe("bus · redis · 1 stream");
    expect(health.rate.alive).toBe(true);
  });

  it("keeps a silent house's rate alive", () => {
    const health = healthGrid({
      overview: firstRunOverviewFixture,
      registryRead: true,
      home: undefined,
    });
    expect(health.rate.value).toBe("0 ev/s");
    expect(health.rate.alive).toBe(true);
  });

  it("rounds the reflex's last measure to whole milliseconds", () => {
    const health = healthGrid({
      overview: { ...overviewFixture, reflex: { model: "reflex-3b", last_ms: 379.6, p50_ms: 372 } },
      registryRead: true,
      home: undefined,
    });
    expect(health.reflex.value).toBe("380 ms");
  });

  it("says nothing rather than zero for a reflex that has not run", () => {
    const health = healthGrid({
      overview: firstRunOverviewFixture,
      registryRead: true,
      home: undefined,
    });
    expect(health.reflex).toEqual({ value: "—", note: "reflex · reflex-3b", alive: false });
  });

  it("says no model reported rather than leaving the note half-written", () => {
    const health = healthGrid({
      overview: { ...overviewFixture, reflex: { model: null, last_ms: 380, p50_ms: 372 } },
      registryRead: true,
      home: undefined,
    });
    expect(health.reflex.note).toBe("reflex · no model reported");
  });

  it("says not registered only once the registry has answered", () => {
    expect(
      healthGrid({ overview: overviewFixture, registryRead: true, home: undefined }).home,
    ).toEqual({ value: "—", note: "home assistant · not registered", alive: false });
    // Same absent probe, before the read: a different sentence.
    expect(
      healthGrid({ overview: overviewFixture, registryRead: false, home: undefined }).home.note,
    ).toBe("home assistant · not read yet");
  });

  it("says testing while the home probe is in flight", () => {
    const health = healthGrid({
      overview: overviewFixture,
      registryRead: true,
      home: probe({ data: undefined, isPending: true }),
    });
    expect(health.home).toEqual({ value: "—", note: "home assistant · testing", alive: false });
  });

  it("does not quote a round trip from a probe that failed", () => {
    const health = healthGrid({
      overview: overviewFixture,
      registryRead: true,
      home: probe({ isError: true, status: 502 }),
    });
    expect(health.home).toEqual({
      value: "failed",
      note: "home assistant · not answering",
      alive: false,
    });
  });

  it("reads the probe rather than the keyring", () => {
    // `unset` is the right word on a Services row and the wrong one here, where
    // a service that is up and answering would be reported by its credentials.
    const health = healthGrid({
      overview: overviewFixture,
      registryRead: true,
      home: probe(),
    });
    expect(health.home).toEqual({ value: "ok", note: "home assistant · 210 ms", alive: true });
  });

  it("rounds the home round trip and calls an unhealthy answer failed", () => {
    expect(
      healthGrid({
        overview: overviewFixture,
        registryRead: true,
        home: probe({ data: { name: "home-service", healthy: false, latency_ms: 17.4 } }),
      }).home,
    ).toEqual({ value: "failed", note: "home assistant · 17 ms", alive: false });
  });

  it("says no round trip measured rather than printing an empty one", () => {
    expect(
      healthGrid({
        overview: overviewFixture,
        registryRead: true,
        home: probe({ data: { name: "home-service", healthy: true, latency_ms: null } }),
      }).home,
    ).toEqual({ value: "ok", note: "home assistant · no round trip measured", alive: true });
  });
});
