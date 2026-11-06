import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { clearBadge, onBadgeAdded } from "@/lib/badge";
import { onVisible } from "@/lib/lifecycle";
import { onOpenPath, reachContext } from "@/lib/push";
import { usePush } from "@/shell/usePush";

/**
 * The launch sync and the icon's number, and nothing to see. Inside `AuthGate`,
 * so it runs only with a session — the registration needs one — and again after
 * each sign-in. A row the house pruned or lost comes back without the owner doing
 * anything.
 *
 * Opening the app takes the number off the icon: this mount, each return to the
 * foreground, and a push that lands while on screen. For that last one the worker
 * always adds and the page decides, because a worker's reading of
 * `visibilityState` is unverified on iOS. A suspended page runs nothing, so its
 * count stands until it returns. Only the installed app has a number to clear.
 */
export function PushKeeper(): null {
  usePush();
  useEffect(() => {
    if (reachContext() !== "capable") return;
    const clear = () => void clearBadge();
    clear();
    const offVisible = onVisible(clear);
    const offAdded = onBadgeAdded(() => {
      if (document.visibilityState === "visible") clear();
    });
    return () => {
      offVisible();
      offAdded();
    };
  }, []);
  return null;
}

/**
 * Routes a notification tapped while a window was open: the worker focuses it
 * and posts the path, and `onOpenPath` passes on only a path the router routes.
 * Outside `AuthGate`, since it needs only the router: a tap is heard whichever
 * gate is up.
 */
export function NotificationOpen(): null {
  const navigate = useNavigate();
  useEffect(() => onOpenPath((path) => void navigate(path)), [navigate]);
  return null;
}
