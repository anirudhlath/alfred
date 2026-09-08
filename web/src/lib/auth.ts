import { api } from "./api";
import type { AuthStatus } from "./types";

export const DEVICE_KEY = "alfred.device";

export interface RememberedDevice {
  name: string;
  /** ISO 8601, from the moment registration completed. */
  registeredAt: string;
}

export function fetchAuthStatus(): Promise<AuthStatus> {
  return api<AuthStatus>("/api/auth/status");
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
