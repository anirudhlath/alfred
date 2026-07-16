import { describe, expect, it } from "vitest";
import { categorize, summarize, timeOf } from "./format";

describe("categorize", () => {
  it("maps streams to categories", () => {
    expect(categorize("reflex_observations", {})).toBe("reflex");
    expect(categorize("user_responses", {})).toBe("conscious");
    expect(categorize("notifications", {})).toBe("trigger");
  });
  it("maps events stream by event_type", () => {
    expect(categorize("events", { event_type: "trigger_fired" })).toBe("trigger");
    expect(categorize("events", { event_type: "state_changed" })).toBe("home");
  });
  it("maps actions by source", () => {
    expect(categorize("actions", { event_type: "action_request", source: "reflex-engine" })).toBe("reflex");
    expect(categorize("actions", { event_type: "action_request", source: "conscious" })).toBe("conscious");
  });
});

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
});

describe("timeOf", () => {
  it("formats a stream id as HH:MM:SS", () => {
    expect(timeOf("1718000000000-0")).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});
