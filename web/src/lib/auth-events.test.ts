import { afterEach, describe, expect, it, vi } from "vitest";
import { authEvents, type AuthEventKind } from "./auth-events";

// The emitter is a module singleton: unsubscribe in cleanup, not in the test
// body, or one failed assertion leaks its handler into every later test.
const subscriptions: Array<() => void> = [];
function listen(kind: AuthEventKind, fn: () => void): () => void {
  const off = authEvents.on(kind, fn);
  subscriptions.push(off);
  return off;
}

afterEach(() => {
  for (const off of subscriptions.splice(0)) off();
  vi.restoreAllMocks();
});

describe("authEvents", () => {
  it("calls every handler registered for a kind", () => {
    const first = vi.fn();
    const second = vi.fn();
    listen("expired", first);
    listen("expired", second);

    authEvents.emit("expired");

    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("keeps the kinds apart", () => {
    const expired = vi.fn();
    const denied = vi.fn();
    listen("expired", expired);
    listen("denied", denied);

    authEvents.emit("denied");

    expect(expired).not.toHaveBeenCalled();
    expect(denied).toHaveBeenCalledTimes(1);
  });

  it("stops calling a handler once it unsubscribes", () => {
    const handler = vi.fn();
    const off = listen("expired", handler);
    off();

    authEvents.emit("expired");

    expect(handler).not.toHaveBeenCalled();
  });

  it("survives a handler that unsubscribes itself mid-emit", () => {
    const seen: string[] = [];
    const off1 = listen("expired", () => {
      seen.push("first");
      off1();
    });
    listen("expired", () => seen.push("second"));

    expect(() => authEvents.emit("expired")).not.toThrow();
    expect(seen).toEqual(["first", "second"]);
  });

  it("keeps going past a handler that throws, and does not rethrow", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const second = vi.fn();
    listen("expired", () => {
      throw new Error("handler broke");
    });
    listen("expired", second);

    expect(() => authEvents.emit("expired")).not.toThrow();
    expect(second).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalledWith("auth-events handler failed", expect.any(Error));
  });

  it("is a no-op with nothing listening", () => {
    expect(() => authEvents.emit("denied")).not.toThrow();
  });
});
