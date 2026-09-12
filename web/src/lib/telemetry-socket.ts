import type { TelemetryMessage } from "./types";
import { ReconnectingSocket, type SocketStatus } from "./ws";

/**
 * The one telemetry socket, shared by everything that wants stream frames.
 * Subscriptions are counted per stream: the Door wants `home_action_results`
 * for the life of the app, the Workshop wants all eight only while it is up,
 * and neither may cancel the other's. The server hears `subscribe` when a
 * stream is first wanted, `unsubscribe` when the last wanter leaves, and a
 * replay of everything still wanted on each reconnect.
 *
 * The contract is that every `subscribe` owes exactly one `unsubscribe`; an
 * unpaired extra inflates that stream's count for the life of the app. The
 * Door does this deliberately — it never gives `home_action_results` back —
 * and does it again for each of StrictMode's setup→cleanup→setup rounds, since
 * its cleanup only drops the listener. That is the safe direction to be wrong
 * in: an over-counted stream costs frames nobody reads, while one released
 * early goes quiet on a surface still watching it.
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

  // Both methods walk `new Set(streams)`: a name repeated in one call is one
  // wanter, or the count outruns the frames sent and the matching single
  // unsubscribe never releases it.
  subscribe(streams: string[]): void {
    const fresh: string[] = [];
    for (const s of new Set(streams)) {
      const count = this.wanted.get(s) ?? 0;
      this.wanted.set(s, count + 1);
      if (count === 0) fresh.push(s);
    }
    if (fresh.length > 0) this.socket.send({ type: "subscribe", streams: fresh });
  }

  unsubscribe(streams: string[]): void {
    const released: string[] = [];
    for (const s of new Set(streams)) {
      const count = this.wanted.get(s);
      if (count === undefined) continue; // never wanted — nothing to give up
      if (count > 1) this.wanted.set(s, count - 1);
      else {
        this.wanted.delete(s);
        released.push(s);
      }
    }
    if (released.length > 0) this.socket.send({ type: "unsubscribe", streams: released });
  }

  listen(fn: (msg: TelemetryMessage) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}
