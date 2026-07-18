import { describe, expect, it, vi } from "vitest";

const { sent } = vi.hoisted(() => ({ sent: [] as Record<string, unknown>[] }));

vi.mock("./ws", () => {
  class ReconnectingSocket {
    onstatus: (s: unknown) => void = () => {};
    onopen: () => void = () => {};
    onmessage: (data: unknown) => void = () => {};
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

describe("ChatSocket payloads", () => {
  it("include the client IANA timezone", () => {
    const socket = new ChatSocket();
    socket.sendText("hello");
    const body = sent.at(-1)!;
    expect(body.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
    expect(body.channel).toBe("web_pwa");
  });
});
