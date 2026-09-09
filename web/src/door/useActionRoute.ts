import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useDoor } from "@/door/DoorProvider";
import { fetchAction } from "@/lib/actions";
import { ApiError } from "@/lib/api";
import { shortId } from "@/lib/format";
import type { TimelineItem } from "@/lib/history";

const ACTION_PATH = /^\/actions\/([^/]+)$/;

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
  const { arrived, openAction } = useDoor();
  const [missing, setMissing] = useState<{ id: string; at: string } | null>(null);
  const handledRef = useRef<string | null>(null);

  const id = ACTION_PATH.exec(pathname)?.[1] ?? null;

  useEffect(() => {
    // No cleanup, and no cancellation flag: StrictMode's setup→cleanup→setup
    // would otherwise abort the only read this route ever makes. The ref makes
    // the second pass a no-op instead.
    if (!id || handledRef.current === id) return;
    handledRef.current = id;

    void fetchAction(id)
      .then((action) => {
        arrived(action);
        openAction(id);
      })
      .catch((error: unknown) => {
        // Only a 404 is "already answered": the house looked and has nothing
        // to approve, so the thread says so rather than opening an empty Door.
        // A house that could not be asked has answered nothing, and the Room
        // must not claim it has — the offline note is the honest word there.
        if (error instanceof ApiError && error.status === 404) {
          setMissing({ id, at: new Date().toISOString() });
        }
      })
      .finally(() => {
        // Replace, never push: a pull-to-refresh must not reopen a decision that
        // has already been answered.
        navigate("/", { replace: true });
      });
  }, [id, arrived, openAction, navigate]);

  const tombstone = useMemo<TimelineItem | null>(() => {
    if (!missing) return null;
    return {
      kind: "tombstone",
      id: `tomb:missing:${missing.id}`,
      at: missing.at,
      title: titles[missing.id] ?? `Action ${shortId(missing.id)}`,
      meta: "already answered · nothing was done",
    };
  }, [missing, titles]);

  return { tombstone };
}
