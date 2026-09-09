import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchRoomHistory,
  pendingActionTitles,
  toTimelineItems,
  withDividers,
  type RoomHistory,
  type RoomStream,
  type TimelineItem,
} from "./history";
import {
  notificationsPage,
  reflexObservationsPage,
  userRequestsPage,
  userResponsesPage,
  yesterdayRequestPage,
} from "@/test/fixtures";

const HISTORY: RoomHistory = {
  user_requests: userRequestsPage.entries,
  user_responses: userResponsesPage.entries,
  reflex_observations: reflexObservationsPage.entries,
  notifications: notificationsPage.entries,
};

const EMPTY: RoomHistory = {
  user_requests: [],
  user_responses: [],
  reflex_observations: [],
  notifications: [],
};

/** A conversational turn at `at`, for the divider rules. */
function turnAt(at: string): TimelineItem {
  return { kind: "you", id: `you:${at}`, at, text: "And the windows?", state: "sent" };
}

afterEach(() => vi.unstubAllGlobals());

describe("fetchRoomHistory", () => {
  it("reads exactly the four Room streams, fifty each", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        urls.push(String(input));
        return new Response('{"entries":[],"next_before":null}', { status: 200 });
      }),
    );

    await fetchRoomHistory();

    expect(urls.sort()).toEqual([
      "/api/admin/streams/notifications?count=50",
      "/api/admin/streams/reflex_observations?count=50",
      "/api/admin/streams/user_requests?count=50",
      "/api/admin/streams/user_responses?count=50",
    ]);
  });

  it("loses one stream rather than the whole conversation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes("notifications"))
          return new Response('{"detail":"redis is down"}', { status: 503 });
        // A 200 with no entries at all: still a list, or the merge would throw.
        if (String(input).includes("reflex_observations"))
          return new Response('{"next_before":null}', { status: 200 });
        return new Response(JSON.stringify(userRequestsPage), { status: 200 });
      }),
    );

    const history = await fetchRoomHistory();

    expect(history.notifications).toEqual([]);
    expect(history.reflex_observations).toEqual([]);
    expect(history.user_requests).toHaveLength(2);
  });

  it("answers an empty thread, not an error, when every stream is down", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"detail":"redis is down"}', { status: 503 })),
    );

    await expect(fetchRoomHistory()).resolves.toEqual(EMPTY);
  });
});

