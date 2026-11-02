import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { onOpenPath } from "@/lib/push";
import { usePush } from "@/shell/usePush";

/**
 * The launch sync, and nothing to see. Inside `AuthGate`, so it runs only with a
 * session — the registration needs one — and again after each sign-in. A row
 * the house pruned or lost comes back without the owner doing anything.
 */
export function PushKeeper(): null {
  usePush();
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
