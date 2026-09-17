import { trigger, TRIGGER_CREATED_AT, TRIGGER_NOW, TRIGGER_RUN_AT } from "@/test/fixtures";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  fetchTriggers,
  fireTrigger,
  setTriggerEnabled,
  TRIGGER_KINDS,
  triggerKind,
  triggerMeta,
  type TriggerType,
} from "./triggers";

/**
 * A stored stamp, built on the device's own clock and serialised the way
 * `model_dump_json` would. Local rather than a UTC literal because `hhmm` reads
 * the device's clock: `"2026-09-17T08:40:00Z"` stamps 08:40 in CI and something
 * else on a developer's machine.
 */
const stored = (year: number, month: number, day: number, hour: number, minute: number): string =>
  new Date(year, month, day, hour, minute, 0).toISOString();

const RUN_AT = new Date(TRIGGER_RUN_AT).toISOString();

/** `created from conversation 20:52` — the third clause, the same in most cases. */
const CREATED_CLAUSE = "created from conversation 20:52";

const respond = (body: unknown, status = 200) =>
  vi.fn<typeof fetch>(async () => new Response(JSON.stringify(body), { status }));

afterEach(() => vi.unstubAllGlobals());

describe("the shared fixture", () => {
  it("carries exactly the fields BaseTrigger dumps, and no invented one", () => {
    expect(Object.keys(trigger()).sort()).toEqual([
      "action",
      "conditions",
      "created_at",
      "created_by",
      "enabled",
      "last_fired",
      "name",
      "one_shot",
      "trigger_id",
      "trigger_type",
      "urgency",
    ]);
  });

  it("stamps itself 20:52, whatever zone the suite runs in", () => {
    expect(trigger().created_at).toBe(new Date(TRIGGER_CREATED_AT).toISOString());
    expect(new Date(TRIGGER_CREATED_AT).getHours()).toBe(20);
  });
});

describe("TRIGGER_KINDS", () => {
  it("lists the chips in the handoff's order, all first", () => {
    expect(TRIGGER_KINDS).toEqual(["all", "time", "schedule", "sensor", "composite"]);
  });
});

describe("triggerKind", () => {
  it("calls a time trigger with a cron a schedule", () => {
    const conditions = { cron: "0 19 * * 4", run_at: null };
    expect(triggerKind(trigger({ trigger_type: "time", conditions }))).toBe("schedule");
  });

  it("calls a time trigger with a run_at a time", () => {
    const once = trigger({ trigger_type: "time", conditions: { cron: null, run_at: RUN_AT } });
    expect(triggerKind(once)).toBe("time");
  });

  it("calls a time trigger with neither a time, not a schedule", () => {
    expect(triggerKind(trigger({ trigger_type: "time", conditions: {} }))).toBe("time");
  });

  it("reads a blank or padded cron as no schedule at all", () => {
    expect(triggerKind(trigger({ conditions: { cron: "" } }))).toBe("time");
    expect(triggerKind(trigger({ conditions: { cron: "   " } }))).toBe("time");
  });

  it("lets run_at win a record that carries both, as the engine does", () => {
    // `TimeTrigger.next_fire_time` returns out of its run_at branch
    // unconditionally, so the cron on such a record can never be reached.
    const both = trigger({ conditions: { cron: "0 19 * * 4", run_at: RUN_AT } });
    expect(triggerKind(both)).toBe("time");
  });

  it("passes sensor and composite through", () => {
    const sensor = trigger({
      trigger_type: "sensor",
      conditions: { entity_id: "binary_sensor.front_door" },
    });
    const composite = trigger({ trigger_type: "composite", conditions: { children: [] } });
    expect(triggerKind(sensor)).toBe("sensor");
    expect(triggerKind(composite)).toBe("composite");
  });

  it("falls back to time for a type it has never heard of", () => {
    expect(triggerKind(trigger({ trigger_type: "weather" as TriggerType }))).toBe("time");
  });

  it("still chips an unknown type by its cron, so it stays in a filtered list", () => {
    const unknown = trigger({
      trigger_type: "weather" as TriggerType,
      conditions: { cron: "0 19 * * 4" },
    });
    expect(triggerKind(unknown)).toBe("schedule");
  });
});

