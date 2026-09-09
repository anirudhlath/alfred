import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TimelineItem } from "@/lib/history";
import type { ChatServerMessage } from "@/lib/types";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { NO_REPLY_MS, UNSENT_KEY, useRoom, type UseRoomOptions } from "./useRoom";

const { chats, playWavBase64Mock } = vi.hoisted(() => ({
  chats: [] as unknown[],
  playWavBase64Mock: vi.fn(),
}));

let sendSucceeds = true;

vi.mock("@/lib/audio", () => ({
  playWavBase64: playWavBase64Mock,
  installAudioUnlock: () => () => {},
  getAudioContext: () => null,
}));

vi.mock("@/lib/chat-socket", () => {
  class ChatSocket {
    onstatus: (status: string) => void = () => {};
    listeners = new Set<(msg: ChatServerMessage) => void>();
    connect = vi.fn();
    close = vi.fn();
    sendText = vi.fn(() => sendSucceeds);
    sendAudio = vi.fn(() => sendSucceeds);
    constructor() {
      chats.push(this);
    }
    listen(fn: (msg: ChatServerMessage) => void): () => void {
      this.listeners.add(fn);
      return () => void this.listeners.delete(fn);
    }
    deliver(msg: ChatServerMessage): void {
      for (const fn of [...this.listeners]) fn(msg);
    }
  }
  return { ChatSocket };
});

vi.mock("@/lib/telemetry-socket", () => ({
  TelemetrySocket: class {
    onstatus = () => {};
    connect() {}
    close() {}
    subscribe() {}
    listen() {
      return () => {};
    }
  },
}));

interface FakeChat {
  onstatus: (status: string) => void;
  sendText: ReturnType<typeof vi.fn>;
  sendAudio: ReturnType<typeof vi.fn>;
  deliver: (msg: ChatServerMessage) => void;
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function Wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <ConnectionProvider>{children}</ConnectionProvider>
    </QueryClientProvider>
  );
}

// `online` is the socket's word, not a prop: the provider hears it from the
// ChatSocket, so the harness says it the same way.
function renderRoom({ online, ...props }: UseRoomOptions & { online: boolean }) {
  const view = renderHook((options: UseRoomOptions) => useRoom(options), {
    wrapper: Wrapper,
    initialProps: props,
  });
  const chat = chats.at(-1) as FakeChat;
  if (online) act(() => chat.onstatus("online"));
  return { ...view, chat };
}

// Dated well in the past: useRoom sorts by timestamp, and the live rows are
// stamped with the real clock, so history must not be "later than now".
const historyRow: TimelineItem = {
  kind: "alfred",
  id: "alfred:history",
  at: "2026-09-01T20:52:06",
  text: "The dentist at nine, sir.",
  mood: "pleased",
  actions: ["calendar.today"],
};

function kinds(items: TimelineItem[]): string[] {
  return items.filter((item) => item.kind !== "divider").map((item) => item.kind);
}

beforeEach(() => {
  sendSucceeds = true;
  playWavBase64Mock.mockClear();
  localStorage.clear();
  (chats.at(-1) as FakeChat | undefined)?.sendText.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
  localStorage.clear();
});

describe("useRoom — the merged thread", () => {
  it("keeps history and live rows in one chronological list", () => {
    const { result, chat } = renderRoom({ history: [historyRow], online: true });

    act(() => result.current.sendText("Is the back door locked?"));

    expect(kinds(result.current.items)).toEqual(["alfred", "you", "thinking"]);
    expect(chat.sendText).toHaveBeenCalledWith("Is the back door locked?");
    const thinking = result.current.items.find((item) => item.kind === "thinking")!;
    expect(thinking.kind === "thinking" && thinking.detail).toBe("working");
  });

  it("merges history, tombstones and live rows by timestamp", () => {
    // Older than the history row, and passed after it: only a real sort puts
    // it first.
    const tombstone: TimelineItem = {
      kind: "tombstone",
      id: "tomb:1",
      at: "2026-09-01T09:00:00",
      title: "Lock unlock",
      meta: "expired · not done",
    };
    const { result } = renderRoom({ history: [historyRow], tombstones: [tombstone], online: true });

    act(() => result.current.sendText("Is the back door locked?"));

    expect(kinds(result.current.items)).toEqual(["tombstone", "alfred", "you", "thinking"]);
  });

  it("relabels the day when the app returns after midnight", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-01T23:59:00"));
    const { result } = renderRoom({ history: [historyRow], online: true });
    expect(result.current.items[0]).toMatchObject({ kind: "divider", label: "earlier today" });

    vi.setSystemTime(new Date("2026-09-02T00:01:00"));
    act(() => void document.dispatchEvent(new Event("visibilitychange")));

    expect(result.current.items[0]).toMatchObject({ kind: "divider", label: "yesterday" });
  });

  it("puts a day divider in front of the thread", () => {
    const { result } = renderRoom({ history: [historyRow], online: true });
    expect(result.current.items[0].kind).toBe("divider");
  });

  it("ignores an empty or whitespace-only draft", () => {
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() => result.current.sendText("   "));

    expect(chat.sendText).not.toHaveBeenCalled();
    expect(result.current.items).toHaveLength(0);
  });
});

