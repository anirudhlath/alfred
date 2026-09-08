import { beforeEach, describe, expect, it, vi } from "vitest";

const { sent, sockets } = vi.hoisted(() => ({
  sent: [] as Record<string, unknown>[],
  sockets: [] as { onmessage: (data: unknown) => void; onstatus: (s: string) => void }[],
}));

vi.mock("./ws", () => {
  class ReconnectingSocket {
    onstatus: (s: string) => void = () => {};
    onopen: () => void = () => {};
    onmessage: (data: unknown) => void = () => {};
    lastMessageAt: number | null = null;
    constructor() {
      sockets.push(this);
    }
    connect(): void {}
    close(): void {}
    send(payload: Record<string, unknown>): boolean {
      sent.push(payload);
      return true;
    }
  }
  return { ReconnectingSocket };
});

import { ChatSocket } from "./chat-socket";
import type { ChatServerMessage } from "./types";

beforeEach(() => {
  sent.length = 0;
  sockets.length = 0;
  localStorage.clear();
});

describe("ChatSocket payloads", () => {
  it("include the client IANA timezone and the pwa channel", () => {
    const socket = new ChatSocket();
    socket.sendText("hello");
    const body = sent.at(-1)!;
    expect(body.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
    expect(body.channel).toBe("web_pwa");
    expect(body.type).toBe("text");
    expect(body.content).toBe("hello");
  });

  it("carry a stored session id on the first message only", () => {
    localStorage.setItem("alfred_session_id", "s_9f2");
    const socket = new ChatSocket();

    socket.sendText("first");
    socket.sendText("second");

    expect(sent[0].session_id).toBe("s_9f2");
    expect(sent[1].session_id).toBeUndefined();
  });

  it("send audio as a data URL", () => {
    const socket = new ChatSocket();
    socket.sendAudio("data:audio/mp4;base64,AAAA");
    expect(sent.at(-1)).toMatchObject({ type: "audio", content: "data:audio/mp4;base64,AAAA" });
  });
});

describe("ChatSocket frames", () => {
  it("adopts the server's session id when it has none", () => {
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_new" });
    expect(socket.sessionId).toBe("s_new");
    expect(localStorage.getItem("alfred_session_id")).toBe("s_new");
  });

  it("keeps a pong to itself", () => {
    const socket = new ChatSocket();
    const seen: ChatServerMessage[] = [];
    socket.listen((msg) => seen.push(msg));

    sockets[0].onmessage({ type: "pong" });
    sockets[0].onmessage({ type: "error", text: "nope" });

    expect(seen).toEqual([{ type: "error", text: "nope" }]);
  });

  it("forwards socket status", () => {
    const socket = new ChatSocket();
    const seen: string[] = [];
    socket.onstatus = (s) => seen.push(s);
    sockets[0].onstatus("unauthorized");
    expect(seen).toEqual(["unauthorized"]);
  });
});
