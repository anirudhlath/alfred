import { describe, expect, it } from "vitest";
import { hhmm, summarize, timeOf } from "./format";

describe("summarize", () => {
  it("summarizes state changes", () => {
    expect(
      summarize("events", { event_type: "state_changed", entity_id: "light.study", new_state: "off" }),
    ).toBe("light.study → off");
  });
  it("summarizes action requests", () => {
    expect(summarize("actions", { event_type: "action_request", tool_name: "dim_lights" })).toBe("dim_lights");
  });
  it("falls back to event_type", () => {
    expect(summarize("events", { event_type: "mystery" })).toBe("mystery");
  });
  it("summarizes an acted-on reflex observation as its tool", () => {
    expect(
      summarize("reflex_observations", {
        event_type: "reflex_observation",
        trigger_event: { entity_id: "light.study", old_state: "on", new_state: "off" },
        action: { tool_name: "dim_lights" },
        result: { status: "success" },
      }),
    ).toBe("dim_lights");
  });
  it("summarizes a passive observation as its state transition", () => {
    expect(
      summarize("reflex_observations", {
        event_type: "reflex_observation",
        trigger_event: { entity_id: "light.study", old_state: "on", new_state: "off" },
        action: null,
        result: null,
      }),
    ).toBe("light.study: on → off");
  });
  it("renders a first sighting's missing old_state as unknown", () => {
    expect(
      summarize("reflex_observations", {
        event_type: "reflex_observation",
        trigger_event: { entity_id: "sensor.hallway", new_state: "23.5" },
        action: null,
      }),
    ).toBe("sensor.hallway: unknown → 23.5");
    expect(
      summarize("reflex_observations", {
        event_type: "reflex_observation",
        trigger_event: { entity_id: "sensor.hallway", old_state: "", new_state: "23.5" },
        action: null,
      }),
    ).toBe("sensor.hallway: unknown → 23.5");
  });
  it("falls back when trigger_event is missing entirely", () => {
    expect(summarize("reflex_observations", { event_type: "reflex_observation" })).toBe(
      "observation",
    );
  });
  it("falls back for a passive observation whose trigger_event has no entity", () => {
    expect(
      summarize("reflex_observations", {
        event_type: "reflex_observation",
        trigger_event: { event_type: "action_request", tool_name: "unlock_door" },
        action: null,
      }),
    ).toBe("observation");
  });
});

describe("timeOf", () => {
  it("formats a stream id as HH:MM:SS", () => {
    expect(timeOf("1718000000000-0")).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});

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
