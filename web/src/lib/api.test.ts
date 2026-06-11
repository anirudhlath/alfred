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

  it("parses FastAPI detail from JSON error body", async () => {
    stubFetch(503, { detail: "Vector search unavailable" });
    await expect(api("/x")).rejects.toMatchObject({
      status: 503,
      message: "Vector search unavailable",
    });
  });
});