describe("toTimelineItems", () => {
  const items = toTimelineItems(HISTORY);

  it("puts the whole thread in chronological order", () => {
    expect(items.map((item) => item.kind)).toEqual([
      "act",
      "act",
      "act",
      "you",
      "alfred",
      "you",
      "alfred",
    ]);
    const stamps = items.map((item) => Date.parse(item.at));
    expect(stamps).toEqual([...stamps].sort((a, b) => a - b));
  });

  it("renders what you said as a sent bubble", () => {
    const you = items.find((item) => item.kind === "you");
    expect(you).toEqual({
      kind: "you",
      id: "you:1788811923000-0",
      at: "2026-09-07T20:52:03",
      text: "What have I got tomorrow morning?",
      state: "sent",
    });
  });

  it("carries mood and tools onto Alfred's row", () => {
    const alfred = items.find((item) => item.kind === "alfred");
    expect(alfred).toEqual({
      kind: "alfred",
      id: "alfred:1788811926000-0",
      at: "2026-09-07T20:52:06",
      text: "The dentist at nine, sir. I'd leave by twenty to; there's rain forecast from eight.",
      mood: "pleased",
      actions: ["calendar.today", "weather.forecast"],
    });
  });

  it("keeps only the tool names Alfred's row can print", () => {
    const [alfred] = toTimelineItems({
      ...EMPTY,
      user_responses: [
        {
          id: "1-0",
          event: { timestamp: "2026-09-07T21:14:06", text: "Done.", actions_taken: ["home.lock", 42, null] },
        },
      ],
    });
    expect(alfred.kind === "alfred" && alfred.actions).toEqual(["home.lock"]);
  });

  it("reads a reflex act as its decision, in the RX hue", () => {
    expect(items[0]).toEqual({
      kind: "act",
      id: "rx:1788800280000-0",
      at: "2026-09-07T17:58:00",
      hue: 210,
      text: "movie started, evening, user home",
      meta: "17:58 · reflex · home.light_set",
    });
  });

  it("says when a reflex act failed", () => {
    const failed = items.find((item) => item.kind === "act" && item.meta.includes("home.fan_set"))!;
    expect(failed.kind === "act" && failed.meta).toBe("19:41 · reflex · home.fan_set · failed");
  });

  it("falls back to the tool name when there is no decision context", () => {
    const failed = items.find((item) => item.kind === "act" && item.meta.includes("home.fan_set"))!;
    expect(failed.kind === "act" && failed.text).toBe("home.fan_set");
  });

  it("drops the observations Alfred only watched", () => {
    expect(items.some((item) => item.kind === "act" && item.text.includes("moving about"))).toBe(
      false,
    );
  });

  it("renders a notification as its body, in the NT hue", () => {
    const nt = items.find((item) => item.kind === "act" && item.hue === 255);
    expect(nt).toEqual({
      kind: "act",
      id: "nt:1788801600000-0",
      at: "2026-09-07T18:20:00",
      hue: 255,
      text: "The door sensor saw it at 18:20.",
      meta: "18:20 · trigger:trg_parcel · important",
    });
  });

  it("falls back to the title when a notification has no body", () => {
    const [nt] = toTimelineItems({
      ...EMPTY,
      notifications: [
        {
          id: "1-0",
          event: { timestamp: "2026-09-07T18:20:00", title: "Routine Suggestion", body: "" },
        },
      ],
    });
    expect(nt.kind === "act" && nt.text).toBe("Routine Suggestion");
  });

  it("files an unlabelled notification under the house, informational", () => {
    // An empty source is no source; the fallbacks cover both.
    const [nt] = toTimelineItems({
      ...EMPTY,
      notifications: [
        {
          id: "1-0",
          event: { timestamp: "2026-09-07T18:20:00", title: "Bins go out tonight", source: "" },
        },
      ],
    });
    expect(nt.kind === "act" && nt.meta).toBe("18:20 · house · informational");
  });

  it("leaves confirmation requests to the Door", () => {
    expect(
      items.some((item) => item.kind === "act" && item.text.includes("Confirmation required")),
    ).toBe(false);
  });

  it("gives every row a stable, unique id", () => {
    const ids = items.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(toTimelineItems(HISTORY).map((item) => item.id)).toEqual(ids);
  });

  it("keeps rows apart when Redis gave two streams the same id", () => {
    // Stream ids are `<ms>-<seq>` per stream, so an observation and the
    // notification it raised can share one. The row id carries the stream.
    const shared = toTimelineItems({
      user_requests: [{ id: "1-0", event: userRequestsPage.entries[0].event }],
      user_responses: [{ id: "1-0", event: userResponsesPage.entries[0].event }],
      reflex_observations: [{ id: "1-0", event: reflexObservationsPage.entries[2].event }],
      notifications: [{ id: "1-0", event: notificationsPage.entries[1].event }],
    });
    expect(shared.map((item) => item.id).sort()).toEqual(["alfred:1-0", "nt:1-0", "rx:1-0", "you:1-0"]);
  });

  it("skips an entry whose event is unreadable rather than dying", () => {
    const parsed = toTimelineItems({
      user_requests: [{ id: "1-0", event: {} }, ...userRequestsPage.entries],
      user_responses: [],
      reflex_observations: [],
      notifications: [],
    });
    expect(parsed).toHaveLength(2);
  });

  // One guard at a time: a row without its text would print nothing, and a row
  // without its stamp would sort on NaN and read "--:--". A response's text is
  // `text`, never `content`; a request's is `content`, and an empty one is none.
  const halfEntries: Array<[RoomStream, Record<string, unknown>, Record<string, unknown>]> = [
    ["user_requests", { timestamp: "2026-09-07T21:14:00", content: "" }, { content: "Hello?" }],
    [
      "user_responses",
      { timestamp: "2026-09-07T21:14:06", content: "Yes, sir." },
      { text: "Yes, sir." },
    ],
    [
      "reflex_observations",
      { timestamp: "2026-09-07T17:58:00" },
      { action: { tool_name: "home.light_set" } },
    ],
    ["notifications", { timestamp: "2026-09-07T18:20:00" }, { title: "Your parcel arrived" }],
  ];

  it.each(halfEntries)("drops a %s entry missing its text or its stamp", (stream, noText, noStamp) => {
    const history = { ...EMPTY, [stream]: [{ id: "1-0", event: noText }, { id: "2-0", event: noStamp }] };
    expect(toTimelineItems(history)).toEqual([]);
  });
});

