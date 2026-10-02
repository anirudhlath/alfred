import { authEvents } from "./auth-events";

export class ApiError extends Error {
  status: number;
  /** The FastAPI `detail` string, or the raw body when it was not JSON. */
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/**
 * One line for any failed request: the server's `detail` when it sent one (an
 * ApiError's message), the error's own message otherwise.
 */
export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

/**
 * Whether a request failed because its deadline ran out (`AbortSignal.timeout`).
 * By name, not `instanceof DOMException`: the reason is a DOMException from the
 * realm that made the signal, which need not be the page's.
 */
export function timedOut(error: unknown): boolean {
  return (
    typeof error === "object" && error !== null && "name" in error && error.name === "TimeoutError"
  );
}

async function readDetail(resp: Response): Promise<string> {
  const text = await resp.text();
  try {
    const parsed: unknown = JSON.parse(text);
    if (
      parsed &&
      typeof parsed === "object" &&
      "detail" in parsed &&
      typeof (parsed as { detail: unknown }).detail === "string"
    ) {
      return (parsed as { detail: string }).detail;
    }
  } catch {
    // Not JSON — the raw text is the most honest thing we have.
  }
  return text;
}

/**
 * A rejected passkey assertion is a 401 too — that attempt failing, not a
 * session lapsing. The gate that asked shows it; no Expired gate over it.
 */
const isPasskeyAttempt = (path: string): boolean =>
  path.startsWith("/api/auth/login/") || path.startsWith("/api/auth/register/");

/** The house's answer to "who is this?" — read by `fetchAuthStatus` (./auth.ts). */
export const AUTH_STATUS_PATH = "/api/auth/status";

/**
 * The status read is the one request the house's network gate never refuses:
 * its route has none (`auth_status`, core/identity/auth_routes.py, on a router
 * with no dependencies). So a 403 there came from something in front of the
 * house — a proxy, a WAF, a Cloudflare challenge — and the denied gate's "not
 * from here" would be a claim nobody made. Unannounced, it is a failed read
 * like any other: `AuthGate`, the only listener, shows the unanswered gate
 * while nothing has been read, and keeps the last answer once something has.
 * If the route ever gains a network gate, its 403 becomes the house's answer:
 * revisit this then.
 */
const isStatusRead = (path: string): boolean => path === AUTH_STATUS_PATH;

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!resp.ok) {
    const detail = await readDetail(resp);
    // Announce, never navigate: the gate rises over whatever is on screen, so the
    // last-known state stays visible behind it (spec §5.2, "live is not last-known").
    if (resp.status === 401 && !isPasskeyAttempt(path)) authEvents.emit("expired");
    if (resp.status === 403 && !isStatusRead(path)) authEvents.emit("denied");
    throw new ApiError(resp.status, detail || resp.statusText);
  }

  // 204 has no body; `await resp.json()` would throw on it.
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const post = <T>(path: string, body?: unknown): Promise<T> =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const put = <T>(path: string, body?: unknown): Promise<T> =>
  api<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) });
