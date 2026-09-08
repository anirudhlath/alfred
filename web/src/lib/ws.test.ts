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
  // Faithful close event: a real socket is already CLOSED (readyState 3) by the time
  // onclose fires — never OPEN. Tests must go through this, not call onclose directly.
  emitClose(code: number) { this.readyState = 3; this.onclose?.({ code }); }
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
    FakeWebSocket.instances[0].emitClose(1006);
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(600);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("does not resurrect after close() during a backoff wait", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    FakeWebSocket.instances[0].open();
    FakeWebSocket.instances[0].emitClose(1006);
    sock.close();
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("does not reconnect after 4001 and reports unauthorized", () => {
    const statuses: string[] = [];
    const sock = new ReconnectingSocket("/ws/test");
    sock.onstatus = (s) => statuses.push(s);
    sock.connect();
    FakeWebSocket.instances[0].emitClose(4001);
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(statuses.at(-1)).toBe("unauthorized");
  });

  it("a superseded socket's late close does not spawn a duplicate connection", () => {
    // Reproduces the StrictMode setup→cleanup→setup leak: after close()+connect(),
    // the first socket's delayed onclose must not schedule its own reconnect.
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    const wsA = FakeWebSocket.instances[0];
    wsA.open();
    sock.close();      // stops wsA
    sock.connect();    // wsA is CLOSED → guard allows a fresh wsB
    expect(FakeWebSocket.instances).toHaveLength(2);
    wsA.emitClose(1006);          // stale close from the superseded socket
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(2);  // no orphaned wsC
  });

  it("pings every 30 s while open and stops once closed", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    vi.advanceTimersByTime(30_000);
    expect(ws.sent.map((s) => JSON.parse(s))).toEqual([{ type: "ping" }]);

    vi.advanceTimersByTime(30_000);
    expect(ws.sent).toHaveLength(2);

    sock.close();
    // The interval is gone, not merely quiet: a closed fake socket refuses
    // sends, so `sent` staying flat would not prove the timer was cleared.
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(120_000);
    expect(ws.sent).toHaveLength(2);
  });

  it("does not ping before the socket is open", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    vi.advanceTimersByTime(120_000);
    expect(FakeWebSocket.instances[0].sent).toHaveLength(0);
  });

  it("takes a custom interval", () => {
    const sock = new ReconnectingSocket("/ws/test", { pingIntervalMs: 1000 });
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    vi.advanceTimersByTime(3000);

    expect(ws.sent).toHaveLength(3);
    sock.close();
  });

  it("stops pinging a socket the server dropped", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.emitClose(1006);

    vi.advanceTimersByTime(30_000);

    expect(ws.sent).toHaveLength(0);
    // The 500 ms reconnect has fired by now; the only timer left would be a
    // leaked ping interval.
    expect(vi.getTimerCount()).toBe(0);
    sock.close();
  });

  it("stamps when the last frame arrived", () => {
    const sock = new ReconnectingSocket("/ws/test");
    expect(sock.lastMessageAt).toBeNull();
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    expect(sock.lastMessageAt).toBeNull(); // opening is not a frame

    ws.onmessage?.({ data: '{"type":"pong"}' });

    expect(sock.lastMessageAt).toBeGreaterThan(0);
    sock.close();
  });

  it("replaces an open socket that has gone quiet", () => {
    const statuses: string[] = [];
    const sock = new ReconnectingSocket("/ws/test");
    sock.onstatus = (s) => statuses.push(s);
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    // Four keepalive rounds, no pong: the connection died under a suspended app.
    vi.advanceTimersByTime(121_000);
    sock.connect();

    expect(ws.readyState).toBe(3);
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(statuses).toEqual(["connecting", "online", "connecting"]);
    sock.close();
  });

  it("keeps an open socket that answered recently", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    vi.advanceTimersByTime(50_000);
    ws.onmessage?.({ data: '{"type":"pong"}' });
    vi.advanceTimersByTime(20_000);
    sock.connect();

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(ws.readyState).toBe(1);
    sock.close();
  });

  it("keeps a socket that opened recently and has not spoken yet", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    // No frame at all yet — the first pong is still on its way. Opening counts.
    vi.advanceTimersByTime(50_000);
    sock.connect();

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(ws.readyState).toBe(1);
    sock.close();
  });

  it("keeps a live socket through a long turn: pong latency is not liveness", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    // One pong, then silence for a full conscious-engine turn (60 s), plus the
    // ping interval the last pong can predate it by, plus slack for the round trip.
    vi.advanceTimersByTime(30_000);
    ws.onmessage?.({ data: '{"type":"pong"}' });
    vi.advanceTimersByTime(100_000);
    sock.connect();

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(ws.readyState).toBe(1);
    sock.close();
  });

  it("reopens a socket the server refused, once asked", () => {
    const sock = new ReconnectingSocket("/ws/test");
    sock.connect();
    FakeWebSocket.instances[0].emitClose(4001);

    sock.connect();

    expect(FakeWebSocket.instances).toHaveLength(2);
    sock.close();
  });
});

describe("TelemetrySocket", () => {
  it("replays subscriptions on reconnect", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events", "actions"]);
    FakeWebSocket.instances[0].emitClose(1006);
    vi.advanceTimersByTime(600);
    FakeWebSocket.instances[1].open();
    const replayed = FakeWebSocket.instances[1].sent.map((s) => JSON.parse(s));
    expect(replayed).toContainEqual({ type: "subscribe", streams: ["events", "actions"] });
  });
});
