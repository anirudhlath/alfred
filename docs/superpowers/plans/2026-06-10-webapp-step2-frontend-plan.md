# Web App Rebuild — Step 2: Frontend (Vite + React + shadcn) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the vanilla JS app in `web/` with a Vite + React + TypeScript SPA — Mission Control design, Cockpit shell — covering chat/voice, login, onboarding, settings, and the full observatory (activity, memory, triggers, health), per `docs/superpowers/specs/2026-06-10-web-app-rebuild-design.md`.

**Architecture:** SPA built to `web/dist/`, served by FastAPI with an SPA fallback. REST via a typed fetch client + TanStack Query; live data via two reconnecting WebSockets (`/ws` chat, `/ws/telemetry` streams). One dark Mission Control theme expressed entirely through CSS variables — no theme switching ("no unnecessary theming").

**Tech Stack:** Vite, React 19, TypeScript (strict), Tailwind v4, shadcn/ui, react-router-dom v7, @tanstack/react-query v5, vitest + @testing-library/react, JetBrains Mono + Inter Variable (self-hosted via @fontsource — local-first, no CDN).

**Prerequisites:**
- Step 1 plan (admin API + `/ws/telemetry`) implemented on this branch.
- Node 22+ (`node --version`).
- This branch deletes the old `web/` app in Task 1. The old files stay retrievable via `git show master:web/app.js` etc. — Tasks 6 and 14 port logic from them.

**Conventions for every task:**
- TypeScript strict; no `any` unless interfacing with WebAuthn browser APIs (cast at the boundary).
- All commands run from `web/`: `npm run dev` (HMR, proxies to :8081), `npm test` (vitest), `npm run build`, `npm run lint`.
- Use the design tokens (Task 2) — never raw hex in components. Source colors: reflex=cyan, conscious=green, memory=amber, trigger=pink.
- Monospace (`font-mono`) for telemetry/data; sans for chat/body text.

---

### Task 1: Scaffold

**Files:**
- Delete: `web/app.js`, `web/auth.js`, `web/index.html`, `web/settings.html`, `web/settings.js`, `web/style.css` (keep `web/manifest.json`, `web/icon.svg` — moved into `web/public/`)
- Create: Vite project in `web/` (package.json, vite.config.ts, tsconfig*.json, index.html, src/main.tsx, src/App.tsx, src/index.css, src/test/setup.ts)
- Modify: `.gitignore` (repo root)

- [ ] **Step 1: Remove old app, scaffold Vite**

```bash
cd web && mkdir -p public && git mv manifest.json icon.svg public/ 2>/dev/null || (mv manifest.json icon.svg public/)
git rm app.js auth.js index.html settings.html settings.js style.css
npm create vite@latest . -- --template react-ts
npm install
npm install tailwindcss @tailwindcss/vite react-router-dom @tanstack/react-query \
  @fontsource-variable/inter @fontsource/jetbrains-mono react-markdown
npm install -D vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

- [ ] **Step 2: Configure TypeScript path alias** — in `tsconfig.json` AND `tsconfig.app.json` `compilerOptions`:

```json
"baseUrl": ".",
"paths": { "@/*": ["./src/*"] }
```

- [ ] **Step 3: Write `vite.config.ts`**

```ts
/// <reference types="vitest/config" />
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    proxy: {
      "/api": "http://localhost:8081",
      "/health": "http://localhost:8081",
      "/ws": { target: "ws://localhost:8081", ws: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
});
```

`src/test/setup.ts`:

```ts
import "@testing-library/jest-dom";
```

- [ ] **Step 4: Initialize shadcn**

```bash
# index.css must contain `@import "tailwindcss";` first (replace Vite's default CSS)
npx shadcn@latest init   # style: new-york, base color: zinc, CSS variables: yes
npx shadcn@latest add button card input textarea badge tabs dialog command switch \
  select table tooltip separator scroll-area skeleton sonner progress alert sheet
```

This generates `components.json`, `src/components/ui/*`, `src/lib/utils.ts` (the `cn` helper).

- [ ] **Step 5: npm scripts** — ensure `package.json` has:

```json
"scripts": {
  "dev": "vite",
  "build": "tsc -b && vite build",
  "lint": "eslint .",
  "test": "vitest run",
  "preview": "vite preview"
}
```

- [ ] **Step 6: Update repo `.gitignore`** — append:

```
# Web frontend
web/node_modules/
web/dist/
```

- [ ] **Step 7: Verify scaffold builds and tests run**

Run: `npm run build && npm test`
Expected: build succeeds producing `web/dist/`; vitest reports "no test files" (exit 0 with `--passWithNoTests` — add that flag to the test script if it exits non-zero).

- [ ] **Step 8: Commit**

```bash
git add -A web/ .gitignore
git commit -m "feat(web): scaffold Vite + React + TS + Tailwind v4 + shadcn, remove vanilla app"
```

---

### Task 2: Mission Control theme + format helpers

**Files:**
- Modify: `web/src/index.css` (replace shadcn defaults with the Mission Control palette)
- Create: `web/src/lib/format.ts`
- Test: `web/src/lib/format.test.ts`

- [ ] **Step 1: Write `index.css`** — single dark theme; shadcn variables mapped to Mission Control values; source-color tokens registered with Tailwind via `@theme`:

```css
@import "tailwindcss";
@import "tw-animate-css";

:root {
  /* Mission Control — single dark theme by design (no light mode, no switching) */
  --background: #090c12;
  --foreground: #e2e8f0;
  --card: #0b101a;
  --card-foreground: #e2e8f0;
  --popover: #0b101a;
  --popover-foreground: #e2e8f0;
  --primary: #7dd3fc;
  --primary-foreground: #06080c;
  --secondary: #13203a;
  --secondary-foreground: #e2e8f0;
  --muted: #141c29;
  --muted-foreground: #64748b;
  --accent: #13203a;
  --accent-foreground: #e2e8f0;
  --destructive: #f87171;
  --border: #1a2230;
  --input: #1a2230;
  --ring: #7dd3fc;
  --radius: 0.5rem;

  /* Panels darker than content background */
  --panel: #070a0f;

  /* Source colors — the one color language, used everywhere */
  --reflex: #7dd3fc;
  --conscious: #4ade80;
  --memory: #facc15;
  --trigger: #f472b6;
  --home: #38bdf8;
  --user: #94a3b8;

  /* Status */
  --ok: #4ade80;
  --warn: #facc15;
  --bad: #f87171;
}

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --color-panel: var(--panel);
  --color-reflex: var(--reflex);
  --color-conscious: var(--conscious);
  --color-memory: var(--memory);
  --color-trigger: var(--trigger);
  --color-home: var(--home);
  --color-user: var(--user);
  --color-ok: var(--ok);
  --color-warn: var(--warn);
  --color-bad: var(--bad);
  --font-sans: "Inter Variable", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
  --radius-lg: var(--radius);
  --radius-md: calc(var(--radius) - 2px);
  --radius-sm: calc(var(--radius) - 4px);
}

body {
  @apply bg-background text-foreground font-sans antialiased;
}

/* Live pulse dot — aliveness is data motion, not decoration */
@keyframes pulse-glow {
  0%, 100% { opacity: 1; box-shadow: 0 0 6px currentColor; }
  50% { opacity: 0.5; box-shadow: 0 0 2px currentColor; }
}
.pulse-dot {
  @apply inline-block size-1.5 rounded-full;
  animation: pulse-glow 2s ease-in-out infinite;
}
```

In `src/main.tsx`, import fonts before CSS:

```ts
import "@fontsource-variable/inter";
import "@fontsource/jetbrains-mono";
import "./index.css";
```

- [ ] **Step 2: Write the failing tests for format helpers**

```ts
import { describe, expect, it } from "vitest";
import { categorize, summarize, timeOf } from "./format";

describe("categorize", () => {
  it("maps streams to categories", () => {
    expect(categorize("reflex_observations", {})).toBe("reflex");
    expect(categorize("user_responses", {})).toBe("conscious");
    expect(categorize("notifications", {})).toBe("trigger");
  });
  it("maps events stream by event_type", () => {
    expect(categorize("events", { event_type: "trigger_fired" })).toBe("trigger");
    expect(categorize("events", { event_type: "state_changed" })).toBe("home");
  });
  it("maps actions by source", () => {
    expect(categorize("actions", { event_type: "action_request", source: "reflex-engine" })).toBe("reflex");
    expect(categorize("actions", { event_type: "action_request", source: "conscious" })).toBe("conscious");
  });
});

describe("summarize", () => {
  it("summarizes state changes", () => {
    expect(
      summarize("events", { event_type: "state_changed", entity_id: "light.study", new_state: "off" }),
    ).toBe("light.study → off");
  });
  it("summarizes action requests", () => {
    expect(summarize("actions", { event_type: "action_request", tool_name: "dim_lights" })).toBe("dim_lights");
  });
  it("falls back to event_type", () => {
    expect(summarize("events", { event_type: "mystery" })).toBe("mystery");
  });
});

describe("timeOf", () => {
  it("formats a stream id as HH:MM:SS", () => {
    expect(timeOf("1718000000000-0")).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });
});
```

- [ ] **Step 3: Run to verify failure** — `npm test` → FAIL (module missing)

- [ ] **Step 4: Implement `format.ts`**

```ts
export type SourceCategory =
  | "reflex" | "conscious" | "memory" | "trigger" | "user" | "home" | "system";

type Ev = Record<string, unknown>;

