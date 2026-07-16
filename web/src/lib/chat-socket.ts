import type { ChatServerMessage } from "./types";
import { ReconnectingSocket, type SocketStatus } from "./ws";

const SESSION_KEY = "alfred_session_id";

export class ChatSocket {
  private socket = new ReconnectingSocket("/ws");
  private listeners = new Set<(msg: ChatServerMessage) => void>();
  private firstMessageSent = false;
  sessionId: string | null = localStorage.getItem(SESSION_KEY);

  onstatus: (s: SocketStatus) => void = () => {};

  constructor() {
    this.socket.onstatus = (s) => this.onstatus(s);
    this.socket.onopen = () => { this.firstMessageSent = false; };
    this.socket.onmessage = (data) => {
      const msg = data as ChatServerMessage;
      if (msg.type === "session") {
        // Server assigns; we may override with our stored id on first send.
        if (!this.sessionId) {
          this.sessionId = msg.session_id;
          localStorage.setItem(SESSION_KEY, msg.session_id);
        }
      }
      for (const fn of this.listeners) fn(msg);
    };
  }

  connect(): void { this.socket.connect(); }
  close(): void { this.socket.close(); }

  private payload(type: "text" | "audio", content: string): Record<string, unknown> {
    const body: Record<string, unknown> = { type, content, channel: "web_pwa" };
    if (!this.firstMessageSent && this.sessionId) body.session_id = this.sessionId;
    this.firstMessageSent = true;
    return body;
  }

  sendText(content: string): boolean { return this.socket.send(this.payload("text", content)); }
  sendAudio(dataUrl: string): boolean { return this.socket.send(this.payload("audio", dataUrl)); }

  listen(fn: (msg: ChatServerMessage) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}
