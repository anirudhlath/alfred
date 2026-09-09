import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { DEVICE_KEY, defaultDeviceName, deviceFootLine, failureText, fetchAuthStatus, rememberDevice, rememberedDevice } from "./auth";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("fetchAuthStatus", () => {
  it("reads GET /api/auth/status", async () => {
    const mock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ registered: true, authenticated: false }), { status: 200 }),
    );
    vi.stubGlobal("fetch", mock);

    await expect(fetchAuthStatus()).resolves.toEqual({ registered: true, authenticated: false });
    expect(mock.mock.calls[0][0]).toBe("/api/auth/status");
    expect((mock.mock.calls[0][1] as RequestInit | undefined)?.method ?? "GET").toBe("GET");
  });
});

describe("defaultDeviceName", () => {
  it("names the common devices", () => {
    expect(
      defaultDeviceName(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
      ),
    ).toBe("iPhone");
    expect(defaultDeviceName("Mozilla/5.0 (iPad; CPU OS 18_2 like Mac OS X) AppleWebKit/605.1.15")).toBe("iPad");
    expect(defaultDeviceName("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15")).toBe("Mac");
    expect(defaultDeviceName("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36")).toBe("Android");
    expect(defaultDeviceName("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")).toBe("Windows");
  });

  it("falls back rather than guessing", () => {
    expect(defaultDeviceName("curl/8.9.1")).toBe("This device");
  });
});

describe("rememberDevice / rememberedDevice", () => {
  it("uses the alfred.device key", () => {
    expect(DEVICE_KEY).toBe("alfred.device");
  });

  it("round-trips a device", () => {
    rememberDevice({ name: "iPhone", registeredAt: "2026-09-07T07:02:00.000Z" });
    expect(rememberedDevice()).toEqual({ name: "iPhone", registeredAt: "2026-09-07T07:02:00.000Z" });
  });

  it("is null before anything is remembered", () => {
    expect(rememberedDevice()).toBeNull();
  });

  it("is null for a corrupt value", () => {
    localStorage.setItem(DEVICE_KEY, "{not json");
    expect(rememberedDevice()).toBeNull();
  });

  it("is null for a value of the wrong shape", () => {
    localStorage.setItem(DEVICE_KEY, JSON.stringify({ name: 17 }));
    expect(rememberedDevice()).toBeNull();
  });

  it("is null when registeredAt is missing", () => {
    localStorage.setItem(DEVICE_KEY, JSON.stringify({ name: "iPhone" }));
    expect(rememberedDevice()).toBeNull();
  });

  it("survives a storage that refuses to write", () => {
    vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => rememberDevice({ name: "iPhone", registeredAt: "2026-09-07T07:02:00.000Z" })).not.toThrow();
  });

  it("is null when storage cannot be read", () => {
    vi.spyOn(localStorage, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(rememberedDevice()).toBeNull();
  });
});

describe("failureText", () => {
  it("repeats the server's own words", () => {
    expect(failureText(new ApiError(503, "Attention store unavailable"))).toBe(
      "Attention store unavailable",
    );
  });

  it("writes its own line for a cancelled or timed-out Face ID", () => {
    const webkit =
      "The operation either timed out or was not allowed. See: https://www.w3.org/TR/webauthn-2/#sctn-privacy-considerations-client.";
    expect(failureText(new DOMException(webkit, "NotAllowedError"))).toBe("Face ID was cancelled.");
    expect(failureText(new DOMException("Aborted", "AbortError"))).toBe("Face ID was cancelled.");
  });

  it("keeps any other DOMException's message", () => {
    expect(failureText(new DOMException("Already registered", "InvalidStateError"))).toBe(
      "Already registered",
    );
  });

  it("falls back for anything that is not an Error", () => {
    expect(failureText(new Error("Credential creation cancelled"))).toBe(
      "Credential creation cancelled",
    );
    expect(failureText("nope")).toBe("Something went wrong.");
  });
});

describe("deviceFootLine", () => {
  it("names the remembered passkey and when it was made", () => {
    expect(
      deviceFootLine({ name: "iPhone", registeredAt: new Date(2026, 7, 12, 7, 2).toISOString() }),
    ).toBe("Passkey · iPhone · registered 12 Aug");
  });

  it("says as little as it knows when nothing is remembered", () => {
    expect(deviceFootLine(null)).toBe("Passkey · this phone");
  });

  it("drops the date rather than printing an invalid one", () => {
    expect(deviceFootLine({ name: "iPad", registeredAt: "whenever" })).toBe("Passkey · iPad");
  });
});
