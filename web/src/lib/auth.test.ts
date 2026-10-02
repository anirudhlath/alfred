import { afterEach, describe, expect, it, vi } from "vitest";
import workerSource from "../../public/sw.js?raw";
import { ApiError, timedOut } from "./api";
import {
  DEVICE_KEY,
  STATUS_DEADLINE_MS,
  defaultDeviceName,
  deviceFootLine,
  failureText,
  fetchAuthStatus,
  rememberDevice,
  rememberedDevice,
} from "./auth";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

/** A fetch that answers every read with this body. */
const answering = (body: string | null, init: ResponseInit) =>
  vi.fn<typeof fetch>(async () => new Response(body, init));

/**
 * A fetch that never answers, and rejects once its signal aborts — with the
 * signal's reason, as the fetch standard says, unless `rejection` says what an
 * engine that does not follow it rejects with instead.
 */
function inFlight(rejection?: () => unknown) {
  return vi.fn<typeof fetch>(
    (_input, init) =>
      new Promise<Response>((_resolve, reject) => {
        const signal = init?.signal;
        if (!signal) return;
        const abort = () => reject(rejection ? rejection() : signal.reason);
        if (signal.aborted) abort();
        else signal.addEventListener("abort", abort, { once: true });
      }),
  );
}

/** The read's deadlines, in the test's hands: each one is a signal the test fires. */
function holdDeadlines(): AbortController[] {
  const deadlines: AbortController[] = [];
  vi.spyOn(AbortSignal, "timeout").mockImplementation(() => {
    const deadline = new AbortController();
    deadlines.push(deadline);
    return deadline.signal;
  });
  return deadlines;
}

const ranOut = () => new DOMException("signal timed out", "TimeoutError");

/** How a read ended: its error, or "still in flight" if it had not ended a moment later. */
const outcome = (read: Promise<unknown>): Promise<unknown> =>
  Promise.race([
    read.then(
      () => "answered",
      (error: unknown) => error,
    ),
    new Promise((resolve) => setTimeout(() => resolve("still in flight"), 20)),
  ]);

describe("fetchAuthStatus", () => {
  it("reads GET /api/auth/status", async () => {
    const mock = answering(JSON.stringify({ registered: true, authenticated: false }), {
      status: 200,
    });
    vi.stubGlobal("fetch", mock);

    await expect(fetchAuthStatus(new AbortController().signal)).resolves.toEqual({
      registered: true,
      authenticated: false,
    });
    expect(mock.mock.calls[0][0]).toBe("/api/auth/status");
    expect(mock.mock.calls[0][1]?.method ?? "GET").toBe("GET");
  });

  it("takes nothing but the house's status for an answer", async () => {
    // core/identity/auth_routes.py `auth_status` sends both booleans with every
    // 200. A 200 without them came from something else on the way: a captive
    // portal's page, a proxy's own JSON, an empty reply.
    const replies: Array<[string | null, ResponseInit]> = [
      [
        "<!doctype html><title>Sign in to this Wi-Fi</title>",
        { status: 200, headers: { "Content-Type": "text/html" } },
      ],
      ['{"ok":true}', { status: 200 }],
      ['{"registered":"yes","authenticated":false}', { status: 200 }],
      ['{"registered":true}', { status: 200 }],
      ["null", { status: 200 }],
      [null, { status: 204 }],
    ];
    for (const [body, init] of replies) {
      vi.stubGlobal("fetch", answering(body, init));
      await expect(fetchAuthStatus(new AbortController().signal)).rejects.toThrow();
    }
  });

  it("gives the house as long to answer as the worker gives a navigation", () => {
    // public/sw.js serves the cached shell once TIMEOUT_MS passes with no answer
    // from the network. The page and the worker agree on when the house is "not
    // answering": shorter, and a slow but working link is called silent; longer,
    // and a launch that will get no answer sits on the field for nothing.
    const match = /^const TIMEOUT_MS = (\d+);$/m.exec(workerSource);
    expect(match).not.toBeNull();
    expect(STATUS_DEADLINE_MS).toBe(Number(match?.[1]));
  });

  it("gives up at the deadline, and says it was the deadline", async () => {
    const deadlines = holdDeadlines();
    vi.stubGlobal("fetch", inFlight());
    const read = fetchAuthStatus(new AbortController().signal);
    expect(AbortSignal.timeout).toHaveBeenCalledWith(STATUS_DEADLINE_MS);
    expect(await outcome(read)).toBe("still in flight");

    deadlines[0].abort(ranOut());

    expect(timedOut(await outcome(read))).toBe(true);
  });

  it("says it was the deadline even where fetch rejects with an AbortError of its own", async () => {
    // Engines that predate abort reasons reject an aborted fetch with a fresh
    // AbortError; the retry policy still needs to know the time ran out.
    const deadlines = holdDeadlines();
    vi.stubGlobal("fetch", inFlight(() => new DOMException("Fetch is aborted", "AbortError")));
    const read = fetchAuthStatus(new AbortController().signal);

    deadlines[0].abort(ranOut());

    expect(timedOut(await outcome(read))).toBe(true);
  });

  it("lets react-query's own cancel through as a cancel, not a deadline", async () => {
    holdDeadlines();
    vi.stubGlobal("fetch", inFlight());
    const query = new AbortController();
    const read = fetchAuthStatus(query.signal);

    query.abort();

    const error = await outcome(read);
    expect(error).toBe(query.signal.reason);
    expect(timedOut(error)).toBe(false);
  });

  it("follows both signals where AbortSignal.any is missing, as before iOS 17.4", async () => {
    const any = Object.getOwnPropertyDescriptor(AbortSignal, "any");
    Reflect.deleteProperty(AbortSignal, "any");
    try {
      expect("any" in AbortSignal).toBe(false);
      const deadlines = holdDeadlines();
      vi.stubGlobal("fetch", inFlight());

      const timed = fetchAuthStatus(new AbortController().signal);
      deadlines[0].abort(ranOut());
      expect(timedOut(await outcome(timed))).toBe(true);

      const query = new AbortController();
      const cancelled = fetchAuthStatus(query.signal);
      expect(await outcome(cancelled)).toBe("still in flight");
      query.abort();
      expect(await outcome(cancelled)).toBe(query.signal.reason);

      // A cancel that came before the read began.
      const early = new AbortController();
      early.abort();
      expect(await outcome(fetchAuthStatus(early.signal))).toBe(early.signal.reason);
    } finally {
      if (any) Object.defineProperty(AbortSignal, "any", any);
    }
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
