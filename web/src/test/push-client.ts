import { QueryClient } from "@tanstack/react-query";
import { expect } from "vitest";
import { ApiError } from "@/lib/api";
import { QUERY_DEFAULTS } from "@/shell/QueryProvider";
import { PUSH_QUERY_KEY } from "@/shell/usePush";

/** What a failed sync's register was refused with, in `failedClient`. */
export const BUSY = "Device store busy";

/**
 * A client on the app's own policy, so the one retry a failed sync gets is the
 * source's and not the harness's. Only the backoff is the harness's.
 */
export const makeClient = (): QueryClient =>
  new QueryClient({
    defaultOptions: { ...QUERY_DEFAULTS, queries: { ...QUERY_DEFAULTS.queries, retryDelay: 0 } },
  });

/**
 * A client already holding a sync that failed, as it is when System is opened
 * after one: `afterOn` puts an earlier `on` behind the failure, the shape a
 * sign-in's failed re-sync leaves. Either way react-query has marked the query
 * invalidated, which is what a mounting observer reads as a reason to fetch.
 */
export async function failedClient(afterOn: boolean): Promise<QueryClient> {
  const client = makeClient();
  if (afterOn) client.setQueryData(PUSH_QUERY_KEY, "on");
  await client
    .fetchQuery({
      queryKey: PUSH_QUERY_KEY,
      queryFn: () => Promise.reject(new ApiError(503, BUSY)),
      retry: false,
      staleTime: 0,
    })
    .catch(() => {});
  expect(client.getQueryState(PUSH_QUERY_KEY)).toMatchObject({
    status: "error",
    isInvalidated: true,
    data: afterOn ? "on" : undefined,
  });
  return client;
}
