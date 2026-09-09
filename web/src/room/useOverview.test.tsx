import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SESSION_IDLE_MS } from "@/lib/history";
import { overviewFixture } from "@/test/fixtures";
import { sessionIdleMs, useOverview } from "./useOverview";

const { markTrueMock } = vi.hoisted(() => ({ markTrueMock: vi.fn() }));
vi.mock("@/shell/ConnectionProvider", () => ({ markTrue: markTrueMock }));

/** When set, the overview read 500s — Redis is down behind it. */
let overviewDown = false;

function renderOverview() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useOverview(), { wrapper });
}

describe("useOverview", () => {
  beforeEach(() => {
    overviewDown = false;
    markTrueMock.mockClear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        expect(String(input)).toBe("/api/admin/overview");
        if (overviewDown) return new Response('{"detail":"redis is down"}', { status: 500 });
        return new Response(JSON.stringify(overviewFixture), { status: 200 });
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("stamps last-true on every successful read: the house answered", async () => {
    const { result } = renderOverview();
    await waitFor(() => expect(result.current.data).toEqual(overviewFixture));
    expect(markTrueMock).toHaveBeenCalledTimes(1);
  });

  it("leaves last-true alone when the read fails", async () => {
    overviewDown = true;
    const { result } = renderOverview();
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(markTrueMock).not.toHaveBeenCalled();
  });
});

describe("sessionIdleMs", () => {
  it("reads the server's idle timeout in minutes", () => {
    expect(sessionIdleMs({ ...overviewFixture, session: { idle_minutes: 10 } })).toBe(600_000);
  });

  it.each([undefined, 0, -5, Number.NaN])("falls back to the default for %s", (minutes) => {
    const overview =
      minutes === undefined
        ? undefined
        : { ...overviewFixture, session: { idle_minutes: minutes } };
    expect(sessionIdleMs(overview)).toBe(SESSION_IDLE_MS);
  });
});
