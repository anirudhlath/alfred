import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { failureText } from "@/lib/auth";
import { feedReducer, initialFeed, mergeRows, olderTargets, type FeedRow } from "@/lib/feed";
import { onVisible } from "@/lib/lifecycle";
import { fetchStreamPage, isStreamName, STREAMS, type StreamName } from "@/lib/streams";
import type { StreamPage } from "@/lib/types";
import { useConnection } from "@/shell/ConnectionProvider";

export interface Activity {
  /** What the list shows: the solo stream or all eight, newest first, from the horizon forward. */
  rows: FeedRow[];
  /**
   * Entries *held* per stream — the chips' numbers. This is loaded depth, not
   * visible rows: with all streams shown, a stream paged deeper than the shared
   * horizon holds more entries than the list shows (solo it to see them all).
   */
  counts: Record<StreamName, number>;
  /** The telemetry socket is up. When false the stale banner shows. */
  live: boolean;
  paused: boolean;
  /** When the feed was last known live, for the stale banner; null if never. */
  liveAt: number | null;
  heldCount: number;
  solo: StreamName | null;
  setSolo: (name: StreamName | null) => void;
  /** The one open row's key. */
  expanded: string | null;
  toggle: (key: string) => void;
  pause: () => void;
  resume: () => void;
  /** What `↑ older` reads before; null when there is nothing older to fetch. */
  cursor: string | null;
  fetchingOlder: boolean;
  loadOlder: () => void;
  /** The first head read has finished (well or badly). */
  loaded: boolean;
  error: string | null;
}

type Settled = PromiseSettledResult<StreamPage>[];

function readFailure(results: Settled, of: number): string | null {
  const failed = results.filter((r) => r.status === "rejected");
  if (failed.length === 0) return null;
  const reason = (failed[0] as PromiseRejectedResult).reason as unknown;
  return `${failed.length} of ${of} streams could not be read · ${failureText(reason)}`;
}

export function useActivity(): Activity {
  const { telemetry, telemetryStatus } = useConnection();
  const [feed, dispatch] = useReducer(feedReducer, undefined, initialFeed);
  const [solo, setSoloState] = useState<StreamName | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [fetchingOlder, setFetchingOlder] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Start a read of all eight heads; the caller does not wait for it. Only
  // `dispatch`, `setError` and `setLoaded` are touched, all stable, so this
  // identity never changes and neither effect below re-runs because of it.
  const readHeads = useCallback(() => {
    void (async () => {
      const results = await Promise.allSettled(STREAMS.map((name) => fetchStreamPage(name)));
      results.forEach((result, i) => {
        if (result.status === "fulfilled") {
          dispatch({ type: "page", stream: STREAMS[i], page: result.value, mode: "head" });
        }
      });
      setError(readFailure(results, STREAMS.length));
      setLoaded(true);
    })();
  }, []);

  useEffect(() => {
    readHeads();
  }, [readHeads]);

  // Head pages on every return to the foreground: the socket replays nothing,
  // so whatever happened while the app was suspended is only on the server
  // (spec §10) — but *not* while paused. The reducer merges a head page
  // straight into the entries whatever `paused` says (pages are not live
  // frames), so re-reading here would slide rows into a list the handoff
  // promises will not move until Resume. Resume does the re-read instead,
  // which also recovers whatever the socket missed during the pause.
  useEffect(() => {
    if (feed.paused) return;
    return onVisible(readHeads);
  }, [feed.paused, readHeads]);

  // All eight streams for as long as the bench is mounted. The socket counts
  // wanters per stream, so this never cancels the Door's own subscription.
  useEffect(() => {
    telemetry.subscribe([...STREAMS]);
    const stop = telemetry.listen((msg) => {
      if (msg.type !== "entry" || !isStreamName(msg.stream)) return;
      dispatch({
        type: "live",
        stream: msg.stream,
        entry: { id: msg.id, event: msg.event },
        at: Date.now(),
      });
    });
    return () => {
      stop();
      telemetry.unsubscribe([...STREAMS]);
    };
  }, [telemetry]);

  // The feed is live while the socket is up; stamp both ends of that, so the
  // stale banner can say when it stopped.
  useEffect(() => {
    if (telemetryStatus !== "online") return;
    dispatch({ type: "seen", at: Date.now() });
    return () => dispatch({ type: "seen", at: Date.now() });
  }, [telemetryStatus]);

  const targets = useMemo<readonly StreamName[]>(() => (solo ? [solo] : STREAMS), [solo]);
  const { rows, cursor } = useMemo(() => mergeRows(feed, targets), [feed, targets]);

  const counts = useMemo(() => {
    const result = {} as Record<StreamName, number>;
    for (const name of STREAMS) result[name] = feed.streams[name].entries.length;
    return result;
  }, [feed]);

  const setSolo = useCallback((name: StreamName | null) => {
    setSoloState(name);
    setExpanded(null);
  }, []);

  const toggle = useCallback((key: string) => {
    setExpanded((current) => (current === key ? null : key));
  }, []);

  const pause = useCallback(() => dispatch({ type: "pause" }), []);

  // Resume always re-reads the heads: nothing was read while the hold was on,
  // and a socket that dropped during it lost frames no replay will bring back.
  const resume = useCallback(() => {
    dispatch({ type: "resume" });
    readHeads();
  }, [readHeads]);

  const loadOlder = useCallback(() => {
    if (fetchingOlder) return;
    const wanted = olderTargets(feed, targets);
    if (wanted.length === 0) return;
    setFetchingOlder(true);
    void (async () => {
      const results = await Promise.allSettled(
        wanted.map((name) => fetchStreamPage(name, feed.streams[name].nextBefore)),
      );
      results.forEach((result, i) => {
        if (result.status === "fulfilled") {
          dispatch({ type: "page", stream: wanted[i], page: result.value, mode: "older" });
        }
      });
      setError(readFailure(results, wanted.length));
      setFetchingOlder(false);
    })();
  }, [feed, targets, fetchingOlder]);

  return {
    rows,
    counts,
    live: telemetryStatus === "online",
    paused: feed.paused,
    liveAt: feed.liveAt,
    heldCount: feed.held.length,
    solo,
    setSolo,
    expanded,
    toggle,
    pause,
    resume,
    cursor,
    fetchingOlder,
    loadOlder,
    loaded,
    error,
  };
}