describe("triggerMeta", () => {
  const now = TRIGGER_NOW;

  it("prints a cron verbatim, because the next fire time is in another process", () => {
    const cron = trigger({ conditions: { cron: "0 19 * * 4", run_at: null } });
    expect(triggerMeta(cron, now)).toBe(`recurring · cron 0 19 * * 4 · ${CREATED_CLAUSE}`);
  });

  it("trims a padded cron rather than printing the record's whitespace", () => {
    const padded = trigger({ conditions: { cron: "  0 19 * * 4 " } });
    expect(triggerMeta(padded, now)).toBe(`recurring · cron 0 19 * * 4 · ${CREATED_CLAUSE}`);
  });

  it("formats a run_at, which it can read honestly", () => {
    const once = trigger({ one_shot: true, conditions: { run_at: RUN_AT } });
    expect(triggerMeta(once, now)).toBe(`one-shot · runs 08:40 tomorrow · ${CREATED_CLAUSE}`);
  });

  it("leaves a run_at still ahead today a bare clock", () => {
    const later = trigger({ conditions: { run_at: stored(2026, 8, 16, 23, 15) } });
    expect(triggerMeta(later, now)).toBe(`recurring · runs 23:15 · ${CREATED_CLAUSE}`);
  });

  it("says a run_at that was due this morning is behind, not merely today", () => {
    const missed = trigger({ conditions: { run_at: stored(2026, 8, 16, 8, 0) } });
    expect(triggerMeta(missed, now)).toBe(
      `recurring · runs 08:00 earlier today · ${CREATED_CLAUSE}`,
    );
  });

  it("renders a run_at days in the past as its date, never as tomorrow", () => {
    const past = trigger({ conditions: { run_at: stored(2026, 8, 12, 8, 40) } });
    expect(triggerMeta(past, now)).toBe(`recurring · runs 08:40 12 Sep · ${CREATED_CLAUSE}`);
  });

  it("reads an epoch-0 run_at as a field never set, not as 1970", () => {
    const never = trigger({ conditions: { run_at: "1970-01-01T00:00:00Z" } });
    expect(triggerMeta(never, now)).toBe(`recurring · no schedule stored · ${CREATED_CLAUSE}`);
  });

  it("draws the run_at of a record carrying both, which is the one that fires", () => {
    const both = trigger({ conditions: { cron: "0 19 * * 4", run_at: RUN_AT } });
    expect(triggerMeta(both, now)).toBe(`recurring · runs 08:40 tomorrow · ${CREATED_CLAUSE}`);
  });

  it("names the entity a sensor trigger watches", () => {
    const sensor = trigger({
      trigger_type: "sensor",
      conditions: { entity_id: "binary_sensor.front_door", state_match: "on" },
    });
    expect(triggerMeta(sensor, now)).toBe(
      `recurring · binary_sensor.front_door is on · ${CREATED_CLAUSE}`,
    );
  });

  it("drops the state clause when a sensor trigger matches any state", () => {
    const sensor = trigger({
      trigger_type: "sensor",
      conditions: { entity_id: "binary_sensor.front_door", state_match: null },
    });
    expect(triggerMeta(sensor, now)).toBe(
      `recurring · binary_sensor.front_door on any change · ${CREATED_CLAUSE}`,
    );
  });

  it("names the attributes a sensor match requires, which narrow it too", () => {
    const sensor = trigger({
      trigger_type: "sensor",
      conditions: {
        entity_id: "sensor.washer",
        state_match: null,
        attribute_match: { power_w: 0, mode: "eco" },
      },
    });
    expect(triggerMeta(sensor, now)).toBe(
      `recurring · sensor.washer with power_w 0, mode "eco" · ${CREATED_CLAUSE}`,
    );
  });

  it("keeps both clauses when a sensor match wants a state and an attribute", () => {
    const sensor = trigger({
      trigger_type: "sensor",
      conditions: {
        entity_id: "binary_sensor.front_door",
        state_match: "on",
        attribute_match: { battery: 12 },
      },
    });
    expect(triggerMeta(sensor, now)).toBe(
      `recurring · binary_sensor.front_door is on with battery 12 · ${CREATED_CLAUSE}`,
    );
  });

  it("reads an empty attribute_match as the constraint it is not", () => {
    const sensor = trigger({
      trigger_type: "sensor",
      conditions: { entity_id: "sensor.washer", attribute_match: {} },
    });
    expect(triggerMeta(sensor, now)).toBe(
      `recurring · sensor.washer on any change · ${CREATED_CLAUSE}`,
    );
  });

  it("says a sensor trigger stored no entity rather than naming undefined", () => {
    expect(triggerMeta(trigger({ trigger_type: "sensor", conditions: {} }), now)).toBe(
      `recurring · no entity stored · ${CREATED_CLAUSE}`,
    );
  });

  it("counts what a composite needs", () => {
    const composite = trigger({
      trigger_type: "composite",
      conditions: { children: [{}, {}, {}], require: 2 },
    });
    expect(triggerMeta(composite, now)).toBe(`recurring · 2 of 3 conditions · ${CREATED_CLAUSE}`);
  });

  it("refuses to count a composite that stored children but no require", () => {
    const composite = trigger({
      trigger_type: "composite",
      conditions: { children: [{}, {}, {}] },
    });
    expect(triggerMeta(composite, now)).toBe(
      `recurring · no conditions stored · ${CREATED_CLAUSE}`,
    );
  });

  it("refuses to count a composite that stored require but no children", () => {
    const composite = trigger({ trigger_type: "composite", conditions: { require: 2 } });
    expect(triggerMeta(composite, now)).toBe(
      `recurring · no conditions stored · ${CREATED_CLAUSE}`,
    );
  });

  it("refuses a require that is not a number it can draw", () => {
    const composite = trigger({
      trigger_type: "composite",
      conditions: { children: [{}, {}, {}], require: Number.NaN },
    });
    expect(triggerMeta(composite, now)).toBe(
      `recurring · no conditions stored · ${CREATED_CLAUSE}`,
    );
  });

  it("clamps a require that is negative or fractional to a whole count", () => {
    const meta = (require: number) =>
      triggerMeta(
        trigger({ trigger_type: "composite", conditions: { children: [{}, {}, {}], require } }),
        now,
      );
    expect(meta(-1)).toBe(`recurring · 0 of 3 conditions · ${CREATED_CLAUSE}`);
    expect(meta(2.6)).toBe(`recurring · 2 of 3 conditions · ${CREATED_CLAUSE}`);
  });

  it("gives created_at a day label, so a week-old trigger is not a bare clock", () => {
    const week = trigger({ created_at: stored(2026, 8, 9, 20, 52) });
    expect(triggerMeta(week, now)).toBe(
      "recurring · runs 08:40 tomorrow · created from conversation 20:52 9 Sep",
    );
  });

  it("says yesterday rather than a date when that is what it was", () => {
    const yesterday = trigger({ created_at: stored(2026, 8, 15, 20, 52) });
    expect(triggerMeta(yesterday, now)).toBe(
      "recurring · runs 08:40 tomorrow · created from conversation 20:52 yesterday",
    );
  });

  it("says nothing it cannot read rather than guessing", () => {
    expect(triggerMeta(trigger({ trigger_type: "time", conditions: {} }), now)).toBe(
      `recurring · no schedule stored · ${CREATED_CLAUSE}`,
    );
  });

  it("treats an unreadable run_at as no schedule, not as the epoch", () => {
    expect(triggerMeta(trigger({ conditions: { run_at: "soon" } }), now)).toBe(
      `recurring · no schedule stored · ${CREATED_CLAUSE}`,
    );
  });

  it("stamps an unreadable created_at rather than claiming a time", () => {
    expect(triggerMeta(trigger({ created_at: "whenever" }), now)).toBe(
      "recurring · runs 08:40 tomorrow · created from conversation --:--",
    );
  });

  it("leaves last_fired to the expanded detail, out of the three clauses", () => {
    const fired = trigger({ last_fired: stored(2026, 8, 16, 21, 15) });
    expect(triggerMeta(fired, now)).toBe(`recurring · runs 08:40 tomorrow · ${CREATED_CLAUSE}`);
  });
});

