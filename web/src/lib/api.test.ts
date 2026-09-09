import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, post, put } from "./api";
import { authEvents, type AuthEventKind } from "./auth-events";

// `authEvents` is a module singleton: unsubscribe in cleanup, not in the test
// body, or one failed assertion leaks its handler into every later test.
const subscriptions: Array<() => void> = [];
function listen(kind: AuthEventKind, fn: () => void): void {
  subscriptions.push(authEvents.on(kind, fn));
}

afterEach(() => {
  for (const off of subscriptions.splice(0)) off();
  vi.unstubAllGlobals();
});

function stubFetch(status: number, body: unknown) {
  const mock = vi.fn<typeof fetch>(
    async () => new Response(status === 204 ? null : JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("api", () => {
  it("returns parsed JSON on success", async () => {
    stubFetch(200, { ok: true });
    await expect(api("/api/admin/overview")).resolves.toEqual({ ok: true });
  });

  it("sends JSON and keeps caller headers", async () => {
    const mock = stubFetch(200, {});
    await api("/x", { headers: { "X-Test": "1" } });
    const init = mock.mock.calls[0][1] as RequestInit;
    expect(init.headers).toMatchObject({ "Content-Type": "application/json", "X-Test": "1" });
  });

  it("resolves undefined for a 204", async () => {
    stubFetch(204, null);
    await expect(api("/x", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("carries the FastAPI detail onto the error", async () => {
    stubFetch(503, { detail: "Attention store unavailable" });
    await expect(api("/x")).rejects.toMatchObject({
      status: 503,
      detail: "Attention store unavailable",
      message: "Attention store unavailable",
    });
  });

  it("keeps a non-JSON error body as the detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("upstream is down", { status: 502 })),
    );
    await expect(api("/x")).rejects.toMatchObject({ status: 502, detail: "upstream is down" });
  });

  it("falls back to the status text when the error body is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 500, statusText: "Internal Server Error" })),
    );
    await expect(api("/x")).rejects.toMatchObject({
      status: 500,
      detail: "Internal Server Error",
      message: "Internal Server Error",
    });
  });

  it("announces a lapsed session on 401 and still throws", async () => {
    const expired = vi.fn();
    listen("expired", expired);
    const assign = vi.fn();
    vi.stubGlobal("location", { assign, pathname: "/", hostname: "alfred.example.com" });
    stubFetch(401, { detail: "Authentication required" });

    await expect(api("/x")).rejects.toBeInstanceOf(ApiError);

    expect(expired).toHaveBeenCalledTimes(1);
    // The old client hard-redirected here; the Expired gate now rises over the room.
    expect(assign).not.toHaveBeenCalled();
  });

  it("does not call a rejected passkey attempt a lapsed session", async () => {
    const expired = vi.fn();
    listen("expired", expired);
    stubFetch(401, { detail: "Authentication failed" });

    await expect(post("/api/auth/login/complete", {})).rejects.toMatchObject({
      status: 401,
      detail: "Authentication failed",
    });
    await expect(post("/api/auth/register/complete", {})).rejects.toMatchObject({ status: 401 });

    expect(expired).not.toHaveBeenCalled();
  });

  it("announces a refused network on 403 and still throws", async () => {
    const denied = vi.fn();
    listen("denied", denied);
    stubFetch(403, { detail: "Request from untrusted network 192.168.1.24" });

    await expect(api("/x")).rejects.toMatchObject({ status: 403 });

    expect(denied).toHaveBeenCalledTimes(1);
  });

  it("does not announce anything for other failures", async () => {
    const expired = vi.fn();
    const denied = vi.fn();
    listen("expired", expired);
    listen("denied", denied);
    stubFetch(500, { detail: "boom" });

    await expect(api("/x")).rejects.toMatchObject({ status: 500 });

    expect(expired).not.toHaveBeenCalled();
    expect(denied).not.toHaveBeenCalled();
  });
});

describe("post / put", () => {
  it("post sends a JSON body", async () => {
    const mock = stubFetch(200, { status: "ok" });
    await post("/api/actions/a91f/confirm", { note: "yes" });
    const init = mock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"note":"yes"}');
  });

  it("post with no body sends none", async () => {
    const mock = stubFetch(200, {});
    await post("/api/admin/notifications/drain");
    const init = mock.mock.calls[0][1] as RequestInit;
    expect(init.body).toBeUndefined();
  });

  it("put sends a JSON body", async () => {
    const mock = stubFetch(200, { status: "ok" });
    await put("/api/integrations/home-service/credentials", { url: "http://192.168.1.10:8123" });
    const init = mock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("PUT");
    expect(init.body).toBe('{"url":"http://192.168.1.10:8123"}');
  });
});
