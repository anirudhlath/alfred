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
 * Every instant here is built on the device's own clock rather than from a UTC
 * string, because `hhmm` reads the device's clock: `"2026-09-17T08:40:00Z"`
 * stamps 08:40 in CI and something else on a developer's machine.
 */
const CREATED = TRIGGER_CREATED_AT.toISOString();
const RUN_AT = TRIGGER_RUN_AT.toISOString();

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
});

describe("TRIGGER_KINDS", () => {
  it("lists the chips in the handoff's order, all first", () => {
    expect(TRIGGER_KINDS).toEqual(["all", "time", "schedule", "sensor", "composite"]);
  });
});

describe("triggerKind", () => {
  it("calls a time trigger with a cron a schedule", () => {
    expect(
      triggerKind(trigger({ trigger_type: "time", conditions: { cron: "0 19 * * 4", run_at: null } })),
    ).toBe("schedule");
  });

  it("calls a time trigger with a run_at a time", () => {
    expect(
      triggerKind(trigger({ trigger_type: "time", conditions: { cron: null, run_at: RUN_AT } })),
    ).toBe("time");
  });

  it("calls a time trigger with neither a time, not a schedule", () => {
    expect(triggerKind(trigger({ trigger_type: "time", conditions: {} }))).toBe("time");
  });

  it("reads a stored blank cron as no schedule at all", () => {
    expect(triggerKind(trigger({ trigger_type: "time", conditions: { cron: "" } }))).toBe("time");
  });

  it("passes sensor and composite through", () => {
    expect(
      triggerKind(
        trigger({ trigger_type: "sensor", conditions: { entity_id: "binary_sensor.front_door" } }),
      ),
    ).toBe("sensor");
    expect(
      triggerKind(trigger({ trigger_type: "composite", conditions: { children: [], require: 1 } })),
    ).toBe("composite");
  });

  it("falls back to time for a type it has never heard of", () => {
    expect(triggerKind(trigger({ trigger_type: "weather" as TriggerType }))).toBe("time");
  });
});

describe("triggerMeta", () => {
  const now = TRIGGER_NOW;

  it("prints a cron verbatim, because the next fire time is in another process", () => {
    expect(
      triggerMeta(
        trigger({
          trigger_type: "time",
          conditions: { cron: "0 19 * * 4", run_at: null },
          created_by: "conversation",
          created_at: CREATED,
        }),
        now,
      ),
    ).toBe(`recurring · cron 0 19 * * 4 · ${CREATED_CLAUSE}`);
  });

  it("formats a run_at, which it can read honestly", () => {
    expect(
      triggerMeta(
        trigger({ trigger_type: "time", one_shot: true, conditions: { run_at: RUN_AT } }),
        now,
      ),
    ).toBe(`one-shot · runs 08:40 tomorrow · ${CREATED_CLAUSE}`);
  });

  it("leaves a run_at later the same day a bare clock", () => {
    const later = new Date(2026, 8, 16, 23, 15, 0).toISOString();
    expect(triggerMeta(trigger({ conditions: { run_at: later } }), now)).toBe(
      `recurring · runs 23:15 · ${CREATED_CLAUSE}`,
    );
  });

  it("renders a run_at in the past as its date, never as tomorrow", () => {
    const past = new Date(2026, 8, 12, 8, 40, 0).toISOString();
    expect(triggerMeta(trigger({ conditions: { run_at: past } }), now)).toBe(
      `recurring · runs 08:40 12 Sep · ${CREATED_CLAUSE}`,
    );
  });

  it("names the entity a sensor trigger watches", () => {
    expect(
      triggerMeta(
        trigger({
          trigger_type: "sensor",
          conditions: { entity_id: "binary_sensor.front_door", state_match: "on" },
        }),
        now,
      ),
    ).toBe(`recurring · binary_sensor.front_door is on · ${CREATED_CLAUSE}`);
  });

  it("drops the state clause when a sensor trigger matches any state", () => {
    expect(
      triggerMeta(
        trigger({
          trigger_type: "sensor",
          conditions: { entity_id: "binary_sensor.front_door", state_match: null },
        }),
        now,
      ),
    ).toBe(`recurring · binary_sensor.front_door on any change · ${CREATED_CLAUSE}`);
  });

  it("counts what a composite needs", () => {
    expect(
      triggerMeta(
        trigger({ trigger_type: "composite", conditions: { children: [{}, {}, {}], require: 2 } }),
        now,
      ),
    ).toBe(`recurring · 2 of 3 conditions · ${CREATED_CLAUSE}`);
  });

  it("gives created_at a day label, so a week-old trigger is not a bare clock", () => {
    const week = new Date(2026, 8, 9, 20, 52, 0).toISOString();
    expect(triggerMeta(trigger({ created_at: week }), now)).toBe(
      "recurring · runs 08:40 tomorrow · created from conversation 20:52 9 Sep",
    );
  });

  it("says yesterday rather than a date when that is what it was", () => {
    const yesterday = new Date(2026, 8, 15, 20, 52, 0).toISOString();
    expect(triggerMeta(trigger({ created_at: yesterday }), now)).toBe(
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

  it("refuses to count a composite whose children it cannot read", () => {
    expect(triggerMeta(trigger({ trigger_type: "composite", conditions: {} }), now)).toBe(
      `recurring · no conditions stored · ${CREATED_CLAUSE}`,
    );
  });

  it("says a sensor trigger named no entity rather than watching undefined", () => {
    expect(triggerMeta(trigger({ trigger_type: "sensor", conditions: {} }), now)).toBe(
      `recurring · no entity stored · ${CREATED_CLAUSE}`,
    );
  });

  it("stamps an unreadable created_at rather than claiming a time", () => {
    expect(triggerMeta(trigger({ created_at: "whenever" }), now)).toBe(
      "recurring · runs 08:40 tomorrow · created from conversation --:--",
    );
  });

  it("leaves last_fired to the expanded detail, out of the three clauses", () => {
    const fired = new Date(2026, 8, 16, 21, 15, 0).toISOString();
    expect(triggerMeta(trigger({ last_fired: fired }), now)).toBe(
      `recurring · runs 08:40 tomorrow · ${CREATED_CLAUSE}`,
    );
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

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "/api/admin/triggers/trg%2Fbins%20out/enabled",
    );
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

  it("encodes the id here too", async () => {
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
