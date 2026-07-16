import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { playWavBase64 } from "@/lib/audio";
import { ChatSocket } from "@/lib/chat-socket";
import { TelemetrySocket } from "@/lib/telemetry-socket";
import type { TelemetryMessage } from "@/lib/types";
import type { SocketStatus } from "@/lib/ws";

export interface FeedEntry { stream: string; id: string; event: Record<string, unknown> }

const FEED_MAX = 500;
const ALL_STREAMS = [
  "events", "actions", "user_requests", "user_responses",
  "reflex_observations", "notifications", "home_state", "home_action_results",
];

// --- Status context (changes rarely: socket status transitions) ---
interface AlfredStatusValue {
  chat: ChatSocket;
  telemetry: TelemetrySocket;
  chatStatus: SocketStatus;
  telemetryStatus: SocketStatus;
}

// --- Feed context (changes per telemetry event) ---
interface AlfredFeedValue {
  feed: FeedEntry[];
}

// --- Combined shape (for backward-compat useAlfred()) ---
interface AlfredContextValue extends AlfredStatusValue, AlfredFeedValue {}

const AlfredStatusContext = createContext<AlfredStatusValue | null>(null);
const AlfredFeedContext = createContext<AlfredFeedValue | null>(null);

// Module-level singletons avoid the react-hooks/refs rule (no useRef/.current needed)
// and the react-hooks/immutability rule (not returned from a hook). One instance per
// module load — fine because AlfredProvider renders exactly once in the app tree.
const chat = new ChatSocket();
const telemetry = new TelemetrySocket();

export function AlfredProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [chatStatus, setChatStatus] = useState<SocketStatus>("connecting");
  const [telemetryStatus, setTelemetryStatus] = useState<SocketStatus>("connecting");
  const [feed, setFeed] = useState<FeedEntry[]>([]);

  useEffect(() => {
    chat.onstatus = setChatStatus;
    telemetry.onstatus = setTelemetryStatus;
    const unlistenFeed = telemetry.listen((msg: TelemetryMessage) => {
      if (msg.type === "entry") {
        setFeed((prev) => [{ stream: msg.stream, id: msg.id, event: msg.event }, ...prev].slice(0, FEED_MAX));
      }
    });
    // Notifications are handled here, not in useChat — the chat socket stays
    // connected across routes but useChat only mounts on the chat page, so a toast
    // (and urgent TTS audio) would otherwise be dropped on every other route.
    const unlistenChat = chat.listen((msg) => {
      if (msg.type !== "notification") return;
      const urgent = msg.urgency === "urgent";
      toast(msg.title, { description: msg.body, ...(urgent ? { duration: 10000 } : {}) });
      if (urgent && msg.audio) playWavBase64(msg.audio);
    });
    chat.connect();
    telemetry.connect();
    telemetry.subscribe(ALL_STREAMS);
    return () => { unlistenFeed(); unlistenChat(); chat.close(); telemetry.close(); };
  }, []);

  // Session cookie expired mid-session (either socket closed with 4001): refetch
  // auth-status so Guarded re-evaluates and redirects to /login, instead of the
  // sockets silently dead-ending on "unauthorized".
  useEffect(() => {
    if (chatStatus === "unauthorized" || telemetryStatus === "unauthorized") {
      void queryClient.invalidateQueries({ queryKey: ["auth-status"] });
    }
  }, [chatStatus, telemetryStatus, queryClient]);

  // Status value: only re-creates when socket statuses change (rare)
  const statusValue = useMemo<AlfredStatusValue>(
    () => ({ chat, telemetry, chatStatus, telemetryStatus }),
    [chatStatus, telemetryStatus],
  );

  // Feed value: re-creates on every telemetry entry (frequent)
  const feedValue = useMemo<AlfredFeedValue>(() => ({ feed }), [feed]);

  return (
    <AlfredStatusContext.Provider value={statusValue}>
      <AlfredFeedContext.Provider value={feedValue}>
        {children}
      </AlfredFeedContext.Provider>
    </AlfredStatusContext.Provider>
  );
}

/**
 * Subscribe to socket status only. Prefer this over useAlfred() in components
 * that don't need feed — avoids a re-render on every telemetry entry.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useAlfredStatus(): AlfredStatusValue {
  const ctx = useContext(AlfredStatusContext);
  if (!ctx) throw new Error("useAlfredStatus outside AlfredProvider");
  return ctx;
}

/**
 * Subscribe to the live telemetry feed. Re-renders on every entry.
 * Use only in components that actually render feed data (e.g. ActivityPage).
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useAlfredFeed(): FeedEntry[] {
  const ctx = useContext(AlfredFeedContext);
  if (!ctx) throw new Error("useAlfredFeed outside AlfredProvider");
  return ctx.feed;
}

/**
 * Combined hook for plan compatibility. Components that don't need `feed`
 * should prefer useAlfredStatus() to avoid per-event re-renders.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useAlfred(): AlfredContextValue {
  const status = useContext(AlfredStatusContext);
  const feedCtx = useContext(AlfredFeedContext);
  if (!status) throw new Error("useAlfred outside AlfredProvider");
  if (!feedCtx) throw new Error("useAlfred outside AlfredProvider");
  return useMemo(() => ({ ...status, ...feedCtx }), [status, feedCtx]);
}
