import { describe, expect, it } from "vitest";
import {
  dayLabel,
  dayMonth,
  evs,
  hhmm,
  hhmmss,
  humaniseTool,
  mmss,
  notificationText,
  pastLabel,
  rawCall,
  shortId,
  usd,
  whenLabel,
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

describe("hhmmss", () => {
  it("prints the device's clock to the second", () => {
    expect(hhmmss(new Date(2026, 8, 7, 21, 2, 11))).toBe("21:02:11");
    expect(hhmmss(new Date(2026, 8, 7, 0, 0, 0).getTime())).toBe("00:00:00");
  });

  it("says nothing it cannot read", () => {
    expect(hhmmss("not a date")).toBe("--:--:--");
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

describe("whenLabel", () => {
  const now = new Date(2026, 8, 16, 21, 30);

  it("leaves a moment still ahead today a bare clock", () => {
    expect(whenLabel(new Date(2026, 8, 16, 23, 15), now)).toBe("23:15");
  });

  it("names tomorrow, which dayLabel would call earlier today", () => {
    expect(whenLabel(new Date(2026, 8, 17, 8, 40), now)).toBe("08:40 tomorrow");
  });

  it("dates anything further ahead than tomorrow", () => {
    expect(whenLabel(new Date(2026, 8, 24, 8, 40), now)).toBe("08:40 24 Sep");
  });

  it("says a moment already behind is behind, not merely today", () => {
    expect(whenLabel(new Date(2026, 8, 16, 8, 0), now)).toBe("08:00 earlier today");
    expect(whenLabel(new Date(2026, 8, 16, 21, 30), now)).toBe("21:30 earlier today");
  });

  it("hands the past to dayLabel, so the two never spell a day differently", () => {
    expect(whenLabel(new Date(2026, 8, 15, 20, 52), now)).toBe("20:52 yesterday");
    expect(whenLabel(new Date(2026, 8, 9, 20, 52), now)).toBe("20:52 9 Sep");
  });
});

describe("pastLabel", () => {
  const now = new Date(2026, 8, 16, 21, 30);

  it("leaves today a bare clock, however far back in the day", () => {
    expect(pastLabel(new Date(2026, 8, 16, 20, 52), now)).toBe("20:52");
    expect(pastLabel(new Date(2026, 8, 16, 0, 1), now)).toBe("00:01");
  });

  it("names the day as soon as it is not today", () => {
    expect(pastLabel(new Date(2026, 8, 15, 20, 52), now)).toBe("20:52 yesterday");
    expect(pastLabel(new Date(2026, 8, 9, 20, 52), now)).toBe("20:52 9 Sep");
  });

  it("labels a stamp from a clock running ahead rather than swallowing it", () => {
    expect(pastLabel(new Date(2026, 8, 17, 8, 40), now)).toBe("08:40 tomorrow");
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