export function categorize(stream: string, event: Ev): SourceCategory {
  switch (stream) {
    case "reflex_observations": return "reflex";
    case "user_requests": return "user";
    case "user_responses": return "conscious";
    case "notifications": return "trigger";
    case "home_state":
    case "home_action_results": return "home";
  }
  const type = String(event.event_type ?? "");
  if (type.startsWith("trigger_")) return "trigger";
  if (type === "state_changed") return "home";
  if (type === "action_request" || type === "action_result") {
    const source = String(event.source ?? "");
    if (source.includes("reflex")) return "reflex";
    if (source.includes("trigger")) return "trigger";
    return "conscious";
  }
  return "system";
}

export const CATEGORY_CLASS: Record<SourceCategory, string> = {
  reflex: "text-reflex",
  conscious: "text-conscious",
  memory: "text-memory",
  trigger: "text-trigger",
  user: "text-user",
  home: "text-home",
  system: "text-muted-foreground",
};

export function summarize(stream: string, event: Ev): string {
  const type = String(event.event_type ?? "");
  if (type === "state_changed") return `${event.entity_id} → ${event.new_state}`;
  if (type === "action_request" || type === "action_result")
    return String(event.tool_name ?? type);
  if (type === "trigger_fired") return `${event.trigger_name} fired`;
  if (type === "user_request") return String(event.content ?? "").slice(0, 60);
  if (type === "alfred_response") return String(event.text ?? "").slice(0, 60);
  if (type === "reflex_observation") {
    const action = event.action as Ev | undefined;
    return String(action?.tool_name ?? "observation");
  }
  if (stream === "notifications") return String(event.title ?? "notification");
  return type || "event";
}

export function timeOf(streamId: string): string {
  const ms = Number(streamId.split("-")[0]);
  return new Date(ms).toLocaleTimeString("en-GB", { hour12: false });
}
```

- [ ] **Step 5: Run tests** — `npm test` → PASS. **Commit:**

```bash
git add web/src && git commit -m "feat(web): Mission Control theme tokens + source categorization"
```

---

### Task 3: Types + API client + Query setup

**Files:**
- Create: `web/src/lib/types.ts`, `web/src/lib/api.ts`
- Test: `web/src/lib/api.test.ts`

- [ ] **Step 1: Write `types.ts`** — mirrors the Step 1 backend payloads exactly:

```ts
export interface StreamSummary { length: number; last_id: string | null; last_ts: number | null }

export interface Overview {
  redis: { connected: boolean };
  cost: { date: string; spend_usd: number; cap_usd: number; alert_sent?: boolean } | null;
  dnd: { active: boolean; until?: string | null; reason?: string | null; source?: string };
  counts: { sessions: number; devices: number; deferred: number; triggers: number };
  streams: Record<string, StreamSummary>;
  inference: { ollama: boolean; lmstudio: boolean };
}

export interface StreamEntry { id: string; event: Record<string, unknown> }
export interface StreamPage { entries: StreamEntry[]; next_before: string | null }

export interface EpisodicEntry {
  store: "hot" | "cold";
  score?: number;
  [key: string]: unknown; // defensive — hash fields vary (content, summary, significance, ...)
}
export interface SemanticFile { name: string; dir: string; content: string; modified: string }
export interface Routine {
  name: string; trigger_pattern: string; confidence: number;
  state: "candidate" | "active" | "dormant" | "archived";
  steps: { description: string }[]; last_hit: string | null;
}

export interface Trigger {
  trigger_id: string; trigger_type: string; name: string; enabled: boolean;
  one_shot: boolean; created_by?: string; created_at?: string; last_fired?: string | null;
  urgency?: string; action?: { tool_name: string; target_service: string } | null;
  [key: string]: unknown;
}

export interface DeferredNotification {
  notification_id: string; title: string; body: string; urgency: string;
  source: string; timestamp: string;
}
export interface SessionInfo {
  session_id: string; channel: string; created_at?: string; turns: number; ttl_seconds: number;
}
export interface DeviceInfo { device_token: string; platform?: string; identity?: string; registered_at?: string }

export interface CredentialField {
  label: string; field_type: "text" | "password" | "url"; required: boolean;
  placeholder: string; default: string; help_text: string; transient: boolean;
}
export interface IntegrationInfo {
  name: string; category: string;
  schema: { fields: Record<string, CredentialField> };
  configured: Record<string, boolean>;
}

export interface AuthStatus { registered: boolean; authenticated: boolean }

/** Chat WS server→client messages (existing /ws protocol — unchanged) */
export type ChatServerMessage =
  | { type: "session"; session_id: string }
  | { type: "transcription"; text: string; session_id: string }
  | { type: "response"; text: string; audio?: string; session_id: string;
      actions_taken?: string[]; mood?: string }
  | { type: "notification"; title: string; body: string; urgency: string;
      notification_id: string; audio?: string }
  | { type: "error"; text: string; session_id?: string };

/** Telemetry WS messages (Step 1 protocol) */
export type TelemetryMessage =
  | { type: "subscribed"; streams: string[] }
  | { type: "entry"; stream: string; id: string; event: Record<string, unknown> }
  | { type: "status"; detail: string };
```

- [ ] **Step 2: Write the failing tests for the API client**

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));
}

describe("api", () => {
  it("returns parsed JSON on success", async () => {
    stubFetch(200, { ok: true });
    await expect(api("/api/admin/overview")).resolves.toEqual({ ok: true });
  });

  it("redirects to /login on 401", async () => {
    stubFetch(401, {});
    const assign = vi.fn();
    vi.stubGlobal("location", { assign, pathname: "/health" });
    await expect(api("/x")).rejects.toThrow(ApiError);
    expect(assign).toHaveBeenCalledWith("/login");
  });

  it("throws ApiError with status on failure", async () => {
    stubFetch(500, { detail: "boom" });
    await expect(api("/x")).rejects.toMatchObject({ status: 500 });
  });
});
```

- [ ] **Step 3: Run to verify failure**, then **implement `api.ts`:**

```ts
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (resp.status === 401) {
    if (location.pathname !== "/login") location.assign("/login");
    throw new ApiError(401, "Authentication required");
  }
  if (!resp.ok) throw new ApiError(resp.status, await resp.text());
  return (await resp.json()) as T;
}

export const post = <T>(path: string, body?: unknown): Promise<T> =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const del = <T>(path: string): Promise<T> => api<T>(path, { method: "DELETE" });
```

- [ ] **Step 4: Run tests** — PASS. **Commit:**

```bash
git add web/src && git commit -m "feat(web): typed API client + backend payload types"
```

---

### Task 4: WebSocket layer

**Files:**
- Create: `web/src/lib/ws.ts`, `web/src/lib/chat-socket.ts`, `web/src/lib/telemetry-socket.ts`
- Test: `web/src/lib/ws.test.ts`

- [ ] **Step 1: Write the failing tests** — fake-timer reconnect/backoff, 4001 no-retry, telemetry resubscribe-on-reconnect:

```ts
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
  constructor(public url: string) { FakeWebSocket.instances.push(this); }
  send(data: string) { this.sent.push(data); }
  close() { this.readyState = 3; this.onclose?.({ code: 1000 }); }
  open() { this.readyState = 1; this.onopen?.(); }
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
    FakeWebSocket.instances[0].onclose?.({ code: 1006 });
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(600);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("does not reconnect after 4001 and reports unauthorized", () => {
    const statuses: string[] = [];
    const sock = new ReconnectingSocket("/ws/test");
    sock.onstatus = (s) => statuses.push(s);
    sock.connect();
    FakeWebSocket.instances[0].onclose?.({ code: 4001 });
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(statuses.at(-1)).toBe("unauthorized");
  });
});

describe("TelemetrySocket", () => {
  it("replays subscriptions on reconnect", () => {
    const sock = new TelemetrySocket();
    sock.connect();
    FakeWebSocket.instances[0].open();
    sock.subscribe(["events", "actions"]);
    FakeWebSocket.instances[0].onclose?.({ code: 1006 });
    vi.advanceTimersByTime(600);
    FakeWebSocket.instances[1].open();
    const replayed = FakeWebSocket.instances[1].sent.map((s) => JSON.parse(s));
    expect(replayed).toContainEqual({ type: "subscribe", streams: ["events", "actions"] });
  });
});
```

- [ ] **Step 2: Run to verify failure**, then **implement `ws.ts`:**

```ts
export type SocketStatus = "connecting" | "online" | "reconnecting" | "offline" | "unauthorized";

const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 8000;

export class ReconnectingSocket {
  private ws: WebSocket | null = null;
  private attempts = 0;
  private stopped = false;

  onmessage: (data: unknown) => void = () => {};
  onstatus: (status: SocketStatus) => void = () => {};
  onopen: () => void = () => {};

  constructor(private path: string) {}

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
      setTimeout(() => this.connect(), delay);
    };
  }

  send(payload: unknown): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
      return true;
    }
    return false;
  }

  close(): void { this.stopped = true; this.ws?.close(); }
}
```

**`telemetry-socket.ts`:**

```ts
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
```

**`chat-socket.ts`** — existing `/ws` protocol, session persisted in localStorage:

```ts
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
```

- [ ] **Step 3: Run tests** — PASS. **Commit:**

```bash
git add web/src && git commit -m "feat(web): reconnecting WebSocket layer (chat + telemetry)"
```

---

### Task 5: App shell — providers, router, guards, icon rail, top bar

**Files:**
- Create: `web/src/shell/AlfredProvider.tsx`, `web/src/shell/AppShell.tsx`, `web/src/shell/IconRail.tsx`, `web/src/shell/TopBar.tsx`
- Modify: `web/src/App.tsx`, `web/src/main.tsx`, `web/index.html`
- Test: `web/src/shell/IconRail.test.tsx`

- [ ] **Step 1: `web/index.html`** — title `Alfred`, `<link rel="manifest" href="/manifest.json">`, `<link rel="icon" href="/icon.svg">`, dark background on `<html>` to avoid flash:

