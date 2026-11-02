import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useDoor } from "@/door/DoorProvider";
import { fetchAction } from "@/lib/actions";
import { ApiError } from "@/lib/api";
import { authEvents } from "@/lib/auth-events";
import { shortId } from "@/lib/format";
import type { TimelineItem } from "@/lib/history";

/** The route a push notification opens. `lib/push.ts` admits a tap's path by it too. */
export const ACTION_PATH = /^\/actions\/([^/]+)$/;

export interface ActionRouteValue {
  /** A tombstone for an id the house no longer holds — merge it into the thread. */
  tombstone: TimelineItem | null;
}

/**
 * Handle `/actions/:id`, the URL a push notification opens.
 *
 * The id comes from the location rather than `useParams`, so the hook can live in
 * the Room — which both routes render — and keep its tombstone across the
 * `navigate("/")` instead of unmounting with the route it was called from.
 *
 * @param titles `pendingActionTitles(history)`: what the notifications page
 *   remembers each approval was about, so a lapsed one can be named.
 */
export function useActionRoute(titles: Record<string, string>): ActionRouteValue {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { actions, arrived, answered, openAction } = useDoor();
  const [missing, setMissing] = useState<{ id: string; at: string } | null>(null);
  // Sign-ins heard since mount. Each one reads a path the lapsed session left
  // unasked; the read is keyed on it, so a sign-in asks the same id again.
  const [signIns, setSignIns] = useState(0);
  const handledRef = useRef<string | null>(null);

  const id = ACTION_PATH.exec(pathname)?.[1] ?? null;

  useEffect(() => authEvents.on("signed-in", () => setSignIns((count) => count + 1)), []);

  useEffect(() => {
    // No cleanup, and no cancellation flag: StrictMode's setup→cleanup→setup
    // would otherwise abort the only read this route ever makes. The ref makes
    // the second pass a no-op instead, and is cleared once the read settles so
    // the same id can be tapped again later in the session.
    if (!id) return;
    const key = `${id}#${signIns}`;
    if (handledRef.current === key) return;
    handledRef.current = key;
    let asked = true;

    void fetchAction(id)
      .then((action) => {
        arrived(action);
        openAction(id);
      })
      .catch((error: unknown) => {
        // A 401 is no answer at all: the session lapsed, and the Expired gate
        // is up over the Room. The path stays, and the read is asked again after
        // a sign-in: here, when the Expired gate brings the session back, or on
        // the fresh mount of the Room that follows the sign-in gate.
        if (error instanceof ApiError && error.status === 401) {
          asked = false;
          return;
        }
        // Only a 404 is "already answered": the house looked and has nothing
        // to approve, so the thread says so rather than opening an empty Door.
        // A house that could not be asked has answered nothing, and the Room
        // must not claim it has — the offline note is the honest word there.
        if (error instanceof ApiError && error.status === 404) {
          // The Door hears it too: if it still tracks the approval as pending,
          // its banner would go on offering it, and its fuse would later lay a
          // second, contradicting tombstone beside this one.
          answered(id);
          setMissing({ id, at: new Date().toISOString() });
        }
      })
      .finally(() => {
        // Only its own key: a sign-in may have started a newer read meanwhile.
        if (handledRef.current === key) handledRef.current = null;
        if (!asked) return;
        // Replace, never push: a pull-to-refresh must not reopen a decision that
        // has already been answered.
        navigate("/", { replace: true });
      });
  }, [id, signIns, arrived, answered, openAction, navigate]);

  const tombstone = useMemo<TimelineItem | null>(() => {
    if (!missing) return null;
    // An approval the Door tracks gets the Door's own tombstone, which knows
    // when it was asked; this one is only for an id the Door never met.
    if (actions.some((item) => item.action.request_id === missing.id)) return null;
    return {
      kind: "tombstone",
      id: `tomb:missing:${missing.id}`,
      at: missing.at,
      title: titles[missing.id] ?? `Action ${shortId(missing.id)}`,
      meta: "already answered · nothing was done",
    };
  }, [missing, titles, actions]);

  return { tombstone };
}
