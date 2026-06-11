import type { TelemetryMessage } from "./types";
import { ReconnectingSocket, type SocketStatus } from "./ws";

export class TelemetrySocket {
  private socket = new ReconnectingSocket("/ws/telemetry");
  private subscriptions = new Set<string>();
  private listeners = new Set<(msg: TelemetryMessage) => void>();

  onstatus: (s: SocketStatus) => void = () => {};

  constructor() {
    this.socket.onstatus = (s) => this.onstatus(s);
    this.socket.onmessage = (data) => {
      for (const fn of this.listeners) fn(data as TelemetryMessage);
    };
    this.socket.onopen = () => {
      if (this.subscriptions.size > 0) {
        this.socket.send({ type: "subscribe", streams: [...this.subscriptions] });
      }
    };
  }

  connect(): void { this.socket.connect(); }
  close(): void { this.socket.close(); }

  subscribe(streams: string[]): void {
    for (const s of streams) this.subscriptions.add(s);
    this.socket.send({ type: "subscribe", streams });
  }

  listen(fn: (msg: TelemetryMessage) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}
