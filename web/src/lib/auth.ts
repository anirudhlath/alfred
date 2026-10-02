import { api, errorText } from "./api";
import { dayMonth } from "./format";
import type { AuthStatus } from "./types";

export const DEVICE_KEY = "alfred.device";

export interface RememberedDevice {
  name: string;
  /** ISO 8601, from the moment registration completed. */
  registeredAt: string;
}

/**
 * How long the auth read waits for the house: the worker's `TIMEOUT_MS`
 * (public/sw.js), the time it gives a navigation before it serves the cached
 * shell. The page and the worker agree on when the house is not answering.
 * Shorter would call a slow but working link silent; longer only keeps a launch
 * that will get no answer on the bare field.
 */
export const STATUS_DEADLINE_MS = 10_000;

/** A signal that aborts when either does: `AbortSignal.any`, where there is one. */
function either(a: AbortSignal, b: AbortSignal): AbortSignal {
  // Safari has it from 17.4 only.
  if (typeof AbortSignal.any === "function") return AbortSignal.any([a, b]);
  const both = new AbortController();
  for (const source of [a, b]) {
    if (source.aborted) {
      both.abort(source.reason);
      return both.signal;
    }
  }
  for (const source of [a, b]) {
    // Each listener goes once `both` has aborted, whichever source did it.
    source.addEventListener("abort", () => both.abort(source.reason), {
      once: true,
      signal: both.signal,
    });
  }
  return both.signal;
}

/** What `auth_status` (core/identity/auth_routes.py) sends with every 200. */
function isAuthStatus(value: unknown): value is AuthStatus {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as AuthStatus).registered === "boolean" &&
    typeof (value as AuthStatus).authenticated === "boolean"
  );
}

/**
 * The house's answer to "who is this?", or a rejection. `signal` is
 * react-query's, and the read also has a deadline of its own. A 200 that is not
 * the house's status (a captive portal's page, a proxy's JSON) is a rejection
 * too: only the house's answer may route to setup or sign-in.
 */
export async function fetchAuthStatus(signal: AbortSignal): Promise<AuthStatus> {
  const deadline = AbortSignal.timeout(STATUS_DEADLINE_MS);
  let status: unknown;
  try {
    status = await api<unknown>("/api/auth/status", { signal: either(signal, deadline) });
  } catch (error) {
    // Name the deadline, whatever fetch rejected with: an engine without abort
    // reasons rejects with a plain AbortError, and the retry policy and the
    // gate tell a deadline from a cancel by this.
    throw deadline.aborted ? deadline.reason : error;
  }
  if (!isAuthStatus(status)) throw new Error("The reply was not the house's status.");
  return status;
}

/**
 * A first guess at what to call this passkey. The user never sees a prompt for
 * it in Phase 1, so it has to be right often and harmless when wrong.
 *
 * iOS before Mac, because every iOS user agent carries "like Mac OS X" — a real
 * iPad would be named "Mac" otherwise. An iPad in desktop mode reports plain
 * "Macintosh" and no ordering can catch it; that is accepted, since an iPad that
 * calls itself a Mac is a better failure than a Mac that calls itself an iPad.
 */
export function defaultDeviceName(ua: string = navigator.userAgent): string {
  if (/iPhone/i.test(ua)) return "iPhone";
  if (/iPad/i.test(ua)) return "iPad";
  if (/Macintosh|Mac OS X/i.test(ua)) return "Mac";
  if (/Android/i.test(ua)) return "Android";
  if (/Windows/i.test(ua)) return "Windows";
  return "This device";
}

export function rememberDevice(device: RememberedDevice): void {
  try {
    localStorage.setItem(DEVICE_KEY, JSON.stringify(device));
  } catch {
    // Private mode. The sign-in gate's foot line falls back to "this phone".
  }
}

export function rememberedDevice(): RememberedDevice | null {
  try {
    const raw = localStorage.getItem(DEVICE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof (parsed as RememberedDevice).name === "string" &&
      typeof (parsed as RememberedDevice).registeredAt === "string"
    ) {
      return parsed as RememberedDevice;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * One sentence for a gate's foot line. The server's own words when it has them
 * (an `ApiError` carries `detail` as its message); a written line for the two
 * DOMExceptions a cancelled or timed-out Face ID raises, whose messages are
 * WebKit's paragraph about privacy considerations, not something Alfred says.
 */
export function failureText(error: unknown): string {
  if (error instanceof DOMException) {
    return error.name === "NotAllowedError" || error.name === "AbortError"
      ? "Face ID was cancelled."
      : error.message;
  }
  return errorText(error);
}

/**
 * The sign-in gate's foot line. `GET /api/auth/status` says nothing about the
 * device before you are authenticated — correct on a public host — so this is
 * the client's own memory, and it says so vaguely when it has none.
 */
export function deviceFootLine(device: RememberedDevice | null): string {
  if (!device) return "Passkey · this phone";
  const at = new Date(device.registeredAt);
  if (Number.isNaN(at.getTime())) return `Passkey · ${device.name}`;
  return `Passkey · ${device.name} · registered ${dayMonth(at)}`;
}
