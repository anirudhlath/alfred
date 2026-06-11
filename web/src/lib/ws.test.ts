import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReconnectingSocket } from "./ws";
import { TelemetrySocket } from "./telemetry-socket";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  url: string;
  constructor(url: string) { this.url = url; FakeWebSocket.instances.push(this); }
  send(data: string) { this.sent.push(data); }
  close() { this.readyState = 3; this.onclose?.({ code: 1000 }); }
  open() { this.readyState = 1; this.onopen?.(); }
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.useFakeTimers();
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("ReconnectingSocket", () => {
  it("reconnects with backoff after close", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    FakeWebSocket.instances[0].open();
    FakeWebSocket.instances[0].onclose?.({ code: 1006 });
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(600);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("does not resurrect after close() during a backoff wait", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    FakeWebSocket.instances[0].open();
    FakeWebSocket.instances[0].onclose?.({ code: 1006 });
    sock.close();
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("does not reconnect after 4001 and reports unauthorized", () => {
    const statuses: string[] = [];
    const sock = new ReconnectingSocket("/ws/test");
    sock.onstatus = (s) => statuses.push(s);
    sock.connect();
    FakeWebSocket.instances[0].onclose?.({ code: 4001 });
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(statuses.at(-1)).toBe("unauthorized");
  });
});

describe("TelemetrySocket", () => {
  it("replays subscriptions on reconnect", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events", "actions"]);
    FakeWebSocket.instances[0].onclose?.({ code: 1006 });
    vi.advanceTimersByTime(600);
    FakeWebSocket.instances[1].open();
    const replayed = FakeWebSocket.instances[1].sent.map((s) => JSON.parse(s));
    expect(replayed).toContainEqual({ type: "subscribe", streams: ["events", "actions"] });
  });
});
