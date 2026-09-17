import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider, type DefaultOptions } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";

/**
 * The app's query policy, exported so a test can build a client that behaves
 * like the running one. A test client with its own `retry: false` proves the
 * harness's default rather than the source's: every retry a hook relies on —
 * and every retry it deliberately turns off — is invisible under it.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const QUERY_DEFAULTS: DefaultOptions = {
  queries: {
    // One retry, and never for a 4xx: a 401, 403 or 404 is an answer, not a
    // failure to reach the house, and repeating it only doubles the noise.
    retry: (failureCount, error) =>
      !(error instanceof ApiError && error.status < 500) && failureCount < 1,
    staleTime: 10_000,
    refetchOnWindowFocus: true,
  },
};

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: QUERY_DEFAULTS });
}

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(makeClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
