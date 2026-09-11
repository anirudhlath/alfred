import type { TelemetryMessage } from "./types";
import { ReconnectingSocket, type SocketStatus } from "./ws";

/**
 * The one telemetry socket, shared by everything that wants stream frames.
 * Subscriptions are counted per stream: the Door wants `home_action_results`
 * for the life of the app, the Workshop wants all eight only while it is up,
 * and neither may cancel the other's. The server hears `subscribe` when a
 * stream is first wanted, `unsubscribe` when the last wanter leaves, and a
 * replay of everything still wanted on each reconnect.
 */
export class TelemetrySocket {
  private socket = new ReconnectingSocket("/ws/telemetry");
  private wanted = new Map<string, number>();
  private listeners = new Set<(msg: TelemetryMessage) => void>();
  onstatus: (s: SocketStatus) => void = () => {};

  constructor() {
    this.socket.onstatus = (s) => this.onstatus(s);
    this.socket.onmessage = (data) => {
      for (const fn of this.listeners) fn(data as TelemetryMessage);
    };
    this.socket.onopen = () => {
      if (this.wanted.size > 0) {
        this.socket.send({ type: "subscribe", streams: [...this.wanted.keys()] });
      }
    };
  }

  connect(): void {
    this.socket.connect();
  }

  close(): void {
    this.socket.close();
  }

  subscribe(streams: string[]): void {
    const fresh: string[] = [];
    for (const s of streams) {
      const count = this.wanted.get(s) ?? 0;
      this.wanted.set(s, count + 1);
      if (count === 0) fresh.push(s);
    }
    if (fresh.length > 0) this.socket.send({ type: "subscribe", streams: fresh });
  }

  unsubscribe(streams: string[]): void {
    const done: string[] = [];
    for (const s of streams) {
      const count = this.wanted.get(s) ?? 0;
      if (count <= 1) {
        if (count === 1) done.push(s);
        this.wanted.delete(s);
      } else {
        this.wanted.set(s, count - 1);
      }
    }
    if (done.length > 0) this.socket.send({ type: "unsubscribe", streams: done });
  }

  listen(fn: (msg: TelemetryMessage) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}