describe("useRoom — sending", () => {
  it("marks a message unsent and remembers it when the house cannot hear", () => {
    sendSucceeds = false;
    const { result } = renderRoom({ history: [], online: false });

    act(() => result.current.sendText("Turn the hall light off"));

    const you = result.current.items.find((item) => item.kind === "you")!;
    expect(you.kind === "you" && you.state).toBe("unsent");
    // No thinking row: nothing is in flight.
    expect(kinds(result.current.items)).toEqual(["you"]);
    expect(JSON.parse(localStorage.getItem(UNSENT_KEY) ?? "[]")).toHaveLength(1);
  });

  it("does not touch the socket while the house is unreachable", () => {
    // The socket would accept it; the `online` flag alone must stop the send.
    const { result, chat } = renderRoom({ history: [], online: false });

    act(() => result.current.sendText("Turn the hall light off"));

    expect(chat.sendText).not.toHaveBeenCalled();
    const you = result.current.items.find((item) => item.kind === "you")!;
    expect(you.kind === "you" && you.state).toBe("unsent");
  });

  it("keeps the turn in flight when a later message cannot leave", () => {
    const { result, chat } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("Anything tomorrow?"));

    act(() => chat.onstatus("offline"));
    sendSucceeds = false;
    act(() => result.current.sendText("And the windows?"));

    expect(kinds(result.current.items)).toEqual(["you", "thinking", "you"]);
    expect(result.current.thinking).toBe(true);
  });

  it("shows one thinking row when the queue goes out behind a turn in flight", () => {
    const { result, chat } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("Anything tomorrow?"));

    act(() => chat.onstatus("offline"));
    sendSucceeds = false;
    act(() => result.current.sendText("And the windows?"));

    sendSucceeds = true;
    act(() => chat.onstatus("online"));

    expect(result.current.items.filter((item) => item.kind === "thinking")).toHaveLength(1);
  });

  it("retries the queue in order when the connection returns", () => {
    sendSucceeds = false;
    const { result, chat } = renderRoom({ history: [], online: false });

    act(() => result.current.sendText("first"));
    act(() => result.current.sendText("second"));
    chat.sendText.mockClear();

    sendSucceeds = true;
    act(() => chat.onstatus("online"));

    expect(chat.sendText.mock.calls.map((call) => call[0])).toEqual(["first", "second"]);
    expect(
      result.current.items.every((item) => item.kind !== "you" || item.state === "sent"),
    ).toBe(true);
    expect(JSON.parse(localStorage.getItem(UNSENT_KEY) ?? "[]")).toHaveLength(0);
    // What went out is a turn in flight.
    expect(kinds(result.current.items)).toEqual(["you", "you", "thinking"]);
    expect(result.current.thinking).toBe(true);
    const thinking = result.current.items.find((item) => item.kind === "thinking")!;
    expect(thinking.kind === "thinking" && thinking.detail).toBe("working");
  });

  it("gives up on a retried turn the server never answers", () => {
    vi.useFakeTimers();
    sendSucceeds = false;
    const { result, chat } = renderRoom({ history: [], online: false });
    act(() => result.current.sendText("first"));

    sendSucceeds = true;
    act(() => chat.onstatus("online"));
    act(() => void vi.advanceTimersByTime(60_000));

    expect(result.current.thinking).toBe(false);
    const alfred = result.current.items.find((item) => item.kind === "alfred")!;
    expect(alfred.kind === "alfred" && alfred.text).toBe("No reply in 60 s.");
  });

  it("stops the retry at the first refusal rather than reordering", () => {
    sendSucceeds = false;
    const { result, chat } = renderRoom({ history: [], online: false });

    act(() => result.current.sendText("first"));
    act(() => result.current.sendText("second"));
    act(() => result.current.sendText("third"));
    chat.sendText.mockClear();
    // The third would go if it were tried; the refusal of the second must stop it.
    sendSucceeds = true;
    chat.sendText.mockImplementationOnce(() => true).mockImplementationOnce(() => false);

    act(() => chat.onstatus("online"));

    expect(chat.sendText.mock.calls.map((call) => call[0])).toEqual(["first", "second"]);
    const states = result.current.items
      .filter((item) => item.kind === "you")
      .map((item) => (item.kind === "you" ? item.state : ""));
    expect(states).toEqual(["sent", "unsent", "unsent"]);
  });

  it("restores the queue after a cold launch", () => {
    localStorage.setItem(
      UNSENT_KEY,
      JSON.stringify([
        { kind: "you", id: "you:cold", at: "2026-09-07T21:00:00", text: "held over", state: "unsent" },
      ]),
    );

    const { result } = renderRoom({ history: [], online: false });

    expect(result.current.items.some((item) => item.kind === "you" && item.text === "held over")).toBe(
      true,
    );
  });

  it("ignores a corrupt queue rather than refusing to start", () => {
    localStorage.setItem(UNSENT_KEY, "{not json");
    const { result } = renderRoom({ history: [], online: false });
    expect(result.current.items).toHaveLength(0);
  });

  // Valid JSON, wrong shape — one field short each. Without its timestamp a
  // row would open the thread with a `NaN undefined` day divider; without its
  // state it would never be retried and never be cleared.
  it.each([
    ["kind", { id: "you:cold", at: "2026-09-07T21:00:00", text: "held over", state: "unsent" }],
    ["id", { kind: "you", at: "2026-09-07T21:00:00", text: "held over", state: "unsent" }],
    ["timestamp", { kind: "you", id: "you:cold", text: "held over", state: "unsent" }],
    ["text", { kind: "you", id: "you:cold", at: "2026-09-07T21:00:00", state: "unsent" }],
    ["unsent state", { kind: "you", id: "you:cold", at: "2026-09-07T21:00:00", text: "held over" }],
  ])("ignores a persisted row without its %s", (_field, row) => {
    localStorage.setItem(UNSENT_KEY, JSON.stringify([row]));
    const { result } = renderRoom({ history: [], online: false });
    expect(result.current.items).toHaveLength(0);
  });

  it("drops only the unreadable row, not the whole queue", () => {
    // A `null` element must be refused by the shape check, not thrown on and
    // caught — the catch loses everything that was queued behind it.
    localStorage.setItem(
      UNSENT_KEY,
      JSON.stringify([
        null,
        { kind: "you", id: "you:cold", at: "2026-09-07T21:00:00", text: "held over", state: "unsent" },
      ]),
    );
    const { result } = renderRoom({ history: [], online: false });
    expect(result.current.items.some((item) => item.kind === "you" && item.text === "held over")).toBe(
      true,
    );
  });
});

