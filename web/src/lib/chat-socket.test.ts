import { beforeEach, describe, expect, it, vi } from "vitest";

const { sent, sockets } = vi.hoisted(() => ({
  sent: [] as Record<string, unknown>[],
  sockets: [] as {
    onmessage: (data: unknown) => void;
    onstatus: (s: string) => void;
    onopen: () => void;
  }[],
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

  it("carry a stored, still-live session id on the first message only", () => {
    localStorage.setItem("alfred.session", "s_9f2");
    localStorage.setItem("alfred.session-at", new Date(Date.now() - 60_000).toISOString());
    const socket = new ChatSocket();

    socket.sendText("first");
    socket.sendText("second");

    expect(sent[0].session_id).toBe("s_9f2");
    expect(sent[1].session_id).toBeUndefined();
  });

  it("drop a session id that has been idle for the timeout and take the server's", () => {
    localStorage.setItem("alfred.session", "s_old");
    localStorage.setItem("alfred.session-at", new Date(Date.now() - 31 * 60_000).toISOString());
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_new" });

    socket.sendText("hello again");

    expect(sent[0].session_id).toBeUndefined();
    expect(socket.sessionId).toBe("s_new");
    expect(localStorage.getItem("alfred.session")).toBe("s_new");
  });

  it("treat a session id with no last-sent stamp as idle", () => {
    localStorage.setItem("alfred.session", "s_before_the_stamp");
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_new" });

    socket.sendText("hello");

    expect(sent[0].session_id).toBeUndefined();
    expect(socket.sessionId).toBe("s_new");
  });

  it("treat an unreadable last-sent stamp as idle", () => {
    localStorage.setItem("alfred.session", "s_old");
    localStorage.setItem("alfred.session-at", "the other day");
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_new" });

    socket.sendText("hello");

    expect(sent[0].session_id).toBeUndefined();
    expect(socket.sessionId).toBe("s_new");
  });

  it("follow the server's idle timeout", () => {
    localStorage.setItem("alfred.session", "s_9f2");
    localStorage.setItem("alfred.session-at", new Date(Date.now() - 15 * 60_000).toISOString());
    const socket = new ChatSocket();
    socket.setIdleMs(10 * 60_000);

    socket.sendText("hello");

    expect(sent[0].session_id).toBeUndefined();
  });

  it("stamp every send as the session's last activity", () => {
    const socket = new ChatSocket();
    socket.sendText("hello");
    const stamp = localStorage.getItem("alfred.session-at")!;
    expect(Math.abs(Date.now() - Date.parse(stamp))).toBeLessThan(5_000);
  });

  it("forget a stale id even before the server has spoken, then adopt what it says", () => {
    localStorage.setItem("alfred.session", "s_old");
    const socket = new ChatSocket();

    socket.sendText("hello");
    expect(sent[0].session_id).toBeUndefined();
    expect(localStorage.getItem("alfred.session")).toBeNull();

    sockets[0].onmessage({ type: "session", session_id: "s_new" });
    expect(socket.sessionId).toBe("s_new");
    expect(localStorage.getItem("alfred.session")).toBe("s_new");
  });

  it("offer the stored id again on the next connection, before the server names one", () => {
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_a" });
    socket.sendText("first");

    sockets[0].onopen();
    socket.sendText("after a reconnect");

    expect(sent[1].session_id).toBe("s_a");
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
    expect(localStorage.getItem("alfred.session")).toBe("s_new");
  });

  it("keeps a live stored id over the server's, but remembers the server's", () => {
    localStorage.setItem("alfred.session", "s_9f2");
    localStorage.setItem("alfred.session-at", new Date().toISOString());
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_new" });
    expect(socket.sessionId).toBe("s_9f2");

    socket.sendText("hello");
    expect(sent[0].session_id).toBe("s_9f2");
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
