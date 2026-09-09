import { describe, expect, it } from "vitest";
import {
  dayLabel,
  dayMonth,
  evs,
  hhmm,
  humaniseTool,
  mmss,
  notificationText,
  rawCall,
  shortId,
  usd,
} from "./format";
import type { StreamSummary } from "./types";

describe("hhmm", () => {
  it("formats a Date as a zero-padded local clock time", () => {
    expect(hhmm(new Date(2026, 8, 7, 21, 14))).toBe("21:14");
    expect(hhmm(new Date(2026, 8, 7, 7, 2))).toBe("07:02");
    expect(hhmm(new Date(2026, 8, 7, 0, 0))).toBe("00:00");
  });

  it("accepts an ISO string and an epoch", () => {
    expect(hhmm("2026-09-07T21:14:00")).toBe("21:14");
    expect(hhmm(new Date(2026, 8, 7, 18, 5).getTime())).toBe("18:05");
  });

  it("says so rather than printing NaN", () => {
    expect(hhmm("not a time")).toBe("--:--");
  });
});

describe("dayMonth", () => {
  it("formats as the design writes it", () => {
    expect(dayMonth(new Date(2026, 7, 12))).toBe("12 Aug");
    expect(dayMonth(new Date(2026, 8, 4))).toBe("4 Sep");
    expect(dayMonth(new Date(2026, 0, 1))).toBe("1 Jan");
  });
});

describe("mmss", () => {
  it("formats a fuse", () => {
    expect(mmss(252)).toBe("4:12");
    expect(mmss(300)).toBe("5:00");
    expect(mmss(9)).toBe("0:09");
    expect(mmss(0)).toBe("0:00");
  });
  it("never counts below zero", () => {
    expect(mmss(-5)).toBe("0:00");
  });
});

describe("usd", () => {
  it("is always two decimals", () => {
    expect(usd(1.42)).toBe("1.42");
    expect(usd(5)).toBe("5.00");
    expect(usd(0)).toBe("0.00");
  });
});

describe("evs", () => {
  it("sums the five-minute rates to one decimal", () => {
    expect(
      evs({
        events: { length: 1, last_id: null, last_ts: null, rate_5m: 1.4 },
        user_requests: { length: 1, last_id: null, last_ts: null, rate_5m: 0.2 },
        reflex_observations: { length: 1, last_id: null, last_ts: null, rate_5m: 0.5 },
      }),
    ).toBe("2.1");
  });
  it("counts a summary with no rate as 0, not NaN", () => {
    const unrated = { length: 3, last_id: null, last_ts: null } as StreamSummary;
    expect(evs({ events: unrated })).toBe("0");
    const rated = { length: 1, last_id: null, last_ts: null, rate_5m: 0.2 };
    expect(evs({ events: unrated, user_requests: rated })).toBe("0.2");
  });
  it("says a bare 0 when nothing is flowing", () => {
    expect(evs({})).toBe("0");
    expect(evs({ events: { length: 0, last_id: null, last_ts: null, rate_5m: 0 } })).toBe("0");
  });
});

describe("shortId", () => {
  it("is the first four characters", () => {
    expect(shortId("a91f3c2e-0b1d-4f8a")).toBe("a91f");
    expect(shortId("ab")).toBe("ab");
  });
});

describe("humaniseTool", () => {
  it("reads the last segment as a sentence", () => {
    expect(humaniseTool("home.lock_unlock")).toBe("Lock unlock");
    expect(humaniseTool("home.light_set")).toBe("Light set");
    expect(humaniseTool("speak")).toBe("Speak");
  });
  it("has something to say about nothing", () => {
    expect(humaniseTool("")).toBe("Action");
  });
});

describe("rawCall", () => {
  it("renders the call exactly as the Door shows it", () => {
    expect(
      rawCall("home.lock_unlock", { entity_id: "lock.front_door", action: "unlock" }),
    ).toBe('home.lock_unlock { entity_id: "lock.front_door", action: "unlock" }');
  });
  it("keeps non-string values as JSON", () => {
    expect(rawCall("home.light_set", { brightness_pct: 30, on: true })).toBe(
      "home.light_set { brightness_pct: 30, on: true }",
    );
  });
  it("renders an empty call", () => {
    expect(rawCall("home.ping", {})).toBe("home.ping {}");
  });
});

describe("dayLabel", () => {
  const now = new Date(2026, 8, 7, 21, 14);
  it("names today, yesterday and everything before", () => {
    expect(dayLabel(new Date(2026, 8, 7, 7, 2), now)).toBe("earlier today");
    expect(dayLabel(new Date(2026, 8, 6, 23, 59), now)).toBe("yesterday");
    expect(dayLabel(new Date(2026, 8, 4, 12, 0), now)).toBe("4 Sep");
  });
  it("calls a timestamp from a skewed clock today, not a day in the future", () => {
    expect(dayLabel(new Date(2026, 8, 8, 9, 0), now)).toBe("earlier today");
  });
});

describe("notificationText", () => {
  it("reads the body, which is the message", () => {
    expect(notificationText("The council moved collection to Friday.", "Bins go out tonight")).toBe(
      "The council moved collection to Friday.",
    );
  });
  it("falls back to the title, which is sometimes all there is", () => {
    expect(notificationText("", "Routine Suggestion")).toBe("Routine Suggestion");
  });
  it("counts a value of nothing but spaces as no value", () => {
    expect(notificationText("   ", "Routine Suggestion")).toBe("Routine Suggestion");
  });
  it("trims what it returns, so two rows of the same notification cannot differ", () => {
    expect(notificationText("  The kettle has boiled.  ", "")).toBe("The kettle has boiled.");
  });
  it("has nothing to say for a notification carrying neither", () => {
    expect(notificationText("  ", "  ")).toBeUndefined();
    expect(notificationText(undefined, null)).toBeUndefined();
  });
});
