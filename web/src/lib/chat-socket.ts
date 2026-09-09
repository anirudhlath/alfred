import { SESSION_IDLE_MS } from "./history";
import type { ChatServerMessage } from "./types";
import { ReconnectingSocket, type SocketStatus } from "./ws";

/**
 * Every `localStorage` key is `alfred.<noun>` (see `THEME_KEY`, `DEVICE_KEY`,
 * `UNSENT_KEY`). This one was `alfred_session_id` in the client this one
 * replaced; the rename costs a phone one fresh conversation session.
 */
const SESSION_KEY = "alfred.session";
/**
 * ISO stamp of the last send. The server forgets a session after its idle
 * timeout and would otherwise restore the old id with an empty context; this is
 * how the client knows to let the id go too. A phone from before the stamp
 * existed reads as idle — one fresh session.
 */
const SESSION_AT_KEY = "alfred.session-at";

export class ChatSocket {
  private socket = new ReconnectingSocket("/ws");
  private listeners = new Set<(msg: ChatServerMessage) => void>();
  private firstMessageSent = false;
  /** The id the server assigned this connection; adopted when ours is gone or idle. */
  private assigned: string | null = null;
  sessionId: string | null = localStorage.getItem(SESSION_KEY);
  private idleMs = SESSION_IDLE_MS;

  onstatus: (s: SocketStatus) => void = () => {};

  constructor() {
    this.socket.onstatus = (s) => this.onstatus(s);
    this.socket.onopen = () => {
      this.firstMessageSent = false;
      // The previous connection's assigned id means nothing to this one, which
      // will announce its own — and ours must be offered again until it does.
      this.assigned = null;
    };
    this.socket.onmessage = (data) => {
      const msg = data as ChatServerMessage;
      // Keepalive plumbing. `lastMessageAt` on the socket already recorded it;
      // nothing above this layer should have to skip it.
      if (msg.type === "pong") return;
      if (msg.type === "session") {
        // The server assigns one per connection. Ours wins on the first send if
        // it is still live (web_server.py restores it); otherwise this is the id.
        this.assigned = msg.session_id;
        if (!this.sessionId) this.adopt(msg.session_id);
      }
      for (const fn of this.listeners) fn(msg);
    };
  }

  connect(): void { this.socket.connect(); }
  close(): void { this.socket.close(); }

  /** Follow the server's own idle timeout (`Overview.session.idle_minutes`); the Room sets it. */
  setIdleMs(ms: number): void {
    this.idleMs = ms;
  }

  private adopt(id: string): void {
    this.sessionId = id;
    localStorage.setItem(SESSION_KEY, id);
  }

  /** No stamp and an unreadable one both read as idle — `NaN` fails the comparison. */
  private idle(): boolean {
    const at = localStorage.getItem(SESSION_AT_KEY);
    return at === null || !(Date.now() - Date.parse(at) < this.idleMs);
  }

  private payload(type: "text" | "audio", content: string): Record<string, unknown> {
    const body: Record<string, unknown> = {
      type,
      content,
      channel: "web_pwa",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
    if (!this.firstMessageSent) {
      // The server reads session_id from the first message only, then locks it
      // for the connection — so this is the one place a session can turn over.
      if (this.sessionId && this.idle()) {
        this.sessionId = null;
        localStorage.removeItem(SESSION_KEY);
        if (this.assigned) this.adopt(this.assigned);
      }
      // Only ever carry an id the server does not already have.
      if (this.sessionId && this.sessionId !== this.assigned) body.session_id = this.sessionId;
    }
    this.firstMessageSent = true;
    localStorage.setItem(SESSION_AT_KEY, new Date().toISOString());
    return body;
  }

  sendText(content: string): boolean { return this.socket.send(this.payload("text", content)); }
  sendAudio(dataUrl: string): boolean { return this.socket.send(this.payload("audio", dataUrl)); }

  listen(fn: (msg: ChatServerMessage) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
}
