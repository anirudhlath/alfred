import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  compareIds,
  fetchStreamPage,
  idMs,
  isStreamName,
  record,
  ring,
  ringFill,
  ringText,
  scalar,
  STREAM_INFO,
  STREAMS,
  streamLabel,
  strings,
  summarise,
} from "./streams";
import type { StreamName } from "./streams";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("STREAMS", () => {
  it("lists the eight catalogued streams in the handoff's chip order", () => {
    expect(STREAMS).toEqual([
      "user_requests",
      "user_responses",
      "events",
      "actions",
      "reflex_observations",
      "notifications",
      "home_state",
      "home_action_results",
    ]);
    expect(STREAMS.map((name) => STREAM_INFO[name].mono)).toEqual([
      "UR", "AL", "EV", "AC", "RX", "NT", "HS", "HR",
    ]);
    expect(STREAMS.map((name) => STREAM_INFO[name].hue)).toEqual([
      30, 75, 120, 165, 210, 255, 300, 345,
    ]);
    expect(isStreamName("home_state")).toBe(true);
    expect(isStreamName("home-state")).toBe(false);
  });
});

describe("ring", () => {
  it("is the handoff's one chroma and lightness at the stream's hue", () => {
    expect(ring(210)).toBe("oklch(0.62 0.11 210)");
    expect(STREAMS.map((name) => ring(STREAM_INFO[name].hue))).toContain("oklch(0.62 0.11 30)");
  });

  it("darkens the hue under white text and tokenises it as text on the page", () => {
    // White on L 0.62 is 3.45:1 at 9-10 px; 0.52 is 5.11:1 on the worst hue.
    expect(ringFill(210)).toBe("oklch(0.52 0.11 210)");
    // Per-theme lightness: 0.52 on paper (4.62:1), 0.75 on ink (6.76:1).
    expect(ringText(210)).toBe("oklch(var(--ring-text-l) 0.11 210)");
    // One hue, three uses — the three must never drift apart.
    expect([ring(30), ringFill(30), ringText(30)].every((c) => c.includes("0.11 30"))).toBe(true);
  });
});

describe("streamLabel", () => {
  it("says the stream's name in words, for a screen reader", () => {
    expect(streamLabel("home_state")).toBe("home state");
    expect(STREAMS.map(streamLabel)).toContain("reflex observations");
    expect(STREAMS.every((name) => !streamLabel(name).includes("_"))).toBe(true);
  });
});

describe("stream ids", () => {
  it("reads the millisecond half of an id, and 0 for garbage", () => {
    expect(idMs("1788815640000-0")).toBe(1788815640000);
    expect(idMs("garbage")).toBe(0);
  });

  it("orders by milliseconds, then sequence", () => {
    expect(compareIds("1788815640000-0", "1788815640000-1")).toBeLessThan(0);
    expect(compareIds("1788815640001-0", "1788815640000-9")).toBeGreaterThan(0);
    expect(compareIds("1788815640000-2", "1788815640000-2")).toBe(0);
  });
});

describe("scalar, record, strings", () => {
  it("scalar: prints a number or a boolean, and refuses everything wordless", () => {
    expect(scalar("home.light_set")).toBe("home.light_set");
    expect(scalar(0)).toBe("0");
    expect(scalar(false)).toBe("false");
    expect(scalar("")).toBeNull();
    expect(scalar("   ")).toBeNull();
    expect(scalar({})).toBeNull();
    expect(scalar([])).toBeNull();
    expect(scalar(null)).toBeNull();
    expect(scalar(undefined)).toBeNull();
  });

  it("record: an object, never an array and never null", () => {
    const event = { a: 1 };
    expect(record(event)).toBe(event);
    expect(record([])).toBeNull();
    expect(record(null)).toBeNull();
    expect(record("light.living_room")).toBeNull();
  });

  it("strings: the strings of an array, nothing of anything else", () => {
    expect(strings(["a", 5, null])).toEqual(["a"]);
    expect(strings([])).toEqual([]);
    expect(strings("nope")).toEqual([]);
    expect(strings(undefined)).toEqual([]);
  });
});

