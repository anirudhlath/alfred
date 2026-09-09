import { beforeEach, describe, expect, it, vi } from "vitest";

const { sent, sockets, delivers } = vi.hoisted(() => ({
  sent: [] as Record<string, unknown>[],
  sockets: [] as {
    onmessage: (data: unknown) => void;
    onstatus: (s: string) => void;
    onopen: () => void;
  }[],
  /** Whether the fake socket is open enough to take a frame. See the failed-send test. */
  delivers: { value: true },
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
      if (!delivers.value) return false;
      sent.push(payload);
      return true;
    }
  }
  return { ReconnectingSocket };
});

import { ChatSocket } from "./chat-socket";
import type { ChatServerMessage } from "./types";

/** A stored session last used `ageMs` ago; omit the age for one that predates the stamp. */
function storedSession(id: string, ageMs?: number): void {
  localStorage.setItem("alfred.session", id);
  if (ageMs !== undefined) {
    localStorage.setItem("alfred.session-at", new Date(Date.now() - ageMs).toISOString());
  }
}

beforeEach(() => {
  sent.length = 0;
  sockets.length = 0;
  delivers.value = true;
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

  it("send audio as a data URL", () => {
    const socket = new ChatSocket();
    socket.sendAudio("data:audio/mp4;base64,AAAA");
    expect(sent.at(-1)).toMatchObject({ type: "audio", content: "data:audio/mp4;base64,AAAA" });
  });
});

describe("ChatSocket frames", () => {
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

describe("ChatSocket sessions", () => {
  it("adopts the server's session id when it has none", () => {
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_new" });
    expect(socket.sessionId).toBe("s_new");
    expect(localStorage.getItem("alfred.session")).toBe("s_new");
  });

  it("carry a stored, still-live session id on the first message only", () => {
    storedSession("s_9f2", 60_000);
    const socket = new ChatSocket();

    socket.sendText("first");
    socket.sendText("second");

    expect(sent[0].session_id).toBe("s_9f2");
    expect(sent[1].session_id).toBeUndefined();
  });

  it("keeps a live stored id over the server's, but remembers the server's", () => {
    storedSession("s_9f2", 0);
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_new" });
    expect(socket.sessionId).toBe("s_9f2");

    socket.sendText("hello");
    expect(sent[0].session_id).toBe("s_9f2");
  });

  it("drop a session id that has been idle for the timeout and take the server's", () => {
    storedSession("s_old", 31 * 60_000);
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_new" });

    socket.sendText("hello again");

    expect(sent[0].session_id).toBeUndefined();
    expect(socket.sessionId).toBe("s_new");
    expect(localStorage.getItem("alfred.session")).toBe("s_new");
  });

  it("treat a session id with no last-sent stamp as idle", () => {
    storedSession("s_before_the_stamp");
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_new" });

    socket.sendText("hello");

    expect(sent[0].session_id).toBeUndefined();
    expect(socket.sessionId).toBe("s_new");
  });

  it("treat an unreadable last-sent stamp as idle", () => {
    storedSession("s_old");
    localStorage.setItem("alfred.session-at", "the other day");
    const socket = new ChatSocket();
    sockets[0].onmessage({ type: "session", session_id: "s_new" });

    socket.sendText("hello");

    expect(sent[0].session_id).toBeUndefined();
    expect(socket.sessionId).toBe("s_new");
  });

  it("follow the server's idle timeout", () => {
    storedSession("s_9f2", 15 * 60_000);
    const socket = new ChatSocket();
    socket.setIdleMs(10 * 60_000);

    socket.sendText("hello");

    expect(sent[0].session_id).toBeUndefined();
  });

  it("stamp every send as the session's last activity", () => {
    const socket = new ChatSocket();
    const before = Date.now();
    socket.sendText("hello");
    expect(Date.parse(localStorage.getItem("alfred.session-at")!)).toBeGreaterThanOrEqual(before);
  });

  it("do not spend the first message on a send that never left", () => {
    storedSession("s_9f2", 60_000);
    const stamp = localStorage.getItem("alfred.session-at");
    const socket = new ChatSocket();

    delivers.value = false;
    expect(socket.sendText("into the void")).toBe(false);
    expect(localStorage.getItem("alfred.session-at")).toBe(stamp);

    delivers.value = true;
    socket.sendText("the real first message");
    expect(sent[0].session_id).toBe("s_9f2");
  });

  it("forget a stale id even before the server has spoken, then adopt what it says", () => {
    storedSession("s_old");
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
});