describe("fetchTriggers", () => {
  it("unwraps the envelope the admin route answers in", async () => {
    const fetchMock = respond({ triggers: [trigger()] });
    vi.stubGlobal("fetch", fetchMock);

    expect(await fetchTriggers()).toEqual([trigger()]);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/admin/triggers");
  });

  it("reads a body with no triggers as none, not as a crash", async () => {
    vi.stubGlobal("fetch", respond({}));
    expect(await fetchTriggers()).toEqual([]);
  });
});

describe("setTriggerEnabled", () => {
  it("posts the new state to the trigger's own route", async () => {
    const fetchMock = respond({ status: "queued", effective_within_seconds: 60 });
    vi.stubGlobal("fetch", fetchMock);

    await setTriggerEnabled("trg_bins", false);

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/admin/triggers/trg_bins/enabled");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ enabled: false });
  });

  it("encodes an id the LLM invented, so a slash cannot become a path", async () => {
    const fetchMock = respond({ status: "queued" });
    vi.stubGlobal("fetch", fetchMock);

    await setTriggerEnabled("trg/bins out", true);

    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/admin/triggers/trg%2Fbins%20out/enabled");
  });

  it("lets a 500 through, for the row that asked to carry it", async () => {
    vi.stubGlobal("fetch", respond({ detail: "corrupt stored data" }, 500));
    await expect(setTriggerEnabled("trg_bins", true)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("fireTrigger", () => {
  it("posts to the fire route with no body of its own", async () => {
    const fetchMock = respond({ status: "queued", trigger_id: "trg_bins" });
    vi.stubGlobal("fetch", fetchMock);

    await fireTrigger("trg_bins");

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/admin/triggers/trg_bins/fire");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
  });

  it("keeps a slash in an id out of the path on this route too", async () => {
    const fetchMock = respond({ status: "queued" });
    vi.stubGlobal("fetch", fetchMock);

    await fireTrigger("trg/bins out");

    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/admin/triggers/trg%2Fbins%20out/fire");
  });

  it("lets a 404 through rather than reporting a fire that never queued", async () => {
    vi.stubGlobal("fetch", respond({ detail: "Unknown trigger 'trg_gone'" }, 404));
    await expect(fireTrigger("trg_gone")).rejects.toBeInstanceOf(ApiError);
  });
});