describe("useRoom — what comes back", () => {
  it("replaces the thinking row with Alfred's reply and speaks it", () => {
    const { result, chat } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("Anything tomorrow?"));

    act(() =>
      chat.deliver({
        type: "response",
        text: "The dentist at nine, sir.",
        session_id: "s_9f2",
        actions_taken: ["calendar.today"],
        mood: "pleased",
        audio: "UklGRg==",
      }),
    );

    expect(kinds(result.current.items)).toEqual(["you", "alfred"]);
    const alfred = result.current.items.find((item) => item.kind === "alfred")!;
    expect(alfred.kind === "alfred" && alfred.mood).toBe("pleased");
    expect(playWavBase64Mock).toHaveBeenCalledWith("UklGRg==");
    expect(result.current.thinking).toBe(false);
  });

  it("renders an error frame as an error row", () => {
    const { result, chat } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("Anything tomorrow?"));

    act(() => chat.deliver({ type: "error", text: "Expected a JSON object" }));

    const alfred = result.current.items.find((item) => item.kind === "alfred")!;
    expect(alfred.kind === "alfred" && alfred.error).toBe(true);
    expect(alfred.kind === "alfred" && alfred.text).toBe("Expected a JSON object");
  });

  it("replaces the dashed bubble with what the server heard", () => {
    const { result, chat } = renderRoom({ history: [], online: true });
    act(() => result.current.sendAudio("data:audio/mp4;base64,AAAA", 2.4));
    expect(kinds(result.current.items)).toEqual(["transcribing"]);
    // The bubble shows how much audio is with the server; it must be what was sent.
    const bubble = result.current.items.find((item) => item.kind === "transcribing")!;
    expect(bubble.kind === "transcribing" && bubble.seconds).toBe(2.4);

    act(() =>
      chat.deliver({ type: "transcription", text: "Is the back door locked?", session_id: "s_9f2" }),
    );

    expect(kinds(result.current.items)).toEqual(["you", "thinking"]);
    const you = result.current.items.find((item) => item.kind === "you")!;
    expect(you.kind === "you" && you.text).toBe("Is the back door locked?");
    const thinking = result.current.items.find((item) => item.kind === "thinking")!;
    expect(thinking.kind === "thinking" && thinking.detail).toBe("working");
  });

  it("clears the dashed bubble when the turn errors", () => {
    const { result, chat } = renderRoom({ history: [], online: true });
    act(() => result.current.sendAudio("data:audio/mp4;base64,AAAA", 1.2));

    act(() => chat.deliver({ type: "error", text: "Expected a JSON object" }));

    expect(kinds(result.current.items)).toEqual(["alfred"]);
  });

  it("clears the dashed bubble when transcription failed outright", () => {
    const { result, chat } = renderRoom({ history: [], online: true });
    act(() => result.current.sendAudio("data:audio/mp4;base64,AAAA", 1.2));

    // The STT failure path answers with a `response`, never a `transcription`.
    act(() =>
      chat.deliver({
        type: "response",
        text: "I'm afraid I couldn't make out what was said.",
        session_id: "s_9f2",
      }),
    );

    expect(kinds(result.current.items)).toEqual(["alfred"]);
  });

  it("sends nothing and shows nothing when the socket refuses the audio", () => {
    sendSucceeds = false;
    const { result } = renderRoom({ history: [], online: false });

    act(() => result.current.sendAudio("data:audio/mp4;base64,AAAA", 2.4));

    expect(result.current.items).toHaveLength(0);
  });

  it("turns a notification into a quiet act row", () => {
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Bins go out tonight",
        body: "Collection moved to Friday.",
        urgency: "important",
        notification_id: "ntf-9",
        metadata: {},
      }),
    );

    const act_ = result.current.items.find((item) => item.kind === "act")!;
    expect(act_.kind === "act" && act_.hue).toBe(255);
    expect(act_.kind === "act" && act_.text).toBe("Collection moved to Friday.");
    expect(act_.kind === "act" && act_.meta).toMatch(/^\d{2}:\d{2} · live · important$/);
  });

  it("reads a bodiless notification by its title", () => {
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Routine Suggestion",
        body: "",
        urgency: "informational",
        notification_id: "ntf-10",
        metadata: {},
      }),
    );

    const act_ = result.current.items.find((item) => item.kind === "act")!;
    expect(act_.kind === "act" && act_.text).toBe("Routine Suggestion");
  });

  it("leaves a confirmation request to the Door", () => {
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Confirmation required",
        body: "Alfred wants to run 'home.lock_unlock' on home-service — confirm?",
        urgency: "urgent",
        notification_id: "ntf-8",
        metadata: { pending_action_id: "a91f3c2e" },
      }),
    );

    expect(result.current.items).toHaveLength(0);
  });

  it("speaks an urgent notification and stays quiet for the rest", () => {
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() =>
      chat.deliver({
        type: "notification",
        title: "Water where it should not be",
        body: "The utility-room sensor is wet.",
        urgency: "urgent",
        notification_id: "ntf-7",
        audio: "UklGRg==",
        metadata: {},
      }),
    );
    expect(playWavBase64Mock).toHaveBeenCalledWith("UklGRg==");

    playWavBase64Mock.mockClear();
    act(() =>
      chat.deliver({
        type: "notification",
        title: "Bins go out tonight",
        body: "Collection moved to Friday.",
        urgency: "important",
        notification_id: "ntf-6",
        audio: "UklGRg==",
        metadata: {},
      }),
    );
    expect(playWavBase64Mock).not.toHaveBeenCalled();
    expect(result.current.items.filter((item) => item.kind === "act")).toHaveLength(2);
  });
});