```html
<!doctype html>
<html lang="en" style="background:#090c12">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="icon" href="/icon.svg" />
    <link rel="manifest" href="/manifest.json" />
    <title>Alfred</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 2: `AlfredProvider.tsx`** — owns the two sockets and global connection state:

```tsx
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ChatSocket } from "@/lib/chat-socket";
import { TelemetrySocket } from "@/lib/telemetry-socket";
import type { TelemetryMessage } from "@/lib/types";
import type { SocketStatus } from "@/lib/ws";

export interface FeedEntry { stream: string; id: string; event: Record<string, unknown> }

const FEED_MAX = 500;
const ALL_STREAMS = [
  "events", "actions", "user_requests", "user_responses",
  "reflex_observations", "notifications", "home_state", "home_action_results",
];

interface AlfredContextValue {
  chat: ChatSocket;
  telemetry: TelemetrySocket;
  chatStatus: SocketStatus;
  telemetryStatus: SocketStatus;
  feed: FeedEntry[];
}

const AlfredContext = createContext<AlfredContextValue | null>(null);

export function AlfredProvider({ children }: { children: React.ReactNode }) {
  const chat = useRef(new ChatSocket()).current;
  const telemetry = useRef(new TelemetrySocket()).current;
  const [chatStatus, setChatStatus] = useState<SocketStatus>("connecting");
  const [telemetryStatus, setTelemetryStatus] = useState<SocketStatus>("connecting");
  const [feed, setFeed] = useState<FeedEntry[]>([]);

  useEffect(() => {
    chat.onstatus = setChatStatus;
    telemetry.onstatus = setTelemetryStatus;
    const unlisten = telemetry.listen((msg: TelemetryMessage) => {
      if (msg.type === "entry") {
        setFeed((prev) => [{ stream: msg.stream, id: msg.id, event: msg.event }, ...prev].slice(0, FEED_MAX));
      }
    });
    chat.connect();
    telemetry.connect();
    telemetry.subscribe(ALL_STREAMS);
    return () => { unlisten(); chat.close(); telemetry.close(); };
  }, [chat, telemetry]);

  const value = useMemo(
    () => ({ chat, telemetry, chatStatus, telemetryStatus, feed }),
    [chat, telemetry, chatStatus, telemetryStatus, feed],
  );
  return <AlfredContext.Provider value={value}>{children}</AlfredContext.Provider>;
}

export function useAlfred(): AlfredContextValue {
  const ctx = useContext(AlfredContext);
  if (!ctx) throw new Error("useAlfred outside AlfredProvider");
  return ctx;
}
```

- [ ] **Step 3: `IconRail.tsx`** — left nav; active route highlighted; live dot on Activity when feed is flowing:

```tsx
import { NavLink } from "react-router-dom";
import { Activity, Brain, Heart, MessageSquare, Settings, Timer } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAlfred } from "./AlfredProvider";

const ITEMS = [
  { to: "/", label: "Chat", icon: MessageSquare },
  { to: "/activity", label: "Activity", icon: Activity },
  { to: "/memory", label: "Memory", icon: Brain },
  { to: "/triggers", label: "Triggers", icon: Timer },
  { to: "/health", label: "Health", icon: Heart },
] as const;

export function IconRail() {
  const { telemetryStatus } = useAlfred();
  return (
    <nav aria-label="Primary" className="flex w-13 flex-col items-center gap-1 border-r border-border bg-panel py-3">
      <div className="mb-3 flex size-8 items-center justify-center rounded-lg border border-secondary text-primary">◆</div>
      {ITEMS.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          title={label}
          className={({ isActive }) =>
            cn(
              "relative flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground",
              isActive && "bg-secondary text-foreground",
            )
          }
        >
          <Icon className="size-4" />
          {label === "Activity" && telemetryStatus === "online" && (
            <span className="pulse-dot absolute top-1.5 right-1.5 text-reflex bg-reflex" />
          )}
        </NavLink>
      ))}
      <NavLink to="/settings" title="Settings" className="mt-auto flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground">
        <Settings className="size-4" />
      </NavLink>
    </nav>
  );
}
```

- [ ] **Step 4: `TopBar.tsx`** — connection truth (worst of the two sockets):

```tsx
import { useAlfred } from "./AlfredProvider";
import { cn } from "@/lib/utils";

const LABEL: Record<string, { text: string; dot: string }> = {
  online: { text: "ALFRED ONLINE", dot: "bg-ok text-ok" },
  connecting: { text: "CONNECTING", dot: "bg-warn text-warn" },
  reconnecting: { text: "RECONNECTING", dot: "bg-warn text-warn" },
  offline: { text: "OFFLINE", dot: "bg-bad text-bad" },
  unauthorized: { text: "SIGNED OUT", dot: "bg-bad text-bad" },
};

export function TopBar() {
  const { chatStatus, telemetryStatus } = useAlfred();
  const status =
    [chatStatus, telemetryStatus].find((s) => s !== "online") ?? "online";
  const { text, dot } = LABEL[status] ?? LABEL.offline;
  return (
    <header className="flex h-11 items-center gap-2.5 border-b border-border px-4 font-mono text-[11px]">
      <span className={cn("pulse-dot", dot)} />
      <span className="text-muted-foreground">{text}</span>
      <span className="ml-auto rounded border border-border px-2 py-0.5 text-muted-foreground">⌘K command</span>
    </header>
  );
}
```

- [ ] **Step 5: `AppShell.tsx` + `App.tsx`** — router, auth guard, layout:

```tsx
// AppShell.tsx
import { Outlet } from "react-router-dom";
import { IconRail } from "./IconRail";
import { TopBar } from "./TopBar";
import { Toaster } from "@/components/ui/sonner";

export function AppShell() {
  return (
    <div className="flex h-dvh">
      <IconRail />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="min-h-0 flex-1"><Outlet /></main>
      </div>
      <Toaster position="top-right" />
    </div>
  );
}
```

```tsx
// App.tsx
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api } from "@/lib/api";
import type { AuthStatus } from "@/lib/types";
import { AlfredProvider } from "@/shell/AlfredProvider";
import { AppShell } from "@/shell/AppShell";
import { ChatPage } from "@/chat/ChatPage";
import { ActivityPage } from "@/pages/ActivityPage";
import { HealthPage } from "@/pages/HealthPage";
import { LoginPage } from "@/pages/LoginPage";
import { MemoryPage } from "@/pages/MemoryPage";
import { OnboardingPage } from "@/pages/OnboardingPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { TriggersPage } from "@/pages/TriggersPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } },
});

