import { afterEach, describe, expect, it, vi } from "vitest";
import { DEVICE_KEY, defaultDeviceName, fetchAuthStatus, rememberDevice, rememberedDevice } from "./auth";

afterEach(() => {
  vi.unstubAllGlobals();
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
});
