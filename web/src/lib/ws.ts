export type SocketStatus = "connecting" | "online" | "reconnecting" | "offline" | "unauthorized";

const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 8000;
/** Cloudflare closes an idle proxied socket at ~100 s; 30 s keeps it comfortably alive. */
const DEFAULT_PING_MS = 30_000;

export interface SocketOptions {
  pingIntervalMs?: number;
}

export class ReconnectingSocket {
  private path: string;
  private ws: WebSocket | null = null;
  private attempts = 0;
  private stopped = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingIntervalMs: number;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  /** `Date.now()` of the last frame from the server, keepalive pongs included. */
  lastMessageAt: number | null = null;
  private openedAt = 0;

  onmessage: (data: unknown) => void = () => {};
  onstatus: (status: SocketStatus) => void = () => {};
  onopen: () => void = () => {};

  constructor(path: string, options: SocketOptions = {}) {
    this.path = path;
    this.pingIntervalMs = options.pingIntervalMs ?? DEFAULT_PING_MS;
  }

  private url(): string {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}${this.path}`;
  }

  private startPing(ws: WebSocket): void {
    this.stopPing();
    if (this.pingIntervalMs <= 0) return;
    this.pingTimer = setInterval(() => {
      if (this.ws !== ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ type: "ping" }));
    }, this.pingIntervalMs);
  }

  private stopPing(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  /**
   * Whether the open socket has gone silent for two keepalive rounds. A socket
   * that died while the PWA was suspended can still read OPEN — no close event
   * arrives for a connection the OS dropped, and `send()` on it does not throw —
   * so the pongs that stopped coming are the only evidence.
   */
  private quiet(ws: WebSocket): boolean {
    if (this.pingIntervalMs <= 0 || ws.readyState !== WebSocket.OPEN) return false;
    const lastSeen = Math.max(this.openedAt, this.lastMessageAt ?? 0);
    return Date.now() - lastSeen > 2 * this.pingIntervalMs;
  }

  connect(): void {
    // A quiet socket is replaced, not kept: `this.ws` moves on first, so the
    // close event the old one produces is ignored below as a superseded socket's.
    if (this.ws && this.quiet(this.ws)) {
      const dead = this.ws;
      this.ws = null;
      this.stopPing();
      dead.close();
    }
    // Bail if a socket is already live. Without this, a second connect() (React
    // StrictMode's setup→cleanup→setup, or a fast logout/login) overwrites this.ws
    // while the previous socket's onclose still fires and spawns a duplicate,
    // permanently-reconnecting connection. Every handler is also pinned to its own
    // socket so a stale socket can never mutate state after being replaced.
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)
    ) {
      return;
    }
    this.stopped = false;
    this.onstatus(this.attempts === 0 ? "connecting" : "reconnecting");
    const ws = new WebSocket(this.url());
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.attempts = 0;
      this.openedAt = Date.now();
      this.startPing(ws);
      this.onstatus("online");
      this.onopen();
    };
    ws.onmessage = (e) => {
      if (this.ws !== ws) return;
      this.lastMessageAt = Date.now();
      try {
        this.onmessage(JSON.parse(e.data as string));
      } catch {
        /* non-JSON frame */
      }
    };
    ws.onclose = (e) => {
      if (this.ws !== ws) return; // superseded socket — ignore its close
      this.stopPing();
      if (e.code === 4001) {
        this.onstatus("unauthorized");
        return;
      }
      if (this.stopped) {
        this.onstatus("offline");
        return;
      }
      this.onstatus("reconnecting");
      const delay = Math.min(BASE_DELAY_MS * 2 ** this.attempts, MAX_DELAY_MS);
      this.attempts += 1;
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        if (!this.stopped) this.connect();
      }, delay);
    };
  }

  send(payload: unknown): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
      return true;
    }
    return false;
  }

  close(): void {
    this.stopped = true;
    this.stopPing();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
  }
}
