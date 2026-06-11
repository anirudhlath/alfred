import { createContext, useContext, useEffect, useMemo, useState } from "react";
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

interface AlfredContextValue {
  chat: ChatSocket;
  telemetry: TelemetrySocket;
  chatStatus: SocketStatus;
  telemetryStatus: SocketStatus;
  feed: FeedEntry[];
}

const AlfredContext = createContext<AlfredContextValue | null>(null);

// Module-level singletons avoid the react-hooks/refs rule (no useRef/.current needed)
// and the react-hooks/immutability rule (not returned from a hook). One instance per
// module load — fine because AlfredProvider renders exactly once in the app tree.
const chat = new ChatSocket();
const telemetry = new TelemetrySocket();

export function AlfredProvider({ children }: { children: React.ReactNode }) {
  const [chatStatus, setChatStatus] = useState<SocketStatus>("connecting");
  const [telemetryStatus, setTelemetryStatus] = useState<SocketStatus>("connecting");
  const [feed, setFeed] = useState<FeedEntry[]>([]);

  useEffect(() => {
    chat.onstatus = setChatStatus;
    telemetry.onstatus = setTelemetryStatus;
    const unlisten = telemetry.listen((msg: TelemetryMessage) => {
      if (msg.type === "entry") {
        setFeed((prev) => [{ stream: msg.stream, id: msg.id, event: msg.event }, ...prev].slice(0, FEED_MAX));
      }
    });
    chat.connect();
    telemetry.connect();
    telemetry.subscribe(ALL_STREAMS);
    return () => { unlisten(); chat.close(); telemetry.close(); };
  }, []);

  const value = useMemo(
    () => ({ chat, telemetry, chatStatus, telemetryStatus, feed }),
    [chatStatus, telemetryStatus, feed],
  );
  return <AlfredContext.Provider value={value}>{children}</AlfredContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAlfred(): AlfredContextValue {
  const ctx = useContext(AlfredContext);
  if (!ctx) throw new Error("useAlfred outside AlfredProvider");
  return ctx;
}
