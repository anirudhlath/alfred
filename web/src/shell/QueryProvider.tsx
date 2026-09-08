import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // One retry, and never for a 4xx: a 401, 403 or 404 is an answer, not a
        // failure to reach the house, and repeating it only doubles the noise.
        retry: (failureCount, error) =>
          !(error instanceof ApiError && error.status < 500) && failureCount < 1,
        staleTime: 10_000,
        refetchOnWindowFocus: true,
      },
    },
  });
}

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(makeClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
