import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RoomHistory } from "@/lib/history";
import {
  notificationsPage,
  reflexObservationsPage,
  userRequestsPage,
  userResponsesPage,
} from "@/test/fixtures";
import { useRoomHistory } from "./useRoomHistory";

const PAGES = {
  user_requests: userRequestsPage,
  user_responses: userResponsesPage,
  reflex_observations: reflexObservationsPage,
  notifications: notificationsPage,
};

const HISTORY: RoomHistory = {
  user_requests: userRequestsPage.entries,
  user_responses: userResponsesPage.entries,
  reflex_observations: reflexObservationsPage.entries,
  notifications: notificationsPage.entries,
};

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderHistory(client: QueryClient) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useRoomHistory(), { wrapper });
}

describe("useRoomHistory", () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const name = /streams\/(\w+)\?/.exec(String(input))?.[1] as keyof typeof PAGES;
    return new Response(JSON.stringify(PAGES[name]), { status: 200 });
  });

  beforeEach(() => {
    fetchMock.mockClear();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("reads the four pages and caches them under the key the provider invalidates", async () => {
    const client = newClient();
    const { result } = renderHistory(client);

    await waitFor(() => expect(result.current.data).toEqual(HISTORY));
    expect(fetchMock).toHaveBeenCalledTimes(4);
    // The foreground refresh (constraint §4.10) is `invalidateQueries` on this
    // exact key; a typo here would leave the thread stale until the next launch.
    expect(client.getQueryData(["room-history"])).toEqual(HISTORY);
  });

  it("reads again when the provider invalidates it", async () => {
    const client = newClient();
    const { result } = renderHistory(client);
    await waitFor(() => expect(result.current.data).toEqual(HISTORY));

    await act(() => client.invalidateQueries({ queryKey: ["room-history"] }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(8));
  });

  it("serves a second reader from cache for thirty seconds, then reads again", async () => {
    const client = newClient();
    const first = renderHistory(client);
    await waitFor(() => expect(first.result.current.data).toEqual(HISTORY));
    const read = client.getQueryState(["room-history"])?.dataUpdatedAt ?? 0;

    vi.setSystemTime(read + 29_999);
    const second = renderHistory(client);
    await waitFor(() => expect(second.result.current.data).toEqual(HISTORY));
    expect(fetchMock).toHaveBeenCalledTimes(4);

    vi.setSystemTime(read + 30_001);
    renderHistory(client);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(8));
  });
});
