import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { authEvents } from "@/lib/auth-events";
import { ChatSocket } from "@/lib/chat-socket";
import { onVisible } from "@/lib/lifecycle";
import { TelemetrySocket } from "@/lib/telemetry-socket";
import type { SocketStatus } from "@/lib/ws";

// Module-level singletons, as the outgoing AlfredProvider had them: one instance
// per module load, which is exactly one per app. Keeps them out of refs and out
// of the react-hooks immutability rule.
const chat = new ChatSocket();
const telemetry = new TelemetrySocket();

/**
 * Make sure both sockets are live. `connect()` reopens a closed socket, replaces
 * one that has gone quiet and leaves a healthy one alone, so this is safe on
 * every return to the foreground — and necessary after every sign-in: the server
 * closes both sockets with 4001 while there is no session, and a 4001 is never
 * retried.
 */
function reconnect(): void {
  chat.connect();
  telemetry.connect();
}

let lastTrue: Date | null = null;
const lastTrueListeners = new Set<(at: Date) => void>();

/**
 * Record that the house answered just now. Called from here on every chat frame
 * and every socket open, and from `useOverview` on every successful poll — the
 * two independent proofs that anything on screen is current.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function markTrue(at: Date = new Date()): void {
  lastTrue = at;
  for (const fn of [...lastTrueListeners]) fn(at);
}

function subscribeTrue(fn: (at: Date) => void): () => void {
  lastTrueListeners.add(fn);
  return () => void lastTrueListeners.delete(fn);
}

let chatOnline = false;
const onlineListeners = new Set<() => void>();

/**
 * Run `fn` each time the chat socket (re)opens, and at once if it is open now.
 * A subscriber that arrives after the open — the Room, behind a gate that took
 * longer than the handshake — must not wait for the next reconnect.
 */
function subscribeOnline(fn: () => void): () => void {
  onlineListeners.add(fn);
  if (chatOnline) fn();
  return () => void onlineListeners.delete(fn);
}

/** What a suspended PWA has to re-read on return; the telemetry socket replays nothing. */
const REHYDRATE_KEYS = [["overview"], ["room-history"], ["pending-actions"], ["deferred"]];

/**
 * How often the same complaint from the telemetry pump may reach the console.
 * The pump repeats `redis_error` once a second for the whole of an outage
 * (`core/channels/telemetry_ws.py`), and a Home Screen app stays open for days.
 */
const WARN_EVERY_MS = 60_000;

export interface ConnectionValue {
  chat: ChatSocket;
  telemetry: TelemetrySocket;
  chatStatus: SocketStatus;
  telemetryStatus: SocketStatus;
  online: boolean;
  lastTrueAt: Date | null;
  /** The moment a queued send can succeed. See `subscribeOnline` above. */
  subscribeOnline: (fn: () => void) => () => void;
  /** Reopen whatever is closed. The gates call it after a sign-in. See `reconnect` above. */
  reconnect: () => void;
}

const ConnectionContext = createContext<ConnectionValue | null>(null);

export function ConnectionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [chatStatus, setChatStatus] = useState<SocketStatus>("connecting");
  const [telemetryStatus, setTelemetryStatus] = useState<SocketStatus>("connecting");
  const [lastTrueAt, setLastTrueAt] = useState<Date | null>(lastTrue);

  useEffect(() => {
    chat.onstatus = (status) => {
      chatOnline = status === "online";
      setChatStatus(status);
      if (chatOnline) {
        markTrue();
        for (const fn of [...onlineListeners]) fn();
      }
    };
    telemetry.onstatus = setTelemetryStatus;

    const stopListening = chat.listen(() => markTrue());
    // The pump's own trouble — Redis gone, a frame it could not read — has no
    // surface until the Workshop's health page (spec §8, phase 3). Until then
    // it goes to the console, where Safari's inspector can see it, not nowhere.
    const lastWarned = new Map<string, number>();
    const stopWarning = telemetry.listen((msg) => {
      const trouble = msg.type === "status" ? msg.detail : msg.type === "error" ? msg.message : null;
      if (trouble === null) return;
      const now = Date.now();
      const before = lastWarned.get(trouble);
      if (before !== undefined && now - before < WARN_EVERY_MS) return;
      lastWarned.set(trouble, now);
      console.warn(`telemetry: ${trouble}`);
    });
    const stopTrue = subscribeTrue(setLastTrueAt);

    reconnect();

    const stopVisible = onVisible(() => {
      reconnect();
      for (const queryKey of REHYDRATE_KEYS) void queryClient.invalidateQueries({ queryKey });
    });

    return () => {
      // The singletons outlive this mount; leave nothing of it on them. The
      // `close()` calls below still produce a close event each, and it must not
      // reach a setState on an unmounted tree or flip `chatOnline` under the next
      // mount.
      chat.onstatus = () => {};
      telemetry.onstatus = () => {};
      stopListening();
      stopWarning();
      stopTrue();
      stopVisible();
      chat.close();
      telemetry.close();
      chatOnline = false;
      // The other piece of module state this mount wrote. "Last true" is a
      // claim about a house this mount was talking to; with the sockets closed
      // behind it, the next mount has no business inheriting it and must earn
      // its own stamp. Leaving it set also carried one test file's stamp into
      // the next, which is a whole class of order-dependent failure.
      lastTrue = null;
    };
  }, [queryClient]);

  // Either socket closing with 4001 means the cookie is gone. Same news as a 401
  // from the API, same gate.
  useEffect(() => {
    if (chatStatus === "unauthorized" || telemetryStatus === "unauthorized") {
      authEvents.emit("expired");
    }
  }, [chatStatus, telemetryStatus]);

  const value = useMemo<ConnectionValue>(
    () => ({
      chat,
      telemetry,
      chatStatus,
      telemetryStatus,
      // The telemetry socket can be down while the butler still answers; only the
      // chat socket decides whether the app calls itself online.
      online: chatStatus === "online",
      lastTrueAt,
      subscribeOnline,
      reconnect,
    }),
    [chatStatus, telemetryStatus, lastTrueAt],
  );

  return <ConnectionContext.Provider value={value}>{children}</ConnectionContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useConnection(): ConnectionValue {
  const ctx = useContext(ConnectionContext);
  if (!ctx) throw new Error("useConnection outside ConnectionProvider");
  return ctx;
}
