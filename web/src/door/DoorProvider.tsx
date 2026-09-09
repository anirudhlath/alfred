import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useState,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";
import {
  actionReducer,
  confirmAction,
  fetchAction,
  fetchPending,
  type TrackedAction,
} from "@/lib/actions";
import { ApiError } from "@/lib/api";
import type { ActionResultEvent, PendingAction } from "@/lib/types";
import { useConnection } from "@/shell/ConnectionProvider";

/** The fuse steps once a second, exactly as the handoff draws it. */
const TICK_MS = 1000;
/** The only stream the Door needs; "applied" is what it carries. */
const RESULT_STREAM = "home_action_results";

export interface DoorValue {
  /** Everything tracked, oldest first — including tombstones. */
  actions: TrackedAction[];
  /** Just the ones still waiting on you. */
  pending: TrackedAction[];
  current: TrackedAction | null;
  open: boolean;
  openAction: (id: string) => void;
  close: () => void;
  confirm: (id: string) => void;
  /** Push an action in from outside — the `/actions/:id` deep link uses this. */
  arrived: (action: PendingAction) => void;
  /** Epoch ms, stepped once a second while anything is counting. */
  now: number;
}

const DoorContext = createContext<DoorValue | null>(null);

export function DoorProvider({ children }: { children: ReactNode }) {
  const { chat, telemetry } = useConnection();
  const [actions, dispatch] = useReducer(actionReducer, []);
  const [openId, setOpenId] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Cold launch and every return from the background: ConnectionProvider
  // invalidates this key on `visibilitychange` (1a, Task 12).
  const { data } = useQuery<PendingAction[]>({
    queryKey: ["pending-actions"],
    queryFn: fetchPending,
    staleTime: 10_000,
  });

  useEffect(() => {
    if (data) dispatch({ type: "loaded", actions: data });
  }, [data]);

  const arrived = useCallback((action: PendingAction) => {
    dispatch({ type: "arrived", action });
  }, []);

  // The app was open when the conscious engine asked. The notification carries
  // the id but not the action, so read it — and say nothing if it has already gone.
  useEffect(() => {
    return chat.listen((msg) => {
      if (msg.type !== "notification") return;
      const id = msg.metadata?.pending_action_id;
      if (typeof id !== "string") return;
      void fetchAction(id)
        .then((action) => dispatch({ type: "arrived", action }))
        .catch(() => {
          // 404: consumed or expired before we asked. Nothing to show.
        });
    });
  }, [chat]);

  // The one true source of "applied".
  useEffect(() => {
    telemetry.subscribe([RESULT_STREAM]);
    return telemetry.listen((msg) => {
      if (msg.type !== "entry" || msg.stream !== RESULT_STREAM) return;
      const result = msg.event as unknown as ActionResultEvent;
      if (typeof result?.request_id !== "string") return;
      dispatch({ type: "result", result, at: new Date().toISOString() });
    });
  }, [telemetry]);

  // Only run a clock while something is actually counting: a Room with no
  // pending approval must not re-render once a second for ever.
  const counting = actions.some((item) => item.phase === "pending" || item.phase === "queued");

  useEffect(() => {
    if (!counting) return;
    const timer = setInterval(() => {
      const at = Date.now();
      setNow(at);
      dispatch({ type: "tick", now: at });
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [counting]);

  const confirm = useCallback((id: string) => {
    void confirmAction(id)
      .then(() => dispatch({ type: "confirm-sent", id, at: new Date().toISOString() }))
      .catch((error: unknown) => {
        // 404 is the handoff's "already consumed" case: the same tombstone as an
        // expiry, with a different sentence. Anything else leaves it pending so
        // the user can try again while the fuse still runs.
        if (error instanceof ApiError && error.status === 404) {
          dispatch({ type: "confirm-404", id });
        }
      });
  }, []);

  const value = useMemo<DoorValue>(() => {
    const current = actions.find((item) => item.action.request_id === openId) ?? null;
    return {
      actions,
      pending: actions.filter((item) => item.phase === "pending"),
      current,
      open: current !== null,
      openAction: setOpenId,
      close: () => setOpenId(null),
      confirm,
      arrived,
      now,
    };
  }, [actions, openId, now, confirm, arrived]);

  return <DoorContext.Provider value={value}>{children}</DoorContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useDoor(): DoorValue {
  const ctx = useContext(DoorContext);
  if (!ctx) throw new Error("useDoor outside DoorProvider");
  return ctx;
}