function Guarded() {
  const { data, isLoading } = useQuery<AuthStatus>({
    queryKey: ["auth-status"],
    queryFn: () => api("/api/auth/status"),
  });
  if (isLoading) return null;
  if (data && !data.registered) return <Navigate to="/onboarding" replace />;
  if (data && !data.authenticated) return <Navigate to="/login" replace />;
  return (
    <AlfredProvider>
      <AppShell />
    </AlfredProvider>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/onboarding" element={<OnboardingPage />} />
          <Route element={<Guarded />}>
            <Route path="/" element={<ChatPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/triggers" element={<TriggersPage />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
```

For this task, create each page as a minimal stub so the build passes (real content lands in Tasks 6-14), e.g. `export function ActivityPage() { return <div className="p-6 font-mono text-muted-foreground">ACTIVITY</div>; }` — stubs are replaced, never shipped: Tasks 6-14 fill every one of them in this plan.

- [ ] **Step 6: Write `IconRail.test.tsx`** — renders all five nav items + settings inside a router with mocked provider; asserts links and active styling. Then run `npm test && npm run build` — PASS.

- [ ] **Step 7: Commit**

```bash
git add web/ && git commit -m "feat(web): cockpit shell — providers, router, auth guard, icon rail, top bar"
```

---

### Task 6: Login page + WebAuthn

**Files:**
- Create: `web/src/lib/webauthn.ts`, `web/src/pages/LoginPage.tsx` (replace stub)
- Test: `web/src/lib/webauthn.test.ts`

**Porting note:** the request/response field names MUST match the old client exactly — retrieve it with `git show master:web/auth.js`. The endpoints are `POST /api/auth/{register,login}/{begin,complete}`; begin responses are py_webauthn `options_to_json` output plus `_challenge_id` (and `_device_name` for registration). Port the field mapping verbatim; the code below is the structure, auth.js is the authority on key names.

- [ ] **Step 1: Write failing tests for the base64url helpers**

```ts
import { describe, expect, it } from "vitest";
import { bufToB64url, b64urlToBuf } from "./webauthn";

describe("base64url helpers", () => {
  it("round-trips bytes", () => {
    const bytes = new Uint8Array([1, 2, 250, 255, 0]);
    const encoded = bufToB64url(bytes.buffer);
    expect(encoded).not.toMatch(/[+/=]/);
    expect(new Uint8Array(b64urlToBuf(encoded))).toEqual(bytes);
  });
});
```

- [ ] **Step 2: Implement `webauthn.ts`**

```ts
import { api, post } from "./api";

export function bufToB64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let str = "";
  for (const b of bytes) str += String.fromCharCode(b);
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlToBuf(value: string): ArrayBuffer {
  const b64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob(padded);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return bytes.buffer;
}

/* Field names below were ported from web/auth.js (git show master:web/auth.js).
   Verify against that file and core/identity/auth_routes.py when implementing. */

export async function registerPasskey(deviceName: string): Promise<void> {
  const options = await post<Record<string, any>>("/api/auth/register/begin", {
    device_name: deviceName,
  });
  const challengeId = options._challenge_id;
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...options,
    challenge: b64urlToBuf(options.challenge),
    user: { ...options.user, id: b64urlToBuf(options.user.id) },
    excludeCredentials: (options.excludeCredentials ?? []).map((c: any) => ({
      ...c, id: b64urlToBuf(c.id),
    })),
  };
  const cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential;
  const response = cred.response as AuthenticatorAttestationResponse;
  await post("/api/auth/register/complete", {
    challenge_id: challengeId,
    device_name: deviceName,
    credential: {
      id: cred.id,
      rawId: bufToB64url(cred.rawId),
      type: cred.type,
      response: {
        clientDataJSON: bufToB64url(response.clientDataJSON),
        attestationObject: bufToB64url(response.attestationObject),
      },
    },
  });
}

export async function loginPasskey(conditional = false): Promise<void> {
  const options = await post<Record<string, any>>("/api/auth/login/begin");
  const challengeId = options._challenge_id;
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...options,
    challenge: b64urlToBuf(options.challenge),
    allowCredentials: (options.allowCredentials ?? []).map((c: any) => ({
      ...c, id: b64urlToBuf(c.id),
    })),
  };
  const cred = (await navigator.credentials.get({
    publicKey,
    ...(conditional ? { mediation: "conditional" as CredentialMediationRequirement } : {}),
  })) as PublicKeyCredential;
  const response = cred.response as AuthenticatorAssertionResponse;
  await post("/api/auth/login/complete", {
    challenge_id: challengeId,
    credential: {
      id: cred.id,
      rawId: bufToB64url(cred.rawId),
      type: cred.type,
      response: {
        clientDataJSON: bufToB64url(response.clientDataJSON),
        authenticatorData: bufToB64url(response.authenticatorData),
        signature: bufToB64url(response.signature),
        userHandle: response.userHandle ? bufToB64url(response.userHandle) : null,
      },
    },
  });
}

export async function logout(): Promise<void> {
  await post("/api/auth/logout");
}
```

- [ ] **Step 3: `LoginPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { loginPasskey } from "@/lib/webauthn";

export function LoginPage() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  const signIn = async (conditional = false) => {
    try {
      await loginPasskey(conditional);
      navigate("/", { replace: true });
    } catch (e) {
      if (!conditional) setError(e instanceof Error ? e.message : "Sign-in failed");
    }
  };

  useEffect(() => {
    // Conditional UI: passkey autofill prompt without a click.
    if (window.PublicKeyCredential?.isConditionalMediationAvailable) {
      void PublicKeyCredential.isConditionalMediationAvailable().then((ok) => {
        if (ok) void signIn(true);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex h-dvh flex-col items-center justify-center gap-6 bg-background">
      <div className="flex size-14 items-center justify-center rounded-2xl border border-secondary font-mono text-2xl text-primary">◆</div>
      <div className="text-center">
        <h1 className="font-mono text-sm tracking-[0.3em] text-foreground">ALFRED</h1>
        <p className="mt-1 text-xs text-muted-foreground">Authentication required</p>
      </div>
      <Button onClick={() => void signIn()} className="font-mono">Sign in with passkey</Button>
      {error && <p className="font-mono text-xs text-bad">{error}</p>}
    </div>
  );
}
```

- [ ] **Step 4: Run `npm test && npm run build`** — PASS. **Commit:**

```bash
git add web/src && git commit -m "feat(web): WebAuthn passkey login with conditional UI"
```

---

### Task 7: Chat page

**Files:**
- Create: `web/src/chat/use-chat.ts`, `web/src/chat/MessageItem.tsx`, `web/src/chat/Composer.tsx`, `web/src/chat/ChatPage.tsx` (replace stub)
- Modify: `core/channels/web_server.py` (forward `actions_taken` + `mood` in the WS response — see Step 1)
- Test: `web/src/chat/MessageItem.test.tsx`, `tests/core/channels/test_web_server.py` (backend addition)

- [ ] **Step 1 (backend): forward the work-shown data.** The `/ws` handler builds the `{"type": "response", ...}` dict from the `AlfredResponse` event it polls off `USER_RESPONSES_STREAM`. `AlfredResponse` already carries `actions_taken: list[str]` and `mood` (`bus/schemas/events.py:104-113`) but the handler currently forwards only `text`/`audio`. Find the response-dict construction in the `/ws` handler in `core/channels/web_server.py` and add:

```python
                "actions_taken": alfred_response.actions_taken,
                "mood": alfred_response.mood,
```

(adapting to the local variable name in that block). Add a backend test in `tests/core/channels/test_web_server.py` asserting the forwarded keys, following that file's existing WS test pattern. This is the only backend change in this plan's scope besides serving (Task 15).

- [ ] **Step 2: `use-chat.ts`** — message state from the chat socket:

```ts
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useAlfred } from "@/shell/AlfredProvider";

export interface ChatMessage {
  role: "user" | "alfred" | "system";
  text: string;
  tools?: string[];
  latencyMs?: number;
  pending?: boolean;
}

export function useChat() {
  const { chat } = useAlfred();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [waiting, setWaiting] = useState(false);
  const sentAt = useRef<number>(0);

  useEffect(() => {
    return chat.listen((msg) => {
      switch (msg.type) {
        case "transcription":
          setMessages((m) => [...m, { role: "user", text: msg.text }]);
          break;
        case "response": {
          const latencyMs = sentAt.current ? Date.now() - sentAt.current : undefined;
          setWaiting(false);
          setMessages((m) => [
            ...m,
            { role: "alfred", text: msg.text, tools: msg.actions_taken, latencyMs },
          ]);
          if (msg.audio) void new Audio(`data:audio/wav;base64,${msg.audio}`).play().catch(() => {});
          break;
        }
        case "notification": {
          const urgent = msg.urgency === "urgent";
          toast(msg.title, { description: msg.body, ...(urgent ? { duration: 10000 } : {}) });
          if (urgent && msg.audio) {
            void new Audio(`data:audio/wav;base64,${msg.audio}`).play().catch(() => {});
          }
          break;
        }
        case "error":
          setWaiting(false);
          setMessages((m) => [...m, { role: "system", text: msg.text }]);
          break;
      }
    });
  }, [chat]);

  const sendText = (text: string) => {
    sentAt.current = Date.now();
    setMessages((m) => [...m, { role: "user", text }]);
    setWaiting(true);
    if (!chat.sendText(text)) {
      setWaiting(false);
      setMessages((m) => [...m, { role: "system", text: "Not connected — message not sent." }]);
    }
  };

  const sendAudio = (dataUrl: string) => {
    sentAt.current = Date.now();
    setWaiting(true);
    if (!chat.sendAudio(dataUrl)) setWaiting(false);
  };

  return { messages, waiting, sendText, sendAudio };
}
```

- [ ] **Step 3: `MessageItem.tsx`** — Alfred speaks with a cyan left border, no bubble; the work-shown line beneath:

```tsx
import { cn } from "@/lib/utils";
import type { ChatMessage } from "./use-chat";

export function MessageItem({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="max-w-[70%] self-end rounded-xl rounded-br-sm bg-secondary px-3.5 py-2 text-sm">
        {message.text}
      </div>
    );
  }
  if (message.role === "system") {
    return <div className="self-center font-mono text-xs text-bad">{message.text}</div>;
  }
  return (
    <div className="max-w-[78%] self-start border-l-2 border-reflex pl-3.5">
      <div className="text-sm leading-relaxed text-foreground/90">{message.text}</div>
      {(message.tools?.length || message.latencyMs) && (
        <div className={cn("mt-1.5 flex flex-wrap gap-3 font-mono text-[10px] text-muted-foreground")}>
          {message.tools?.map((t) => <span key={t}>▸ {t}</span>)}
          {message.latencyMs !== undefined && <span>{(message.latencyMs / 1000).toFixed(1)}s</span>}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: `Composer.tsx`** — textarea + Enter-to-send + voice button slot:

```tsx
import { useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import { VoiceButton } from "./VoiceButton";

export function Composer({ onSend, onAudio }: { onSend: (t: string) => void; onAudio: (d: string) => void }) {
  const [text, setText] = useState("");
  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  };
  return (
    <div className="flex items-end gap-2 border-t border-border p-4">
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
        }}
        placeholder="Message Alfred…"
        rows={1}
        className="max-h-40 min-h-10 resize-none bg-card"
      />
      <VoiceButton onAudio={onAudio} />
    </div>
  );
}
```

(`VoiceButton` arrives in Task 8 — for this task create it as a disabled mic button with the same props so the build passes.)

- [ ] **Step 5: `ChatPage.tsx`** — messages column + telemetry rail mount point (rail in Task 9):

```tsx
import { useEffect, useRef } from "react";
import { useChat } from "./use-chat";
import { Composer } from "./Composer";
import { MessageItem } from "./MessageItem";
import { TelemetryRail } from "@/shell/TelemetryRail";

export function ChatPage() {
  const { messages, waiting, sendText, sendAudio } = useChat();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, waiting]);

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-1 flex-col gap-3.5 overflow-y-auto px-6 py-5">
          {messages.length === 0 && (
            <p className="m-auto font-mono text-xs text-muted-foreground">
              Good evening, sir. How may I be of service?
            </p>
          )}
          {messages.map((m, i) => <MessageItem key={i} message={m} />)}
          {waiting && (
            <div className="flex items-center gap-1.5 self-start border-l-2 border-reflex pl-3.5">
              <span className="pulse-dot bg-reflex text-reflex" />
              <span className="font-mono text-[10px] text-muted-foreground">thinking</span>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
        <Composer onSend={sendText} onAudio={sendAudio} />
      </div>
      <TelemetryRail />
    </div>
  );
}
```

(For this task, stub `TelemetryRail` as `export function TelemetryRail() { return null; }` in `web/src/shell/TelemetryRail.tsx` — Task 9 replaces it.)

- [ ] **Step 6: Write `MessageItem.test.tsx`** — renders user bubble vs Alfred border style; asserts work-line shows `▸ tool` and latency when present, absent otherwise.

- [ ] **Step 7: Run `npm test && npm run build`** and backend `uv run python -m pytest tests/core/channels/ -q` — PASS. **Commit:**

```bash
git add web/ core/channels/web_server.py tests/core/channels/
git commit -m "feat(web): chat page with work-shown responses; forward actions_taken over WS"
```

---

### Task 8: Voice — recorder + level meter

**Files:**
- Replace: `web/src/chat/VoiceButton.tsx`

- [ ] **Step 1: Implement** (MediaRecorder → base64 data URL → existing audio protocol; AnalyserNode level ring while recording):

```tsx
import { useRef, useState } from "react";
import { Mic, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function VoiceButton({ onAudio }: { onAudio: (dataUrl: string) => void }) {
  const [recording, setRecording] = useState(false);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const raf = useRef(0);

  const stop = () => {
    recorder.current?.stop();
    recorder.current?.stream.getTracks().forEach((t) => t.stop());
    cancelAnimationFrame(raf.current);
    setRecording(false);
    setLevel(0);
  };

  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = () => {
        const blob = new Blob(chunks, { type: "audio/webm" });
        const reader = new FileReader();
        reader.onloadend = () => onAudio(reader.result as string);
        reader.readAsDataURL(blob);
      };

      const ctx = new AudioContext();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        const peak = Math.max(...data.map((v) => Math.abs(v - 128))) / 128;
        setLevel(peak);
        raf.current = requestAnimationFrame(tick);
      };
      tick();

      rec.start();
      recorder.current = rec;
      setRecording(true);
      setError(false);
    } catch {
      setError(true);
    }
  };

  return (
    <Button
      variant="outline"
      size="icon"
      title={error ? "Microphone unavailable" : recording ? "Stop recording" : "Record voice message"}
      onClick={() => (recording ? stop() : void start())}
      className={cn("shrink-0", recording && "border-reflex text-reflex", error && "border-bad text-bad")}
      style={recording ? { boxShadow: `0 0 ${4 + level * 16}px var(--reflex)` } : undefined}
    >
      {recording ? <Square className="size-4" /> : <Mic className="size-4" />}
    </Button>
  );
}
```

- [ ] **Step 2: Verify in the running app** — `npm run dev` with the backend running (`uv run python -m runner`): record a message, confirm transcription appears, response plays TTS. (Mic capture can't be unit-tested — this becomes a QA backlog entry in Task 16.)

- [ ] **Step 3: Commit**

```bash
git add web/src && git commit -m "feat(web): voice recording with live level indicator"
```

---

### Task 9: Telemetry rail

**Files:**
- Replace: `web/src/shell/TelemetryRail.tsx`
- Test: `web/src/shell/TelemetryRail.test.tsx`

- [ ] **Step 1: Implement** — vitals from `/api/admin/overview` (refetched when relevant events arrive — event-driven, not interval polling), live ticker from the shared feed, collapsible:

```tsx
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { api } from "@/lib/api";
import { CATEGORY_CLASS, categorize, summarize, timeOf } from "@/lib/format";
import type { Overview } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useAlfred } from "./AlfredProvider";

function Vital({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-md border border-border px-2 py-1.5">
      <div className="text-[8px] tracking-wider text-muted-foreground">{label}</div>
      <div className={cn("font-mono text-sm", tone ?? "text-foreground")}>{value}</div>
    </div>
  );
}

export function TelemetryRail() {
  const { feed, telemetryStatus } = useAlfred();
  const [collapsed, setCollapsed] = useState(false);
  const { data: overview, refetch } = useQuery<Overview>({
    queryKey: ["overview"],
    queryFn: () => api("/api/admin/overview"),
  });

  // Event-driven refresh: new conscious/notification activity can change cost,
  // DND, or session counts — refetch on those, debounced by react-query dedupe.
  const feedHead = feed[0]?.id;
  useEffect(() => {
    if (feedHead) void refetch();
  }, [feedHead, refetch]);

  const perMinute = feed.filter(
    (e) => Date.now() - Number(e.id.split("-")[0]) < 60_000,
  ).length;

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        title="Show telemetry"
        className="border-l border-border bg-panel px-1.5 text-muted-foreground hover:text-foreground"
      >
        <PanelRightOpen className="size-4" />
      </button>
    );
  }

  return (
    <aside className="flex w-60 flex-col border-l border-border bg-panel">
      <div className="flex h-11 items-center justify-between border-b border-border px-3">
        <span className="font-mono text-[9px] tracking-[0.2em] text-muted-foreground">SYSTEM</span>
        <button onClick={() => setCollapsed(true)} title="Hide telemetry" className="text-muted-foreground hover:text-foreground">
          <PanelRightClose className="size-3.5" />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-1.5 p-3">
        <Vital
          label="COST TODAY"
          value={overview?.cost ? `$${overview.cost.spend_usd.toFixed(2)}` : "—"}
          tone={overview?.cost && overview.cost.spend_usd / overview.cost.cap_usd > 0.8 ? "text-warn" : "text-ok"}
        />
        <Vital label="SESSIONS" value={String(overview?.counts.sessions ?? "—")} />
        <Vital label="EVENTS/MIN" value={String(perMinute)} tone="text-reflex" />
        <Vital
          label="DND"
          value={overview?.dnd.active ? "ON" : "OFF"}
          tone={overview?.dnd.active ? "text-warn" : "text-muted-foreground"}
        />
      </div>
      <div className="flex items-center justify-between px-3 pb-1">
        <span className="font-mono text-[9px] tracking-[0.2em] text-muted-foreground">LIVE</span>
        <span className={cn("pulse-dot", telemetryStatus === "online" ? "bg-reflex text-reflex" : "bg-bad text-bad")} />
      </div>
      <div className="flex-1 space-y-2 overflow-hidden px-3 pb-3">
        {feed.slice(0, 14).map((entry, i) => {
          const category = categorize(entry.stream, entry.event);
          return (
            <Link
              key={entry.id + entry.stream}
              to={`/activity#${entry.id}`}
              className="block font-mono text-[9px] leading-snug text-foreground/70 hover:text-foreground"
              style={{ opacity: Math.max(0.25, 1 - i * 0.06) }}
            >
              <span className="text-muted-foreground/60">{timeOf(entry.id)}</span>{" "}
              <span className={CATEGORY_CLASS[category]}>{category}</span>
              <br />
              {summarize(entry.stream, entry.event)}
            </Link>
          );
        })}
        {feed.length === 0 && (
          <p className="font-mono text-[9px] text-muted-foreground">Waiting for activity…</p>
        )}
      </div>
    </aside>
  );
}
```

- [ ] **Step 2: Test** — render with mocked `useAlfred` (a few feed entries) + mocked query; assert ticker lines, categories colored, collapse toggle hides the rail. Run `npm test` — PASS.

- [ ] **Step 3: Commit**

```bash
git add web/src && git commit -m "feat(web): live telemetry rail — vitals + breathing ticker"
```

---

### Task 10: Activity page

**Files:**
- Replace: `web/src/pages/ActivityPage.tsx`
- Create: `web/src/pages/EventInspector.tsx`

- [ ] **Step 1: Implement** — live feed (shared buffer), pause, category + stream filters, history backfill, click → JSON inspector sheet:

```tsx
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { CATEGORY_CLASS, categorize, summarize, timeOf, type SourceCategory } from "@/lib/format";
import type { StreamPage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useAlfred, type FeedEntry } from "@/shell/AlfredProvider";
import { EventInspector } from "./EventInspector";

const STREAMS = [
  "events", "actions", "user_requests", "user_responses",
  "reflex_observations", "notifications", "home_state", "home_action_results",
];

export function ActivityPage() {
  const { feed } = useAlfred();
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState<FeedEntry[]>([]);
  const [stream, setStream] = useState<string | null>(null);
  const [selected, setSelected] = useState<FeedEntry | null>(null);

  const { data: history } = useQuery<StreamPage>({
    queryKey: ["stream-history", stream],
    queryFn: () => api(`/api/admin/streams/${stream}?count=100`),
    enabled: stream !== null,
  });

  const live = paused ? frozen : feed;
  const entries = useMemo(() => {
    if (stream === null) return live;
    const backfill = (history?.entries ?? []).map((e) => ({ stream: stream, ...e }));
    const liveFiltered = live.filter((e) => e.stream === stream);
    const seen = new Set(liveFiltered.map((e) => e.id));
    return [...liveFiltered, ...backfill.filter((e) => !seen.has(e.id))];
  }, [live, stream, history]);

  const togglePause = () => {
    if (!paused) setFrozen(feed);
    setPaused(!paused);
  };

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-1.5 border-b border-border p-3">
          <Button variant="outline" size="sm" onClick={togglePause} className="font-mono text-xs">
            {paused ? <Play className="size-3" /> : <Pause className="size-3" />}
            {paused ? "RESUME" : "PAUSE"}
          </Button>
          <Badge
            variant={stream === null ? "default" : "outline"}
            className="cursor-pointer font-mono text-[10px]"
            onClick={() => setStream(null)}
          >
            all
          </Badge>
          {STREAMS.map((s) => (
            <Badge
              key={s}
              variant={stream === s ? "default" : "outline"}
              className="cursor-pointer font-mono text-[10px]"
              onClick={() => setStream(s)}
            >
              {s}
            </Badge>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto font-mono text-xs">
          {entries.map((entry) => {
            const category: SourceCategory = categorize(entry.stream, entry.event);
            return (
              <button
                key={`${entry.stream}-${entry.id}`}
                onClick={() => setSelected(entry)}
                className="flex w-full items-baseline gap-3 border-b border-border/40 px-4 py-2 text-left hover:bg-card"
              >
                <span className="text-muted-foreground/60">{timeOf(entry.id)}</span>
                <span className={cn("w-20 shrink-0", CATEGORY_CLASS[category])}>{category}</span>
                <span className="w-36 shrink-0 truncate text-muted-foreground">{entry.stream}</span>
                <span className="truncate text-foreground/80">{summarize(entry.stream, entry.event)}</span>
              </button>
            );
          })}
          {entries.length === 0 && (
            <p className="p-6 text-muted-foreground">No activity yet — the system is quiet.</p>
          )}
        </div>
      </div>
      <EventInspector entry={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
```

`EventInspector.tsx`:

```tsx
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { timeOf } from "@/lib/format";
import type { FeedEntry } from "@/shell/AlfredProvider";

export function EventInspector({ entry, onClose }: { entry: FeedEntry | null; onClose: () => void }) {
  return (
    <Sheet open={entry !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-[480px] overflow-y-auto bg-panel font-mono sm:max-w-[480px]">
        {entry && (
          <>
            <SheetHeader>
              <SheetTitle className="font-mono text-sm">
                {entry.stream} · {timeOf(entry.id)}
              </SheetTitle>
            </SheetHeader>
            <pre className="mt-2 overflow-x-auto rounded-md border border-border bg-background p-4 text-[11px] leading-relaxed text-foreground/90">
              {JSON.stringify(entry.event, null, 2)}
            </pre>
            <p className="mt-2 px-1 text-[10px] text-muted-foreground">id: {entry.id}</p>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
```

- [ ] **Step 2: Verify** — `npm run build && npm test`; manual check in dev with the runner producing events. **Commit:**

```bash
git add web/src && git commit -m "feat(web): activity page — live feed, filters, pause, JSON inspector"
```

---

### Task 11: Memory page

**Files:**
- Replace: `web/src/pages/MemoryPage.tsx`

- [ ] **Step 1: Implement** — four tabs against the Task 5 (Step 1 plan) endpoints:

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Markdown from "react-markdown";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { EpisodicEntry, Routine, SemanticFile } from "@/lib/types";

function Episodic() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const { data, isFetching } = useQuery<{ entries: EpisodicEntry[] }>({
    queryKey: ["episodic", query],
    queryFn: () => api(`/api/admin/memory/episodic${query ? `?q=${encodeURIComponent(query)}` : ""}`),
  });
  return (
    <div className="space-y-3">
      <Input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && setQuery(q)}
        placeholder="Semantic search (Enter) — empty shows recent"
        className="max-w-md bg-card font-mono text-xs"
      />
      {isFetching && <p className="font-mono text-xs text-muted-foreground">searching…</p>}
      <div className="space-y-2">
        {(data?.entries ?? []).map((e, i) => (
          <div key={i} className="rounded-md border border-border bg-card p-3">
            <div className="flex items-center gap-2 font-mono text-[10px]">
              <Badge variant="outline" className={e.store === "hot" ? "text-reflex" : "text-home"}>
                {e.store}
              </Badge>
              <span className="text-memory">
                sig {Number((e.significance as never) ?? e.score ?? 0).toFixed?.(2) ?? "—"}
              </span>
              <span className="text-muted-foreground">{String(e.source ?? "")}</span>
            </div>
            <p className="mt-1.5 text-sm text-foreground/90">
              {String(e.content ?? e.summary ?? "")}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function Semantic() {
  const { data } = useQuery<{ files: SemanticFile[] }>({
    queryKey: ["semantic"],
    queryFn: () => api("/api/admin/memory/semantic"),
  });
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {(data?.files ?? []).map((f) => (
        <div key={`${f.dir}/${f.name}`} className="rounded-md border border-border bg-card p-4">
          <div className="mb-2 font-mono text-[10px] text-muted-foreground">
            {f.dir}/{f.name}
          </div>
          <div className="prose prose-invert prose-sm max-w-none text-foreground/90">
            <Markdown>{f.content.replace(/^---[\s\S]*?---/, "")}</Markdown>
          </div>
        </div>
      ))}
    </div>
  );
}

const ROUTINE_TONE: Record<Routine["state"], string> = {
  active: "text-ok", candidate: "text-warn", dormant: "text-muted-foreground", archived: "text-bad",
};

function Routines() {
  const { data } = useQuery<{ routines: Routine[] }>({
    queryKey: ["routines"],
    queryFn: () => api("/api/admin/memory/routines"),
  });
  return (
    <div className="space-y-2">
      {(data?.routines ?? []).map((r) => (
        <div key={r.name} className="rounded-md border border-border bg-card p-3 font-mono text-xs">
          <div className="flex items-center gap-2">
            <span className="text-sm text-foreground">{r.name}</span>
            <Badge variant="outline" className={ROUTINE_TONE[r.state]}>{r.state}</Badge>
            <span className="text-memory">conf {r.confidence.toFixed(2)}</span>
            <span className="ml-auto text-muted-foreground">{r.trigger_pattern}</span>
          </div>
          <ol className="mt-2 list-decimal pl-5 text-muted-foreground">
            {r.steps.map((s, i) => <li key={i}>{s.description}</li>)}
          </ol>
        </div>
      ))}
      {data?.routines.length === 0 && (
        <p className="font-mono text-xs text-muted-foreground">No routines learned yet.</p>
      )}
    </div>
  );
}

function Scratchpad() {
  const { data } = useQuery<{ content: string; pending_queue: number }>({
    queryKey: ["scratchpad"],
    queryFn: () => api("/api/admin/memory/scratchpad"),
  });
  return (
    <div>
      <p className="mb-2 font-mono text-[10px] text-muted-foreground">
        {data?.pending_queue ?? 0} observations queued for drain
      </p>
      <pre className="overflow-x-auto rounded-md border border-border bg-card p-4 font-mono text-[11px] leading-relaxed text-foreground/80">
        {data?.content || "Scratchpad is empty."}
      </pre>
    </div>
  );
}

export function MemoryPage() {
  return (
    <div className="h-full overflow-y-auto p-5">
      <Tabs defaultValue="episodic">
        <TabsList className="font-mono text-xs">
          <TabsTrigger value="episodic">EPISODIC</TabsTrigger>
          <TabsTrigger value="semantic">SEMANTIC</TabsTrigger>
          <TabsTrigger value="routines">ROUTINES</TabsTrigger>
          <TabsTrigger value="scratchpad">SCRATCHPAD</TabsTrigger>
        </TabsList>
        <TabsContent value="episodic" className="mt-4"><Episodic /></TabsContent>
        <TabsContent value="semantic" className="mt-4"><Semantic /></TabsContent>
        <TabsContent value="routines" className="mt-4"><Routines /></TabsContent>
        <TabsContent value="scratchpad" className="mt-4"><Scratchpad /></TabsContent>
      </Tabs>
    </div>
  );
}
```

(If `prose` classes are unavailable, render Markdown inside a plain styled div — do NOT add @tailwindcss/typography unless already simple to include.)

- [ ] **Step 2: Verify build + manual check. Commit:**

```bash
git add web/src && git commit -m "feat(web): memory browser — episodic search, semantic files, routines, scratchpad"
```

---

### Task 12: Triggers page

**Files:**
- Replace: `web/src/pages/TriggersPage.tsx`

- [ ] **Step 1: Implement** — triggers table with enable switch + fire; DND card; deferred queue with drain. Mutations invalidate queries and toast results:

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { api, post } from "@/lib/api";
import type { DeferredNotification, Overview, Trigger } from "@/lib/types";

export function TriggersPage() {
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["triggers"] });
    void qc.invalidateQueries({ queryKey: ["overview"] });
    void qc.invalidateQueries({ queryKey: ["deferred"] });
  };

  const { data: triggers } = useQuery<{ triggers: Trigger[] }>({
    queryKey: ["triggers"], queryFn: () => api("/api/admin/triggers"),
  });
  const { data: overview } = useQuery<Overview>({
    queryKey: ["overview"], queryFn: () => api("/api/admin/overview"),
  });
  const { data: deferred } = useQuery<{ notifications: DeferredNotification[] }>({
    queryKey: ["deferred"], queryFn: () => api("/api/admin/notifications/deferred"),
  });

  const setEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      post(`/api/admin/triggers/${id}/enabled`, { enabled }),
    onSuccess: () => { toast("Trigger updated — effective within 60s"); invalidate(); },
    onError: (e) => toast.error(String(e)),
  });
  const fire = useMutation({
    mutationFn: (id: string) => post(`/api/admin/triggers/${id}/fire`),
    onSuccess: () => { toast("Trigger fired"); invalidate(); },
    onError: (e) => toast.error(String(e)),
  });
  const setDnd = useMutation({
    mutationFn: (active: boolean) => post("/api/admin/dnd", { active }),
    onSuccess: () => { toast("DND updated"); invalidate(); },
  });
  const drain = useMutation({
    mutationFn: () => post("/api/admin/notifications/drain"),
    onSuccess: () => { toast("Drain queued"); invalidate(); },
  });

  return (
    <div className="grid h-full gap-4 overflow-y-auto p-5 lg:grid-cols-3">
      <Card className="bg-card lg:col-span-2">
        <CardHeader><CardTitle className="font-mono text-xs tracking-widest">TRIGGERS</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {(triggers?.triggers ?? []).map((t) => (
            <div key={t.trigger_id} className="flex items-center gap-3 rounded-md border border-border p-3 font-mono text-xs">
              <Switch
                checked={t.enabled}
                onCheckedChange={(enabled) => setEnabled.mutate({ id: t.trigger_id, enabled })}
              />
              <div className="min-w-0">
                <div className="text-sm text-foreground">{t.name}</div>
                <div className="text-muted-foreground">
                  {t.trigger_type} · by {t.created_by ?? "?"} · last fired {t.last_fired ?? "never"}
                </div>
              </div>
              <Badge variant="outline" className="ml-auto text-trigger">{t.trigger_type}</Badge>
              {t.one_shot && <Badge variant="outline" className="text-warn">one-shot</Badge>}
              <Button size="sm" variant="outline" className="font-mono text-[10px]"
                onClick={() => fire.mutate(t.trigger_id)}>
                FIRE
              </Button>
            </div>
          ))}
          {triggers?.triggers.length === 0 && (
            <p className="font-mono text-xs text-muted-foreground">No active triggers.</p>
          )}
        </CardContent>
      </Card>

      <div className="space-y-4">
        <Card className="bg-card">
          <CardHeader><CardTitle className="font-mono text-xs tracking-widest">DO NOT DISTURB</CardTitle></CardHeader>
          <CardContent className="flex items-center gap-3 font-mono text-xs">
            <Switch
              checked={overview?.dnd.active ?? false}
              onCheckedChange={(active) => setDnd.mutate(active)}
            />
            <span className={overview?.dnd.active ? "text-warn" : "text-muted-foreground"}>
              {overview?.dnd.active ? `ON${overview.dnd.reason ? ` — ${overview.dnd.reason}` : ""}` : "OFF"}
            </span>
          </CardContent>
        </Card>

        <Card className="bg-card">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="font-mono text-xs tracking-widest">DEFERRED</CardTitle>
            <Button size="sm" variant="outline" className="font-mono text-[10px]"
              disabled={(deferred?.notifications.length ?? 0) === 0}
              onClick={() => drain.mutate()}>
              DRAIN NOW
            </Button>
          </CardHeader>
          <CardContent className="space-y-2">
            {(deferred?.notifications ?? []).map((n) => (
              <div key={n.notification_id} className="rounded-md border border-border p-2.5">
                <div className="font-mono text-[10px] text-trigger">{n.urgency}</div>
                <div className="text-sm">{n.title}</div>
                <div className="text-xs text-muted-foreground">{n.body}</div>
              </div>
            ))}
            {deferred?.notifications.length === 0 && (
              <p className="font-mono text-xs text-muted-foreground">Queue empty.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add the recent-notification-history card** (spec: triggers page shows notification history). Below the DEFERRED card in the right column, add a HISTORY card backed by the dispatch stream:

```tsx
// additional query inside TriggersPage:
const { data: history } = useQuery<{ entries: { id: string; event: Record<string, unknown> }[] }>({
  queryKey: ["notification-history"],
  queryFn: () => api("/api/admin/streams/notifications?count=20"),
});
```

```tsx
<Card className="bg-card">
  <CardHeader><CardTitle className="font-mono text-xs tracking-widest">HISTORY</CardTitle></CardHeader>
  <CardContent className="space-y-2">
    {(history?.entries ?? []).map((e) => (
      <div key={e.id} className="rounded-md border border-border p-2.5">
        <div className="font-mono text-[10px] text-muted-foreground">
          {timeOf(e.id)} · {String(e.event.urgency ?? "")}
        </div>
        <div className="text-sm">{String(e.event.title ?? "")}</div>
        <div className="text-xs text-muted-foreground">{String(e.event.body ?? "")}</div>
      </div>
    ))}
    {history?.entries.length === 0 && (
      <p className="font-mono text-xs text-muted-foreground">No notifications yet.</p>
    )}
  </CardContent>
</Card>
```

(Import `timeOf` from `@/lib/format`.)

- [ ] **Step 3: Verify build + manual check. Commit:**

```bash
git add web/src && git commit -m "feat(web): triggers page — enable/fire controls, DND, deferred drain, history"
```

---

### Task 13: Health page

**Files:**
- Replace: `web/src/pages/HealthPage.tsx`

- [ ] **Step 1: Implement** — panels fail independently (each backed by its own query): connectivity (Redis/Ollama/LM Studio), cost progress vs cap, stream recency table, integrations with live health checks (existing `/api/integrations` + `/api/integrations/{name}/status`), sessions with END control, devices:

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { api, del } from "@/lib/api";
import type { DeviceInfo, IntegrationInfo, Overview, SessionInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

function Dot({ ok }: { ok: boolean }) {
  return <span className={cn("pulse-dot", ok ? "bg-ok text-ok" : "bg-bad text-bad")} />;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="bg-card">
      <CardHeader><CardTitle className="font-mono text-xs tracking-widest">{title}</CardTitle></CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function IntegrationRow({ integration }: { integration: IntegrationInfo }) {
  const { data, isLoading } = useQuery<{ healthy: boolean }>({
    queryKey: ["integration-status", integration.name],
    queryFn: () => api(`/api/integrations/${integration.name}/status`),
  });
  return (
    <div className="flex items-center gap-2 font-mono text-xs">
      <Dot ok={data?.healthy ?? false} />
      <span>{integration.name}</span>
      <span className="text-muted-foreground">{integration.category}</span>
      {isLoading && <span className="ml-auto text-muted-foreground">checking…</span>}
    </div>
  );
}

export function HealthPage() {
  const qc = useQueryClient();
  const { data: overview, error: overviewError } = useQuery<Overview>({
    queryKey: ["overview"], queryFn: () => api("/api/admin/overview"),
  });
  const { data: sessions } = useQuery<{ sessions: SessionInfo[] }>({
    queryKey: ["sessions"], queryFn: () => api("/api/admin/sessions"),
  });
  const { data: devices } = useQuery<{ devices: DeviceInfo[] }>({
    queryKey: ["devices"], queryFn: () => api("/api/admin/devices"),
  });
  const { data: integrations } = useQuery<IntegrationInfo[]>({
    queryKey: ["integrations"], queryFn: () => api("/api/integrations"),
  });
  const endSession = useMutation({
    mutationFn: (id: string) => del(`/api/admin/sessions/${id}`),
    onSuccess: () => { toast("Session ended"); void qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });

  const spendRatio = overview?.cost ? overview.cost.spend_usd / overview.cost.cap_usd : 0;

  return (
    <div className="grid h-full content-start gap-4 overflow-y-auto p-5 md:grid-cols-2 xl:grid-cols-3">
      <Panel title="CONNECTIVITY">
        {overviewError ? (
          <p className="font-mono text-xs text-bad">overview unavailable: {String(overviewError)}</p>
        ) : (
          <div className="space-y-2 font-mono text-xs">
            <div className="flex items-center gap-2"><Dot ok={overview?.redis.connected ?? false} /> redis</div>
            <div className="flex items-center gap-2"><Dot ok={overview?.inference.ollama ?? false} /> ollama</div>
            <div className="flex items-center gap-2"><Dot ok={overview?.inference.lmstudio ?? false} /> lm studio</div>
          </div>
        )}
      </Panel>

      <Panel title="COST TODAY">
        <div className="font-mono">
          <div className={cn("text-2xl", spendRatio > 0.8 ? "text-warn" : "text-ok")}>
            ${overview?.cost?.spend_usd.toFixed(2) ?? "0.00"}
            <span className="text-xs text-muted-foreground"> / ${overview?.cost?.cap_usd.toFixed(2) ?? "—"}</span>
          </div>
          <Progress value={Math.min(spendRatio * 100, 100)} className="mt-3" />
        </div>
      </Panel>

      <Panel title="INTEGRATIONS">
        <div className="space-y-2">
          {(integrations ?? []).map((i) => <IntegrationRow key={i.name} integration={i} />)}
        </div>
      </Panel>

      <Panel title="STREAMS">
        <table className="w-full font-mono text-xs">
          <tbody>
            {Object.entries(overview?.streams ?? {}).map(([name, s]) => (
              <tr key={name} className="border-b border-border/40">
                <td className="py-1.5 text-muted-foreground">{name}</td>
                <td className="text-right">{s.length}</td>
                <td className="pl-3 text-right text-muted-foreground">
                  {s.last_ts ? new Date(s.last_ts * 1000).toLocaleTimeString("en-GB") : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <Panel title="SESSIONS">
        <div className="space-y-2">
          {(sessions?.sessions ?? []).map((s) => (
            <div key={s.session_id} className="flex items-center gap-2 font-mono text-xs">
              <Badge variant="outline">{s.channel}</Badge>
              <span className="truncate text-muted-foreground">{s.session_id.slice(0, 8)}</span>
              <span>{s.turns} turns</span>
              <span className="text-muted-foreground">{Math.max(0, Math.round(s.ttl_seconds / 60))}m left</span>
              <Button size="sm" variant="outline" className="ml-auto font-mono text-[10px]"
                onClick={() => endSession.mutate(s.session_id)}>
                END
              </Button>
            </div>
          ))}
          {sessions?.sessions.length === 0 && (
            <p className="font-mono text-xs text-muted-foreground">No active sessions.</p>
          )}
        </div>
      </Panel>

      <Panel title="DEVICES">
        <div className="space-y-2">
          {(devices?.devices ?? []).map((d) => (
            <div key={d.device_token} className="font-mono text-xs">
              <Badge variant="outline">{d.platform ?? "?"}</Badge>{" "}
              <span className="text-muted-foreground">{d.device_token.slice(0, 12)}…</span>
              <span className="ml-2 text-muted-foreground">{d.identity}</span>
            </div>
          ))}
          {devices?.devices.length === 0 && (
            <p className="font-mono text-xs text-muted-foreground">No registered devices.</p>
          )}
        </div>
      </Panel>
    </div>
  );
}
```

- [ ] **Step 2: Verify build + manual check. Commit:**

```bash
git add web/src && git commit -m "feat(web): health dashboard — independent failure-isolated panels"
```

---

### Task 14: Settings + Onboarding

**Files:**
- Replace: `web/src/pages/SettingsPage.tsx`, `web/src/pages/OnboardingPage.tsx`

**Porting note:** the integration credential PUT body and the onboarding POST body must match the backend exactly: `PUT /api/integrations/{name}/credentials` (see the endpoint at `core/channels/web_server.py` ~line 434 — port the body shape from `git show master:web/settings.js`), and `POST /api/onboarding` takes `OnboardingPayload` (`core/channels/web_server.py:119-126`): `{ wake_time, work_address, dietary_restrictions, proactivity_level, guest_controls: string[] }` — all optional.

- [ ] **Step 1: `SettingsPage.tsx`** — one card per integration from `GET /api/integrations`; fields rendered from `schema.fields` (`label`, `field_type`, `placeholder`, `default`, `help_text`, `required`, `transient` + `configured[field]`); save (PUT), clear (DELETE), test (GET status); logout button calling `logout()` then navigate `/login`. Render password fields with a visibility toggle. Complete component following the established Card/Input/Button patterns from Tasks 12-13 — state is one `Record<string, string>` per integration card.

- [ ] **Step 2: `OnboardingPage.tsx`** — 6 steps in one component with a step index, Mission Control styled:
  1. **Passkey** — device-name input → `registerPasskey(deviceName)` (skip button if `auth-status.registered`).
  2. **Personal** — `wake_time` (time input), `work_address`, `dietary_restrictions`.
  3. **Proactivity** — radio cards: opinionated / moderate / conservative.
  4. **Guest mode** — checkboxes appended to `guest_controls` (values: `lighting`, `media`, `climate`, `door_locks` — verify against `git show master:web/index.html` step 3 before coding).
  5. **Integrations** — reuse the Settings card form per integration; all skippable.
  6. **Done** — POST the collected `OnboardingPayload` to `/api/onboarding`, then navigate `/`.

  Progress shown as a monospace `STEP n/6` header with a `Progress` bar. Each step has Back/Continue; Continue on step 6 submits.

- [ ] **Step 3: Verify** — `npm run build`; manual run-through of both pages in dev against the live backend. **Commit:**

```bash
git add web/src && git commit -m "feat(web): settings + onboarding wizard in shadcn"
```

---

### Task 15: Command palette + SPA serving + container

**Files:**
- Create: `web/src/shell/CommandPalette.tsx`, `core/channels/spa.py`
- Modify: `web/src/shell/AppShell.tsx`, `core/channels/web_server.py` (replace static mount), `Containerfile`
- Test: `tests/core/channels/test_spa.py`

- [ ] **Step 1: `CommandPalette.tsx`** — ⌘K dialog: navigation + controls:

```tsx
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command";
import { post } from "@/lib/api";

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const qc = useQueryClient();

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, []);

  const run = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => post(path, body),
    onSuccess: (_d, v) => { toast(`Done: ${v.path}`); void qc.invalidateQueries(); },
    onError: (e) => toast.error(String(e)),
  });

  const go = (to: string) => { navigate(to); setOpen(false); };
  const act = (path: string, body?: unknown) => { run.mutate({ path, body }); setOpen(false); };

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Command or destination…" className="font-mono" />
      <CommandList className="font-mono text-xs">
        <CommandEmpty>Nothing matches.</CommandEmpty>
        <CommandGroup heading="Go to">
          <CommandItem onSelect={() => go("/")}>Chat</CommandItem>
          <CommandItem onSelect={() => go("/activity")}>Activity</CommandItem>
          <CommandItem onSelect={() => go("/memory")}>Memory</CommandItem>
          <CommandItem onSelect={() => go("/triggers")}>Triggers</CommandItem>
          <CommandItem onSelect={() => go("/health")}>Health</CommandItem>
          <CommandItem onSelect={() => go("/settings")}>Settings</CommandItem>
        </CommandGroup>
        <CommandGroup heading="Controls">
          <CommandItem onSelect={() => act("/api/admin/dnd", { active: true })}>DND on</CommandItem>
          <CommandItem onSelect={() => act("/api/admin/dnd", { active: false })}>DND off</CommandItem>
          <CommandItem onSelect={() => act("/api/admin/notifications/drain")}>Drain deferred notifications</CommandItem>
          <CommandItem onSelect={() => act("/api/admin/librarian/run")}>Run Librarian now</CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
```

Mount `<CommandPalette />` inside `AppShell` (sibling of `Toaster`).

- [ ] **Step 2: SPA serving — write the failing backend test** (`tests/core/channels/test_spa.py`):

```python
"""SPA fallback serving."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.channels.spa import mount_spa


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>alfred</html>")
    (dist / "assets" / "app.js").write_text("// js")
    return dist


def test_serves_static_files(tmp_path: Path) -> None:
    app = FastAPI()
    mount_spa(app, _dist(tmp_path))
    client = TestClient(app)
    assert client.get("/assets/app.js").status_code == 200
    assert "alfred" in client.get("/").text


def test_client_routes_fall_back_to_index(tmp_path: Path) -> None:
    app = FastAPI()
    mount_spa(app, _dist(tmp_path))
    client = TestClient(app)
    resp = client.get("/activity")
    assert resp.status_code == 200
    assert "alfred" in resp.text


def test_missing_dist_is_noop(tmp_path: Path) -> None:
    app = FastAPI()
    mount_spa(app, tmp_path / "nope")
    client = TestClient(app)
    assert client.get("/").status_code == 404
```

- [ ] **Step 3: Implement `core/channels/spa.py`**

```python
"""SPA serving — static assets + index.html fallback for client-side routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve a built SPA: real files when they exist, index.html otherwise."""
    if not dist.is_dir():
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        candidate = dist / full_path
        if full_path and ".." not in full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")
```

In `core/channels/web_server.py`, replace the old static mount block:

```python
# OLD (delete):
web_dir = Path(__file__).resolve().parent.parent.parent / "web"
if web_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="static")

# NEW:
from core.channels.spa import mount_spa

mount_spa(app, Path(__file__).resolve().parent.parent.parent / "web" / "dist")
```

(Keep `NoCacheStaticMiddleware` — index.html must never be cached; hashed `/assets/*` immutability is a backlog nicety. Do NOT add `web/dist` to the runner's `watch_dirs` — files are read per-request, no restart needed.)

- [ ] **Step 4: Containerfile — add the web build stage** (top of file) and copy the dist (before `ENV PYTHONPATH`):

```dockerfile
FROM node:22-slim AS webbuild
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build
```

```dockerfile
COPY --from=webbuild /web/dist /app/web/dist
```

- [ ] **Step 5: Run everything**

```bash
uv run python -m pytest tests/core/channels/ -q     # backend incl. test_spa.py
cd web && npm run build && npm test && cd ..
uv run ruff check . --fix && uv run mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
```
Then manual: `uv run python -m runner`, open `http://localhost:8081/` — built SPA serves; `/activity` deep-link works.

- [ ] **Step 6: Commit**

```bash
git add web/src core/channels/ tests/core/channels/test_spa.py Containerfile
git commit -m "feat(web): command palette, SPA fallback serving, container web build stage"
```

---

### Task 16: Docs, QA backlog, final gate

**Files:**
- Create: `docs/web-frontend.md`, `docs/qa-backlog/*.md` (5 files below)
- Modify: `docs/architecture.md`, `CLAUDE.md` (web/ description), `alfred/docs/backlog/` (deferred items)

- [ ] **Step 1: `docs/web-frontend.md`** — stack, directory map, design tokens (the source-color language), WS protocols consumed, dev workflow (`npm run dev` + proxy), build/serve/container story, mermaid component diagram. Update `docs/architecture.md` Web PWA node; update root `CLAUDE.md` Key Paths entry for `web/` (now Vite SPA, `npm run dev|build|test`).

- [ ] **Step 2: QA backlog entries** (per the repo QA convention, one file each):
- `docs/qa-backlog/web-voice-roundtrip.md` — mic record → transcription → TTS playback (critical/functional)
- `docs/qa-backlog/web-passkey-flows.md` — register on trusted network, conditional UI login, logout, WS 4001 redirect (critical/functional)
- `docs/qa-backlog/web-live-telemetry.md` — multi-process events appear in rail + activity within 2s; reconnect after killing channels process (high/integration)
- `docs/qa-backlog/web-onboarding-e2e.md` — fresh state → wizard → memory files written, skip-defaults respected (high/e2e)
- `docs/qa-backlog/web-admin-controls.md` — DND toggle affects notification deferral; drain delivers; trigger fire executes action; librarian run logs consolidation (high/integration)

- [ ] **Step 3: Backlog tickets** for deferred niceties: `docs/backlog/low/web-asset-cache-headers.md`, `docs/backlog/low/web-light-theme.md`, `docs/backlog/medium/web-activity-virtualized-list.md` (feed perf beyond 500 entries).

- [ ] **Step 4: Final quality gate**

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
uv run python -m pytest -q
cd web && npm run lint && npm test && npm run build
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add docs/ CLAUDE.md
git commit -m "docs(web): frontend documentation, QA backlog, deferred tickets"
```

---

## Self-review checklist (run after all tasks)

1. **Spec coverage:** Cockpit (T5/7/9) ✓ work-shown line (T7) ✓ source-color language (T2, used T9/10/11/12/13) ✓ all 8 routes (T5-14) ✓ controls wired to UI (T12/13/15) ✓ ⌘K (T15) ✓ full replacement + serving + container (T1/15) ✓ QA backlog (T16) ✓.
2. **Type consistency:** `FeedEntry`/`TelemetryMessage`/`ChatServerMessage` definitions (T3/T5) match every consumer; `CATEGORY_CLASS` keys = `SourceCategory` union.
3. **No stub survives:** every Task 5 placeholder page is replaced by Tasks 7-14; `VoiceButton` (T7 stub) replaced in T8; `TelemetryRail` (T7 stub) replaced in T9.
4. **Old app fully gone:** `git ls-files web/` shows only the new SPA; `git show master:web/auth.js` remains the porting reference.
