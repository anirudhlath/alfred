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
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

/** Fail `count` tries in a row, waiting out each backoff. Returns the statuses heard. */
function failTries(sock: ReconnectingSocket, count: number): string[] {
  const statuses: string[] = [];
  sock.onstatus = (s) => statuses.push(s);
  sock.connect();
  for (let i = 0; i < count; i += 1) {
    FakeWebSocket.instances.at(-1)!.emitClose(1006);
    vi.advanceTimersByTime(8000);
  }
  return statuses;
}

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

  it("says reconnecting for the first failures and offline from the third", () => {
    const sock = new ReconnectingSocket("/ws/test");
    const statuses = failTries(sock, 3);
    // Each failed try is heard twice: at the close and again as the next opens.
    expect(statuses).toEqual([
      "connecting",
      "reconnecting", "reconnecting",
      "reconnecting", "reconnecting",
      "offline", "offline",
    ]);
  });

  it("keeps trying while offline, and says offline throughout", () => {
    const sock = new ReconnectingSocket("/ws/test");
    const statuses = failTries(sock, 6);
    expect(FakeWebSocket.instances).toHaveLength(7);
    expect(statuses.slice(statuses.indexOf("offline"))).toEqual(Array<string>(8).fill("offline"));
  });

  it("is online the moment a try succeeds, and starts the count over", () => {
    const sock = new ReconnectingSocket("/ws/test");
    const statuses = failTries(sock, 4);
    FakeWebSocket.instances.at(-1)!.open();
    expect(statuses.at(-1)).toBe("online");
    FakeWebSocket.instances.at(-1)!.emitClose(1006);
    expect(statuses.at(-1)).toBe("reconnecting");
  });

  it("says offline at the first close when the browser knows it is", () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const sock = new ReconnectingSocket("/ws/test");
    const statuses = failTries(sock, 1);
    expect(statuses).toEqual(["connecting", "offline", "offline"]);
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

  it("hands the app the open before it says online", () => {
    // ChatSocket clears its per-connection session state in onopen, and the
    // "online" status is what makes useRoom flush the unsent queue — a flush
    // that ran first would be the new connection's first message with no
    // session_id, and the server locks a fresh id on that.
    const sock = new ReconnectingSocket("/ws/test");
    const order: string[] = [];
    sock.onopen = () => order.push("open");
    sock.onstatus = (s) => {
      if (s === "online") order.push("online");
    };
    sock.connect();
    FakeWebSocket.instances[0].open();
    expect(order).toEqual(["open", "online"]);
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
  /** Every frame the newest fake socket has been asked to send. */
  function sent(): unknown[] {
    return FakeWebSocket.instances.at(-1)!.sent.map((s) => JSON.parse(s));
  }

  it("replays subscriptions on reconnect", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events", "actions"]);
    FakeWebSocket.instances[0].emitClose(1006);
    vi.advanceTimersByTime(600);
    FakeWebSocket.instances[1].open();
    expect(sent()).toEqual([{ type: "subscribe", streams: ["events", "actions"] }]);
  });

  it("only tells the server about a stream the first time it is wanted", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events"]);
    sock.subscribe(["events", "actions"]);
    expect(sent()).toEqual([
      { type: "subscribe", streams: ["events"] },
      { type: "subscribe", streams: ["actions"] },
    ]);
  });

  it("subscribes on first open for a stream wanted before the socket was up", () => {
    const sock = new TelemetrySocket();
    sock.subscribe(["events"]); // the Door's real ordering: no socket yet
    sock.connect();
    expect(sent()).toEqual([]); // CONNECTING — send() dropped it
    FakeWebSocket.instances[0].open();
    expect(sent()).toEqual([{ type: "subscribe", streams: ["events"] }]);
  });

  it("counts a stream named twice in one call as one wanter", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events", "events"]);
    sock.unsubscribe(["events"]);
    expect(sent()).toEqual([
      { type: "subscribe", streams: ["events"] },
      { type: "unsubscribe", streams: ["events"] },
    ]);
  });

  it("only unsubscribes a stream once nobody wants it", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events", "actions"]);
    sock.subscribe(["events"]);
    sock.unsubscribe(["events", "actions"]);
    // The whole array each time, not just the last frame: `events` still has a
    // wanter, so no frame may mention it yet — in any position.
    expect(sent()).toEqual([
      { type: "subscribe", streams: ["events", "actions"] },
      { type: "unsubscribe", streams: ["actions"] },
    ]);
    sock.unsubscribe(["events"]);
    expect(sent()).toEqual([
      { type: "subscribe", streams: ["events", "actions"] },
      { type: "unsubscribe", streams: ["actions"] },
      { type: "unsubscribe", streams: ["events"] },
    ]);
    // Unsubscribing a stream already released sends nothing.
    sock.unsubscribe(["events"]);
    expect(sent()).toHaveLength(3);
  });

  it("replays only what is still wanted", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events", "actions"]);
    sock.unsubscribe(["events"]);
    FakeWebSocket.instances[0].emitClose(1006);
    vi.advanceTimersByTime(600);
    FakeWebSocket.instances[1].open();
    expect(sent()).toEqual([{ type: "subscribe", streams: ["actions"] }]);
  });
});
