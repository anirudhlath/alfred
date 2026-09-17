import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { errorText } from "@/lib/api";
import { feedReducer, initialFeed, mergeRows, olderTargets, type FeedRow } from "@/lib/feed";
import { onVisible } from "@/lib/lifecycle";
import { fetchStreamPage, isStreamName, STREAMS, type StreamName } from "@/lib/streams";
import type { StreamPage } from "@/lib/types";
import { useConnection } from "@/shell/ConnectionProvider";

export interface Activity {
  /**
   * What the list shows: the solo stream or all eight, newest first, from the
   * horizon forward. Empty while another bench is showing — the merge is this
   * bench's own work and nobody else reads it (see `enabled` below). The
   * frames themselves are not dropped; they are still in `feed`, and the rows
   * are there again on the frame the reader comes back.
   */
  rows: FeedRow[];
  /**
   * Entries *held* per stream — the chips' numbers. This is loaded depth, not
   * visible rows: with all streams shown, a stream paged deeper than the shared
   * horizon holds more entries than the list shows (solo it to see them all).
   */
  counts: Record<StreamName, number>;
  /**
   * Per stream: a page of it has been read, so an empty list means the stream
   * is empty (`feed.ts`, `StreamFeed.loaded`). False both before the first
   * read settles *and* after one that failed — `loaded` below is what tells
   * those two apart, and the bench needs both to choose between "nothing
   * loaded yet", "nothing has been written" and "could not be read". Kept off
   * `error`, which carries one reason for all eight and names no stream.
   */
  streamLoaded: Record<StreamName, boolean>;
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
  /** What `↑ older` reads before; null when there is nothing older to fetch, and while another bench is showing. */
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
 * What the merge hands back while nobody is looking at this bench. One frozen
 * object rather than a fresh `{ rows: [], cursor: null }` each render: it is a
 * `useMemo` result, and a new identity every render would defeat the memo it
 * is the value of.
 */
const NOT_SHOWING: { rows: FeedRow[]; cursor: string | null } = { rows: [], cursor: null };

/**
 * The reads that failed. Only the first one's reason reaches the banner: when
 * eight streams fail eight different ways there is one line to say it in, and
 * the count carries the rest.
 */
function rejections(results: PromiseSettledResult<StreamPage>[]): PromiseRejectedResult[] {
  return results.filter((r): r is PromiseRejectedResult => r.status === "rejected");
}

/**
 * The Activity bench's one hook.
 *
 * `enabled` is `bench === "activity"`. It gates the two expensive halves and
 * neither of the cheap ones: no head reads and no merge while another bench is
 * showing, but the socket stays subscribed and every frame still lands in the
 * feed. That split is deliberate, and it is what the Workshop's header needs —
 * `live`, `liveAt`, `paused` and `heldCount` are on screen on all four benches,
 * and a held count that stopped counting while the reader was in Memory would
 * be a number about the server made from a client that had stopped listening.
 * What is skipped is `mergeRows`, which walks eight streams of up to
 * `MAX_PER_STREAM` rows each — uncapped once `↑ older` has been pressed — and
 * whose only reader is the bench itself.
 */
export function useActivity(enabled: boolean): Activity {
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

  // The eight heads, read whenever this bench is the one showing and the feed
  // is not held. Three moments in one effect, because they are one rule: the
  // Workshop opening on Activity, the reader coming back to it from another
  // bench, and Resume — which is why neither `resume` nor `pause` does any of
  // this itself any more.
  //
  // Not while another bench is showing: eight reads for a list nobody can see,
  // and the reader who arrives gets a fresh one anyway. Not while paused
  // either — the reducer merges a head page straight into the entries whatever
  // `paused` says (pages are not live frames), so reading here would slide rows
  // into a list the handoff promises will not move until Resume.
  useEffect(() => {
    if (!enabled || feed.paused) return;
    readHeads();
    // Nothing to cancel — a fetch already sent will arrive — but the bench it
    // was for has gone, so retire its generation and let the answer fall away.
    // This is also what retires a read when the reader pauses or walks off the
    // bench mid-flight.
    return () => {
      readGeneration.current += 1;
    };
  }, [enabled, feed.paused, readHeads]);

  // Head pages on every return to the foreground: the socket replays nothing,
  // so whatever happened while the app was suspended is only on the server
  // (spec §10). Gated on the same two facts as the read above, and for the same
  // two reasons — a phone brought back into the light while the reader is on
  // System must not read eight streams for a bench that is not there.
  useEffect(() => {
    if (!enabled || feed.paused) return;
    return onVisible(readHeads);
  }, [enabled, feed.paused, readHeads]);

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
  // The one piece of work a frame costs that is worth skipping. Every live
  // frame allocates a new feed — the paused branch of the reducer does too —
  // so this memo recomputes once per frame, and at 2 ev/s with `↑ older`
  // pressed that is a merge of an uncapped list twice a second for a bench
  // nobody is looking at. Skipped rather than computed and thrown away.
  const { rows, cursor } = useMemo(
    () => (enabled ? mergeRows(feed, targets) : NOT_SHOWING),
    [enabled, feed, targets],
  );

  const counts = useMemo(() => {
    const result = {} as Record<StreamName, number>;
    for (const name of STREAMS) result[name] = feed.streams[name].entries.length;
    return result;
  }, [feed]);

  const streamLoaded = useMemo(() => {
    const result = {} as Record<StreamName, boolean>;
    for (const name of STREAMS) result[name] = feed.streams[name].loaded;
    return result;
  }, [feed]);

  const setSolo = useCallback((name: StreamName | null) => {
    setSoloState(name);
    setExpanded(null);
  }, []);

  const toggle = useCallback((key: string) => {
    setExpanded((current) => (current === key ? null : key));
  }, []);

  // Both are one dispatch each. The read that follows a Resume, and the
  // retiring of one already in flight when the hold goes on, belong to the
  // effect above: it runs on exactly the transitions these two cause, and two
  // owners for one read is how a Resume ends up fetching eight streams twice.
  const pause = useCallback(() => dispatch({ type: "pause" }), []);
  const resume = useCallback(() => dispatch({ type: "resume" }), []);

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
    streamLoaded,
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
