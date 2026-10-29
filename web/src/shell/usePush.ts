import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { ApiError, errorText } from "@/lib/api";
import { onVisible } from "@/lib/lifecycle";
import {
  fetchPublicKey,
  permission,
  reachContext,
  requestAndSubscribe,
  syncSubscription,
  type ReachContext,
} from "@/lib/push";

/**
 * The sync's one cache entry. A sign-in's `invalidateQueries()` (`AuthGate`)
 * reaches it like every other key, so the subscription is registered again with
 * each new session.
 */
export const PUSH_QUERY_KEY = ["push", "subscription"] as const;

/**
 * Where this phone stands for notifications, as System › Reach says it. Four
 * are facts about the phone — not installed, no push here, the owner said no,
 * not asked yet — and three are the sync's answer: in flight, the house has no
 * key, or registered. `failed` is a sync that did not land, which retry asks
 * again.
 */
export type Reach =
  | { kind: "install" }
  | { kind: "unavailable" }
  | { kind: "unconfigured" }
  | { kind: "ask" }
  | { kind: "checking" }
  | { kind: "on" }
  | { kind: "denied" }
  | { kind: "failed"; detail: string };

export interface PushValue {
  reach: Reach;
  /** The permission prompt, and the subscription behind it, are under way. */
  asking: boolean;
  /** Ask for permission and subscribe. Call it from the tap itself, nowhere else. */
  enable: () => void;
  /** Run the sync again. */
  retry: () => void;
}

type Synced = "on" | "unconfigured";

/** Register this browser's subscription, or learn that the house has no key to push with. */
async function sync(): Promise<Synced> {
  const key = await fetchPublicKey();
  if (key === null) return "unconfigured";
  await syncSubscription(key);
  return "on";
}

/**
 * The phone's facts first, the sync's answer after: a sync only runs once they
 * all allow it. By status, not by `data` — a re-sync that fails after an
 * earlier one landed is `failed`, because the house may no longer hold the row.
 * A failure being asked again is `checking`: react-query keeps the error, and
 * the old data beside it, until the new answer lands.
 */
function reachOf(
  context: ReachContext,
  allowed: NotificationPermission,
  query: UseQueryResult<Synced>,
): Reach {
  if (context === "tab") return { kind: "install" };
  if (context === "unsupported") return { kind: "unavailable" };
  if (allowed === "denied") return { kind: "denied" };
  if (allowed === "default") return { kind: "ask" };
  if (query.isPending || (query.isError && query.fetchStatus === "fetching")) {
    return { kind: "checking" };
  }
  if (query.isError) return { kind: "failed", detail: errorText(query.error) };
  return { kind: query.data };
}

/**
 * Web Push for this page: the launch sync (`PushKeeper` mounts it), and what
 * System › Reach shows and offers.
 */
export function usePush(): PushValue {
  const queryClient = useQueryClient();
  // Read once. A page never moves between a tab and the Home Screen: installing
  // opens a new one.
  const [context] = useState<ReachContext>(reachContext);
  // Re-read on each return to the foreground, which is how a change made in
  // Settings arrives, and on every render — the one after the prompt included.
  const allowed = useSyncExternalStore(onVisible, permission);
  const [asking, setAsking] = useState(false);
  const query = useQuery({
    queryKey: PUSH_QUERY_KEY,
    queryFn: sync,
    // Off while Enable runs, which registers for itself: a render that reads
    // the new permission mid-tap would otherwise start a second register.
    enabled: context === "capable" && allowed === "granted" && !asking,
    // Once a launch and once a sign-in, never on focus: the house's row does
    // not lapse, and a register on every app switch would only rewrite it.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const { refetch } = query;

  // A permission taken away strands the sync's answer: given back, the query
  // would still hold `on` for the subscription the browser deactivated, and
  // under `staleTime: Infinity` never ask again. Marked stale here, with no
  // fetch, it syncs the moment the permission returns.
  useEffect(() => {
    if (allowed === "granted") return;
    void queryClient.invalidateQueries({ queryKey: PUSH_QUERY_KEY, refetchType: "none" });
  }, [allowed, queryClient]);

  const enable = useCallback(() => {
    // First, before any await, state or effect: iOS shows the prompt only from
    // inside the gesture, and anything ahead of it can end the gesture.
    const answered = requestAndSubscribe();
    setAsking(true);
    void answered
      .then(
        (answer) => {
          // Subscribed and registered: that is the sync's answer, so the query
          // takes it as its own rather than registering a second time. A
          // refusal or a dismissed prompt needs nothing — the permission says it.
          if (answer === "granted") queryClient.setQueryData(PUSH_QUERY_KEY, "on");
        },
        (error: unknown) => {
          // The house has no key: said, and not asked again.
          if (error instanceof ApiError && error.status === 404) {
            queryClient.setQueryData(PUSH_QUERY_KEY, "unconfigured");
            return;
          }
          // Anything else, a 503 `Device store busy` say, gets the sync's own
          // try once the tap is done — and the sync's answer, whatever it is.
          void queryClient.invalidateQueries({ queryKey: PUSH_QUERY_KEY });
        },
      )
      .finally(() => setAsking(false));
  }, [queryClient]);

  const retry = useCallback(() => void refetch(), [refetch]);

  return { reach: reachOf(context, allowed, query), asking, enable, retry };
}
