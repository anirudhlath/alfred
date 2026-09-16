import { authSession, credential, SYSTEM_NOW, SYSTEM_SIGNED_IN_AT } from "@/test/fixtures";
import { describe, expect, it } from "vitest";
import { credentialMeta, serviceNote, sessionMeta, spendFraction, spendNote } from "./system";

/** 23:50 the night before `SYSTEM_NOW` — an 8 h session can only cross one midnight. */
const LAST_NIGHT = new Date(2026, 8, 15, 23, 50, 0).toISOString();

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

  it("leaves out an address the server did not send", () => {
    const meta = sessionMeta(authSession({ ip: "" }), SYSTEM_NOW);
    expect(meta).toBe("passkey · web · signed in 07:02");
    expect(meta.endsWith(" · ")).toBe(false);
  });

  it("says this device for the current session", () => {
    expect(sessionMeta(authSession({ current: true }), SYSTEM_NOW)).toBe(
      "passkey · web · this device · signed in 07:02 · 192.168.1.24",
    );
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
});

describe("credentialMeta", () => {
  it("names the transports and the last use", () => {
    expect(
      credentialMeta(
        credential({
          transports: ["internal", "hybrid"],
          last_used_at: new Date(SYSTEM_SIGNED_IN_AT).toISOString(),
        }),
        SYSTEM_NOW,
      ),
    ).toBe("internal, hybrid · last used 07:02");
  });

  it("says never used rather than an empty stamp", () => {
    expect(credentialMeta(credential({ last_used_at: null }), SYSTEM_NOW)).toBe(
      "internal · never used",
    );
  });

  it("says no transports recorded for an empty list", () => {
    expect(credentialMeta(credential({ transports: [] }), SYSTEM_NOW)).toBe(
      "no transports recorded · last used 07:02",
    );
  });

  it("names the day a passkey was last used, when it was not today", () => {
    expect(credentialMeta(credential({ last_used_at: LAST_NIGHT }), SYSTEM_NOW)).toBe(
      "internal · last used 23:50 yesterday",
    );
  });
});

describe("spendNote", () => {
  it("states the spend against the cap and the request count", () => {
    expect(spendNote(COST)).toBe("$0.42 of $5.00 today · 118 requests · $0.0036 each");
  });

  it("drops the clauses the server did not send", () => {
    expect(spendNote({ date: "2026-09-16", spend_usd: 0.42, cap_usd: 5 })).toBe(
      "$0.42 of $5.00 today",
    );
  });

  it("says no spend recorded today for a null cost", () => {
    expect(spendNote(null)).toBe("no spend recorded today");
  });

  it("does not divide by a zero cap", () => {
    const note = spendNote({ ...COST, cap_usd: 0 });
    expect(note).toBe("$0.42 today · no cap set · 118 requests · $0.0036 each");
    expect(note).not.toContain("NaN");
    expect(spendFraction({ ...COST, cap_usd: 0 })).toBe(0);
  });

  it("prints a per-request cost the two-decimal form would round away", () => {
    // `usd()` is `toFixed(2)`, so it reads $0.0036 as "0.00" — the clause needs
    // four places, trimmed back to the two every other money string here has.
    expect(spendNote({ ...COST, avg_usd: 0.037 })).toContain("$0.037 each");
    expect(spendNote({ ...COST, avg_usd: 0.5 })).toContain("$0.50 each");
    expect(spendNote({ ...COST, avg_usd: 0 })).toContain("$0.00 each");
  });

  it("counts a day with no requests rather than dropping the clause", () => {
    expect(spendNote({ date: "2026-09-16", spend_usd: 0, cap_usd: 5, request_count: 0 })).toBe(
      "$0.00 of $5.00 today · 0 requests",
    );
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
});

describe("serviceNote", () => {
  it("maps each state to its sentence", () => {
    expect(serviceNote("ok")).toBe("reachable");
    expect(serviceNote("failed")).toBe("not answering");
    expect(serviceNote("unset")).toBe("no credentials saved");
    expect(serviceNote("testing")).toBe("testing…");
    expect(serviceNote("queued")).toBe("saved · testing");
  });
});