describe("useRoom — silence", () => {
  it("gives up after sixty seconds and says so", () => {
    vi.useFakeTimers();
    const { result } = renderRoom({ history: [], online: true });

    act(() => result.current.sendText("Anything tomorrow?"));
    expect(result.current.thinking).toBe(true);

    act(() => void vi.advanceTimersByTime(59_999));
    expect(result.current.thinking).toBe(true);

    act(() => void vi.advanceTimersByTime(1));

    expect(result.current.thinking).toBe(false);
    const alfred = result.current.items.find((item) => item.kind === "alfred")!;
    expect(alfred.kind === "alfred" && alfred.text).toBe("No reply in 60 s.");
    expect(alfred.kind === "alfred" && alfred.error).toBe(true);
  });

  it("does not restart the countdown when an unrelated frame arrives", () => {
    vi.useFakeTimers();
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() => result.current.sendText("Anything tomorrow?"));
    act(() => void vi.advanceTimersByTime(59_000));
    act(() =>
      chat.deliver({
        type: "notification",
        title: "Bins go out tonight",
        body: "Collection moved to Friday.",
        urgency: "important",
        notification_id: "ntf-5",
        metadata: {},
      }),
    );
    act(() => void vi.advanceTimersByTime(2_000));

    expect(result.current.thinking).toBe(false);
  });

  it("cancels the timeout when the reply arrives in time", () => {
    vi.useFakeTimers();
    const { result, chat } = renderRoom({ history: [], online: true });

    act(() => result.current.sendText("Anything tomorrow?"));
    act(() =>
      chat.deliver({ type: "response", text: "Nothing, sir.", session_id: "s_9f2" }),
    );
    act(() => void vi.advanceTimersByTime(120_000));

    expect(
      result.current.items.filter((item) => item.kind === "alfred"),
    ).toHaveLength(1);
  });
});

