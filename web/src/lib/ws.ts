export type SocketStatus = "connecting" | "online" | "reconnecting" | "offline" | "unauthorized";

const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 8000;

export class ReconnectingSocket {
  private path: string;
  private ws: WebSocket | null = null;
  private attempts = 0;
  private stopped = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  onmessage: (data: unknown) => void = () => {};
  onstatus: (status: SocketStatus) => void = () => {};
  onopen: () => void = () => {};

  constructor(path: string) {
    this.path = path;
  }

  private url(): string {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}${this.path}`;
  }

  connect(): void {
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
      this.onstatus("online");
      this.onopen();
    };
    ws.onmessage = (e) => {
      if (this.ws !== ws) return;
      try { this.onmessage(JSON.parse(e.data as string)); } catch { /* non-JSON frame */ }
    };
    ws.onclose = (e) => {
      if (this.ws !== ws) return;  // superseded socket — ignore its close
      if (e.code === 4001) { this.onstatus("unauthorized"); return; }
      if (this.stopped) { this.onstatus("offline"); return; }
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
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
    this.ws?.close();
  }
}