describe("withDividers", () => {
  const now = new Date(2026, 8, 7, 21, 30);

  it("opens each day with its own label", () => {
    const twoDays = toTimelineItems({
      user_requests: [...userRequestsPage.entries, ...yesterdayRequestPage.entries],
      user_responses: [],
      reflex_observations: [],
      notifications: [],
    });

    const labels = withDividers(twoDays, now)
      .filter((item) => item.kind === "divider")
      .map((item) => (item.kind === "divider" ? item.label : ""));

    expect(labels).toEqual(["yesterday", "earlier today"]);
  });

  it("starts a new conversation after a thirty-minute silence", () => {
    const labels = withDividers(toTimelineItems(HISTORY), now)
      .filter((item) => item.kind === "divider")
      .map((item) => (item.kind === "divider" ? item.label : ""));

    // 20:52 then 21:14 is 22 minutes — the same conversation. The act rows before
    // them are not turns and never open one.
    expect(labels).toEqual(["earlier today"]);
  });

  it("splits two turns more than thirty minutes apart", () => {
    const first = toTimelineItems({ ...EMPTY, user_requests: userRequestsPage.entries })[0];
    const later = turnAt("2026-09-07T21:44:00");

    const out = withDividers([first, later], now);

    expect(out.map((item) => (item.kind === "divider" ? item.label : item.kind))).toEqual([
      "earlier today",
      "you",
      "new conversation · 21:44",
      "you",
    ]);
    // A divider is stamped with the row it opens, so a live merge keeps it in place.
    expect(out.map((item) => item.at)).toEqual([first.at, first.at, later.at, later.at]);
  });

  it("draws the line at exactly thirty minutes", () => {
    const first = turnAt("2026-09-07T20:52:03");
    const dividers = (items: TimelineItem[]) =>
      withDividers(items, now).filter((item) => item.kind === "divider").length;

    expect(dividers([first, turnAt("2026-09-07T21:22:02")])).toBe(1);
    expect(dividers([first, turnAt("2026-09-07T21:22:03")])).toBe(2);
  });

  it("leaves an empty thread empty", () => {
    expect(withDividers([], now)).toEqual([]);
  });

  it("gives every divider a unique id", () => {
    const twoDays = toTimelineItems({
      ...EMPTY,
      user_requests: [...userRequestsPage.entries, ...yesterdayRequestPage.entries],
    });
    const out = withDividers(
      [...twoDays, turnAt("2026-09-07T21:44:00"), turnAt("2026-09-07T22:20:00")],
      now,
    );

    expect(out.filter((item) => item.kind === "divider")).toHaveLength(4);
    const ids = out.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("pendingActionTitles", () => {
  it("names an approval by its tool, for the tombstone a deep link may need", () => {
    expect(pendingActionTitles(HISTORY)).toEqual({ a91f3c2e: "Lock unlock" });
  });

  it("falls back to the notification's title, then to the id", () => {
    const titled = {
      id: "1-0",
      event: { title: "Unlock the front door?", metadata: { pending_action_id: "b7e21c40" } },
    };
    const bare = { id: "2-0", event: { metadata: { pending_action_id: "c3d9a0f1" } } };

    expect(pendingActionTitles({ ...EMPTY, notifications: [titled, bare] })).toEqual({
      b7e21c40: "Unlock the front door?",
      c3d9a0f1: "Action c3d9",
    });
  });

  it("is empty for an absent history", () => {
    expect(pendingActionTitles(undefined)).toEqual({});
  });
});