describe("useRoom — what the house reads back", () => {
  // Stamped by the server, as history rows are; the live rows carry the
  // phone's clock, and nothing below compares the two.
  const readBack = (kind: "you" | "alfred", id: string, text: string): TimelineItem =>
    kind === "you"
      ? { kind, id: `you:${id}`, at: "2026-09-07T21:00:00", text, state: "sent" }
      : { kind, id: `alfred:${id}`, at: "2026-09-07T21:00:04", text, actions: [] };

  it("shows a turn once when the history has read it back", () => {
    const { result, chat, rerender } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("Anything tomorrow?"));
    act(() =>
      chat.deliver({ type: "response", text: "The dentist at nine, sir.", session_id: "s_9f2" }),
    );
    expect(kinds(result.current.items)).toEqual(["you", "alfred"]);

    // The app went to the background and came back: the streams now carry both.
    rerender({
      history: [
        readBack("you", "1788814800000-0", "Anything tomorrow?"),
        readBack("alfred", "1788814804000-0", "The dentist at nine, sir."),
      ],
    });

    expect(kinds(result.current.items)).toEqual(["you", "alfred"]);
    expect(result.current.items.map((item) => item.id)).toEqual([
      expect.stringMatching(/^divider:/),
      "you:1788814800000-0",
      "alfred:1788814804000-0",
    ]);
  });

  it("keeps a live turn the history has not caught up with", () => {
    const { result, chat, rerender } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("Anything tomorrow?"));
    act(() =>
      chat.deliver({ type: "response", text: "The dentist at nine, sir.", session_id: "s_9f2" }),
    );

    rerender({ history: [readBack("you", "1788814800000-0", "Anything tomorrow?")] });

    expect(kinds(result.current.items)).toEqual(["you", "alfred"]);
    const alfred = result.current.items.find((item) => item.kind === "alfred")!;
    expect(alfred.id).toMatch(/^alfred:\d+$/);
  });

  it("matches by what was said, not by kind alone", () => {
    const { result, rerender } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("Lock the back door"));

    // A satellite's request, read back in the same window: not ours.
    rerender({ history: [readBack("you", "1788814800000-0", "Lights off in the study")] });

    expect(kinds(result.current.items)).toEqual(["you", "you", "thinking"]);
  });

  it("does not take the same words from the other side of the thread", () => {
    const { result, rerender } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("Good night"));

    rerender({ history: [readBack("alfred", "1788814804000-0", "Good night")] });

    // History is dated before the live rows (see `readBack`), so it leads.
    expect(kinds(result.current.items)).toEqual(["alfred", "you", "thinking"]);
  });

  it("does not let an older turn swallow a new one that says the same", () => {
    // "yes" was already in the thread when the Room opened.
    const { result, rerender } = renderRoom({
      history: [readBack("you", "1788814000000-0", "yes")],
      online: true,
    });

    act(() => result.current.sendText("yes"));
    expect(kinds(result.current.items)).toEqual(["you", "you", "thinking"]);

    // Read back: the old one and the new one.
    rerender({
      history: [
        readBack("you", "1788814000000-0", "yes"),
        readBack("you", "1788814800000-0", "yes"),
      ],
    });
    expect(kinds(result.current.items)).toEqual(["you", "you", "thinking"]);
  });

  it("waits for the first history before deciding what predates the session", () => {
    // Undefined, not empty: the read has not answered yet.
    const { result, rerender } = renderRoom({ online: true });
    rerender({ history: [readBack("you", "1788814000000-0", "yes")] });

    act(() => result.current.sendText("yes"));

    expect(kinds(result.current.items)).toEqual(["you", "you", "thinking"]);
  });

  it("answers one live row with one history row", () => {
    const { result, rerender } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("yes"));
    act(() => result.current.sendText("yes"));

    rerender({ history: [readBack("you", "1788814800000-0", "yes")] });

    expect(kinds(result.current.items)).toEqual(["you", "you", "thinking"]);
  });

  it("reads a turn back once, even after its copy has left the window", () => {
    const { result, rerender } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("yes"));
    rerender({ history: [readBack("you", "1788814800000-0", "yes")] });

    // The same words again — and by the time they come back, the window has
    // rolled past the first copy.
    act(() => result.current.sendText("yes"));
    rerender({ history: [readBack("you", "1788814900000-0", "yes")] });

    expect(kinds(result.current.items)).toEqual(["you", "thinking"]);
  });

  it("does not let a copy that has answered once answer again", () => {
    const { result, rerender } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("yes"));
    const first = [readBack("you", "1788814800000-0", "yes")];
    rerender({ history: first });

    // The same words again, and a re-read before the house has written them.
    act(() => result.current.sendText("yes"));
    rerender({ history: [...first] });

    expect(kinds(result.current.items)).toEqual(["you", "you", "thinking"]);
  });

  it("leaves the unsent queue alone", () => {
    const { result, rerender } = renderRoom({ history: [], online: false });
    act(() => result.current.sendText("yes"));

    // Someone else's "yes" was read back; ours never left.
    rerender({ history: [readBack("you", "1788814800000-0", "yes")] });

    const rows = result.current.items.filter((item) => item.kind !== "divider");
    expect(rows.map((item) => item.kind === "you" && item.state)).toEqual(["sent", "unsent"]);
  });

  it("lets the history's copy of a notification replace the live one", () => {
    const { result, chat, rerender } = renderRoom({ history: [], online: true });
    act(() =>
      chat.deliver({
        type: "notification",
        title: "Bins go out tonight",
        body: "Collection moved to Friday.",
        urgency: "important",
        notification_id: "ntf-9",
        metadata: {},
      }),
    );
    expect(kinds(result.current.items)).toEqual(["act"]);

    // The stream copy knows its source; the frame only knew it was live.
    rerender({
      history: [
        {
          kind: "act",
          id: "nt:1788814800000-0",
          at: "2026-09-07T21:00:00",
          hue: 255,
          text: "Collection moved to Friday.",
          meta: "21:00 · domain-router · important",
        },
      ],
    });
    const acts = result.current.items.filter((item) => item.kind === "act");
    expect(acts).toHaveLength(1);
    expect(acts[0]!.kind === "act" && acts[0]!.meta).toBe("21:00 · domain-router · important");
  });

  it("does not let a reflex answer for a notification that says the same", () => {
    const { result, chat, rerender } = renderRoom({ history: [], online: true });
    act(() =>
      chat.deliver({
        type: "notification",
        title: "Bins go out tonight",
        body: "Collection moved to Friday.",
        urgency: "important",
        notification_id: "ntf-9",
        metadata: {},
      }),
    );

    rerender({
      history: [
        {
          kind: "act",
          id: "rx:1788814800000-0",
          at: "2026-09-07T21:00:00",
          hue: 210,
          text: "Collection moved to Friday.",
          meta: "21:00 · reflex · calendar.remind",
        },
      ],
    });
    expect(result.current.items.filter((item) => item.kind === "act")).toHaveLength(2);
  });

  it("leaves a client-made error row alone", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-07T21:00:00"));
    const { result, rerender } = renderRoom({ history: [], online: true });
    act(() => result.current.sendText("yes"));
    act(() => vi.advanceTimersByTime(NO_REPLY_MS));

    rerender({
      history: [
        readBack("you", "1788814800000-0", "yes"),
        readBack("alfred", "1788814860000-0", "No reply in 60 s."),
      ],
    });

    expect(kinds(result.current.items)).toEqual(["you", "alfred", "alfred"]);
  });
});
