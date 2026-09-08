import { describe, expect, it, vi } from "vitest";
import { authEvents } from "./auth-events";

describe("authEvents", () => {
  it("calls every handler registered for a kind", () => {
    const first = vi.fn();
    const second = vi.fn();
    const offFirst = authEvents.on("expired", first);
    const offSecond = authEvents.on("expired", second);

    authEvents.emit("expired");

    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
    offFirst();
    offSecond();
  });

  it("keeps the kinds apart", () => {
    const expired = vi.fn();
    const denied = vi.fn();
    const offExpired = authEvents.on("expired", expired);
    const offDenied = authEvents.on("denied", denied);

    authEvents.emit("denied");

    expect(expired).not.toHaveBeenCalled();
    expect(denied).toHaveBeenCalledTimes(1);
    offExpired();
    offDenied();
  });

  it("stops calling a handler once it unsubscribes", () => {
    const handler = vi.fn();
    const off = authEvents.on("expired", handler);
    off();

    authEvents.emit("expired");

    expect(handler).not.toHaveBeenCalled();
  });

  it("survives a handler that unsubscribes itself mid-emit", () => {
    const seen: string[] = [];
    const off1 = authEvents.on("expired", () => {
      seen.push("first");
      off1();
    });
    const off2 = authEvents.on("expired", () => seen.push("second"));

    expect(() => authEvents.emit("expired")).not.toThrow();
    expect(seen).toEqual(["first", "second"]);
    off2();
  });

  it("is a no-op with nothing listening", () => {
    expect(() => authEvents.emit("denied")).not.toThrow();
  });
});
