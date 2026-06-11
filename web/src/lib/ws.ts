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
    this.stopped = false;
    this.onstatus(this.attempts === 0 ? "connecting" : "reconnecting");
    this.ws = new WebSocket(this.url());
    this.ws.onopen = () => {
      this.attempts = 0;
      this.onstatus("online");
      this.onopen();
    };
    this.ws.onmessage = (e) => {
      try { this.onmessage(JSON.parse(e.data as string)); } catch { /* non-JSON frame */ }
    };
    this.ws.onclose = (e) => {
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
