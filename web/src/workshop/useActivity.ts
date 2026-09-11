import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { errorText } from "@/lib/api";
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
  /**
   * The first head read has finished (well or badly). Pause retires a read that
   * had not finished, so this can stay false with nothing in flight until
   * Resume — which is why the bench's empty note should read "nothing loaded
   * yet" rather than show progress on `!loaded`.
   */
  loaded: boolean;
  /** A head read's failure if there is one, else the last `↑ older` failure. */
  error: string | null;
}

/**
 * The reads that failed. Only the first one's reason reaches the banner: when
 * eight streams fail eight different ways there is one line to say it in, and
 * the count carries the rest.
 */
function rejections(results: PromiseSettledResult<StreamPage>[]): PromiseRejectedResult[] {
  return results.filter((r): r is PromiseRejectedResult => r.status === "rejected");
}

export function useActivity(): Activity {
  const { telemetry, telemetryStatus } = useConnection();
  const [feed, dispatch] = useReducer(feedReducer, undefined, initialFeed);
  const [solo, setSoloState] = useState<StreamName | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [fetchingOlder, setFetchingOlder] = useState(false);
  // Two slots, because the two reads fail independently and neither may speak
  // for the other: a head read that could not reach three streams is still true
  // after `↑ older` succeeds, and an older page that 500s says nothing about
  // the eight chips in the footer.
  const [headError, setHeadError] = useState<string | null>(null);
  const [olderError, setOlderError] = useState<string | null>(null);

  // Which head read the list belongs to. Bumped by every new read, by Pause,
  // and by unmount, so a slow one that lands late can tell it has been
  // superseded and say nothing.
  const readGeneration = useRef(0);

  // Start a read of all eight heads; the caller does not wait for it. Only
  // `dispatch`, `setHeadError` and `setLoaded` are touched, all stable, so this
  // identity never changes and no effect below re-runs because of it.
  const readHeads = useCallback(() => {
    const mine = ++readGeneration.current;
    void (async () => {
      const results = await Promise.allSettled(STREAMS.map((name) => fetchStreamPage(name)));
      // A newer read — or a pause — has spoken for the list since this one left.
      if (mine !== readGeneration.current) return;
      results.forEach((result, i) => {
        if (result.status === "fulfilled") {
          dispatch({ type: "page", stream: STREAMS[i], page: result.value, mode: "head" });
        }
      });
      const bad = rejections(results);
      if (bad.length === 0) {
        setHeadError(null);
        // The endpoint answered for all eight — "could not read further back"
        // is stale.
        setOlderError(null);
      } else {
        setHeadError(
          `${bad.length} of ${STREAMS.length} streams could not be read · ${errorText(bad[0].reason)}`,
        );
      }
      setLoaded(true);
    })();
  }, []);

  useEffect(() => {
    readHeads();
    // Nothing to cancel — a fetch already sent will arrive — but the bench it
    // was for has gone, so retire its generation and let the answer fall away.
    return () => {
      readGeneration.current += 1;
    };
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
  // stale banner can say when it stopped. The cleanup also fires on unmount,
  // where the stamp lands in state being thrown away and costs nothing.
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

  const pause = useCallback(() => {
    // Drop any head read in flight: its pages would land past the hold. Resume
    // re-reads.
    readGeneration.current += 1;
    dispatch({ type: "pause" });
  }, []);

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
    // This attempt speaks for itself; the last one is done being news.
    setOlderError(null);
    void (async () => {
      const results = await Promise.allSettled(
        wanted.map((name) => fetchStreamPage(name, feed.streams[name].nextBefore)),
      );
      results.forEach((result, i) => {
        if (result.status === "fulfilled") {
          dispatch({ type: "page", stream: wanted[i], page: result.value, mode: "older" });
        }
      });
      const bad = rejections(results);
      setOlderError(bad.length === 0 ? null : `Could not read further back · ${errorText(bad[0].reason)}`);
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
    error: headError ?? olderError,
  };
}