describe("fetchStreamPage", () => {
  it("asks for a page of fifty, before a cursor when given one", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ entries: [], next_before: null }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchStreamPage("events");
    await fetchStreamPage("events", "1788815640000-0");
    await fetchStreamPage("events", null, 100);

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/admin/streams/events?count=50",
      "/api/admin/streams/events?count=50&before=1788815640000-0",
      "/api/admin/streams/events?count=100",
    ]);
  });

  it("treats a blank cursor as no cursor", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ entries: [], next_before: null }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchStreamPage("events", "");

    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/admin/streams/events?count=50");
  });

  it("clamps the count to the server's 1..200 rather than asking for a 422", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ entries: [], next_before: null }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchStreamPage("events", null, 0);
    await fetchStreamPage("events", null, 999);
    await fetchStreamPage("events", null, Number.NaN);

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/admin/streams/events?count=50",
      "/api/admin/streams/events?count=200",
      "/api/admin/streams/events?count=50",
    ]);
  });

  it("normalises a page with nothing in it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    expect(await fetchStreamPage("events")).toEqual({ entries: [], next_before: null });
  });

  it("normalises a body that is literally null", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("null", { status: 200 })));
    expect(await fetchStreamPage("events")).toEqual({ entries: [], next_before: null });
  });

  it("lets the server's refusal through, for the bench to show", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "redis unavailable" }), { status: 500 })),
    );
    await expect(fetchStreamPage("events")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("summarise", () => {
  it("user_requests: the words, then session · channel · type", () => {
    expect(
      summarise("user_requests", {
        content: "Is the back door locked?",
        session_id: "s_9f3",
        channel: "web_pwa",
        content_type: "text",
      }),
    ).toEqual({ text: "Is the back door locked?", meta: "session s_9f3 · web_pwa · text" });
    expect(
      summarise("user_requests", { content: "", session_id: "s_9f3", channel: "voice", content_type: "audio" }),
    ).toEqual({ text: "voice message", meta: "session s_9f3 · voice · audio" });
    // Only an audio request is a voice message; a text one that arrived empty says so.
    expect(
      summarise("user_requests", { content: "", session_id: "s_9f3", channel: "web_pwa", content_type: "text" }),
    ).toEqual({ text: "no content", meta: "session s_9f3 · web_pwa · text" });
  });

  it("user_responses: the reply, then session · mood · tools", () => {
    expect(
      summarise("user_responses", {
        text: "It is locked, sir.",
        session_id: "s_9f3",
        mood: "pleased",
        actions_taken: ["home.get_state", "weather.forecast"],
      }),
    ).toEqual({
      text: "It is locked, sir.",
      meta: "session s_9f3 · mood pleased · home.get_state, weather.forecast",
    });
    expect(summarise("user_responses", { text: "Certainly.", session_id: "s_9f3" })).toEqual({
      text: "Certainly.",
      meta: "session s_9f3 · mood neutral · no tools",
    });
    // Whitespace is not words: it falls back like an absent field does.
    expect(summarise("user_responses", { text: "   " }).text).toBe("reply");
  });

  it("events: trigger fired, trigger created, service registered, anything else", () => {
    expect(
      summarise("events", {
        event_type: "trigger_fired",
        trigger_name: "front door open",
        trigger_type: "sensor",
        urgency: "important",
        fired_by: "engine",
      }),
    ).toEqual({ text: "trigger fired: front door open", meta: "fired by engine · sensor · important" });
    expect(
      summarise("events", {
        event_type: "trigger_created",
        name: "bins out",
        trigger_type: "time",
        created_by: "conscious",
        one_shot: true,
      }),
    ).toEqual({ text: "trigger created: bins out", meta: "one-shot · time · by conscious" });
    expect(
      summarise("events", { event_type: "service_registered", service_name: "home-service", source: "home-service" }),
    ).toEqual({ text: "service registered: home-service", meta: "source home-service" });
    expect(summarise("events", { event_type: "state_changed", source: "bus" })).toEqual({
      text: "state changed",
      meta: "source bus",
    });
  });

  it("actions: tool and entity, then request · service · source · confirmed", () => {
    expect(
      summarise("actions", {
        tool_name: "home.light_set",
        parameters: { entity_id: "light.living_room", brightness: 30 },
        request_id: "4b1d9e00",
        target_service: "home-service",
        source: "reflex",
        confirmed: false,
      }),
    ).toEqual({
      text: "home.light_set light.living_room",
      meta: "request 4b1d · home-service · reflex",
    });
    expect(
      summarise("actions", {
        tool_name: "home.lock_unlock",
        parameters: {},
        request_id: "a91f3c2e",
        target_service: "home-service",
        source: "conscious",
        confirmed: true,
      }),
    ).toEqual({ text: "home.lock_unlock", meta: "request a91f · home-service · conscious · confirmed" });
  });

  it("reflex_observations: what was seen and whether it acted, then tool · request · decision", () => {
    expect(
      summarise("reflex_observations", {
        origin: "state_change",
        trigger_event: { entity_id: "media_player.tv", new_state: "playing" },
        action: { request_id: "4b1d9e00", tool_name: "home.light_set" },
        result: { status: "success" },
        decision_context: "movie started, evening, user home",
      }),
    ).toEqual({
      text: "observed media_player.tv · acted",
      meta: 'home.light_set · request 4b1d · decision "movie started, evening, user home"',
    });
    expect(
      summarise("reflex_observations", {
        origin: "state_change",
        trigger_event: { entity_id: "fan.bathroom", new_state: "on" },
        action: { request_id: "8d2a0000", tool_name: "home.fan_set" },
        result: { status: "error", error: "service unavailable" },
        decision_context: null,
      }),
    ).toEqual({ text: "observed fan.bathroom · acted", meta: "home.fan_set · request 8d2a · failed" });
    expect(
      summarise("reflex_observations", {
        origin: "trigger_fired",
        trigger_event: { trigger_name: "front door open" },
        action: null,
        result: null,
        decision_context: "user moving about, lights already on",
      }),
    ).toEqual({
      text: "observed front door open · watched, took no action",
      meta: 'decision "user moving about, lights already on"',
    });
  });

  it("notifications: the body, then urgency · source · confirmation", () => {
    expect(
      summarise("notifications", {
        title: "Your parcel arrived",
        body: "The door sensor saw it at 18:20.",
        urgency: "important",
        source: "trigger-engine",
        metadata: {},
      }),
    ).toEqual({ text: "The door sensor saw it at 18:20.", meta: "important · trigger-engine" });
    expect(
      summarise("notifications", {
        title: "Confirmation required",
        body: "",
        urgency: "urgent",
        source: "domain-router",
        metadata: { pending_action_id: "a91f3c2e" },
      }),
    ).toEqual({ text: "Confirmation required", meta: "urgent · domain-router · confirmation a91f" });
  });

  it("home_state: entity → state, then domain · was · via", () => {
    expect(
      summarise("home_state", {
        entity_id: "binary_sensor.front_door",
        new_state: "on",
        old_state: "off",
        domain: "home",
        source: "home-service",
      }),
    ).toEqual({ text: "binary_sensor.front_door → on", meta: "home · was off · via home-service" });
    expect(
      summarise("home_state", { entity_id: "light.hall", new_state: "off", domain: "home", source: "home-service" }),
    ).toEqual({ text: "light.hall → off", meta: "home · was unknown · via home-service" });
  });

  it("home_action_results: tool and status, then request · error", () => {
    expect(
      summarise("home_action_results", { tool_name: "home.light_set", status: "success", request_id: "4b1d9e00" }),
    ).toEqual({ text: "home.light_set success", meta: "request 4b1d" });
    expect(
      summarise("home_action_results", {
        tool_name: "home.fan_set",
        status: "error",
        request_id: "8d2a0000",
        error: "service unavailable",
      }),
    ).toEqual({ text: "home.fan_set error", meta: "request 8d2a · service unavailable" });
  });

  it("degrades on a wrong-typed field rather than printing null or [object Object]", () => {
    // A producer that sent a string where the schema says object, and so on. Each
    // line loses the part it cannot read and keeps the rest.
    expect(
      summarise("actions", {
        tool_name: "home.light_set",
        parameters: "light.living_room",
        request_id: "4b1d9e00",
      }),
    ).toEqual({ text: "home.light_set", meta: "request 4b1d" });
    expect(
      summarise("notifications", {
        title: "Your parcel arrived",
        body: "The door sensor saw it at 18:20.",
        urgency: "urgent",
        source: "domain-router",
        metadata: [],
      }),
    ).toEqual({ text: "The door sensor saw it at 18:20.", meta: "urgent · domain-router" });
    expect(
      summarise("reflex_observations", {
        origin: "state_change",
        trigger_event: [],
        action: null,
        result: "error",
        decision_context: null,
      }),
    ).toEqual({ text: "observed state_change · watched, took no action", meta: "" });
    expect(
      summarise("user_responses", { text: "Hi.", session_id: "s_1", actions_taken: ["a", null, 5] }),
    ).toEqual({ text: "Hi.", meta: "session s_1 · mood neutral · a" });
  });

  it("says the name of a stream it has never heard of", () => {
    // A ninth stream reaches the client as a string long before it reaches this file.
    expect(summarise("ninth_stream" as StreamName, { source: "bus" })).toEqual({
      text: "ninth stream",
      meta: "source bus",
    });
  });

  it("has a line for an event with none of the fields, on every stream", () => {
    const empty = STREAMS.map((stream) => [stream, summarise(stream, {})] as const);
    expect(Object.fromEntries(empty)).toEqual({
      user_requests: { text: "no content", meta: "" },
      user_responses: { text: "reply", meta: "mood neutral · no tools" },
      events: { text: "event", meta: "" },
      actions: { text: "action", meta: "" },
      reflex_observations: { text: "observed event · watched, took no action", meta: "" },
      notifications: { text: "notification", meta: "" },
      home_state: { text: "entity → unknown", meta: "was unknown" },
      home_action_results: { text: "action unknown", meta: "" },
    });
    for (const [, { text, meta }] of empty) {
      expect(text).not.toMatch(/null|undefined|\[object Object\]/);
      expect(meta).not.toMatch(/null|undefined|\[object Object\]/);
    }
  });
});
