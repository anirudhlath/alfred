import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TimelineItem } from "@/lib/history";
import type { ChatServerMessage } from "@/lib/types";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { UNSENT_KEY, useRoom, type UseRoomOptions } from "./useRoom";

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
  });

  it("stops the retry at the first refusal rather than reordering", () => {
    sendSucceeds = false;
    const { result, chat } = renderRoom({ history: [], online: false });

    act(() => result.current.sendText("first"));
    act(() => result.current.sendText("second"));
    chat.sendText.mockClear();
    chat.sendText.mockImplementationOnce(() => true).mockImplementationOnce(() => false);

    act(() => chat.onstatus("online"));

    const states = result.current.items
      .filter((item) => item.kind === "you")
      .map((item) => (item.kind === "you" ? item.state : ""));
    expect(states).toEqual(["sent", "unsent"]);
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

    act(() =>
      chat.deliver({ type: "transcription", text: "Is the back door locked?", session_id: "s_9f2" }),
    );

    expect(kinds(result.current.items)).toEqual(["you", "thinking"]);
    const you = result.current.items.find((item) => item.kind === "you")!;
    expect(you.kind === "you" && you.text).toBe("Is the back door locked?");
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
    expect(act_.kind === "act" && act_.text).toBe("Bins go out tonight");
    expect(act_.kind === "act" && act_.meta).toMatch(/^\d{2}:\d{2} · live · important$/);
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
