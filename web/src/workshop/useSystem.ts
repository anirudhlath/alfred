import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, errorText } from "@/lib/api";
import { finiteNumber } from "@/lib/format";
import {
  drainDeferred,
  DND_UNCONFIRMED,
  endAuthSession,
  fetchAttention,
  fetchAuthSessions,
  fetchCredentials,
  fetchIntegrations,
  fetchIntegrationStatus,
  healthGrid,
  HOME_SERVICE,
  mintPairingCode,
  putAttention,
  runLibrarian,
  saveCredentials,
  serviceRows,
  setDnd,
  type AttentionDomain,
  type AuthSession,
  type Credential,
  type Health,
  type Integration,
  type ProbeState,
  type ServiceRow,
} from "@/lib/system";
import type { Overview } from "@/lib/types";
import { OVERVIEW_POLL_MS, useOverview } from "@/room/useOverview";

/**
 * How long a read stays fresh. None of these five changes at chat speed — a
 * session list changes when someone signs in, an integration when someone saves
 * a credential — so a trip to Memory and back re-uses what is already in hand.
 */
const STALE_MS = 30_000;

/**
 * How long the last overview keeps speaking for the house. Two polls, not one:
 * a single missed read is a hiccup on a phone radio, and calling the grid
 * unknown for it would make the word meaningless. Two is the point at which
 * nothing is arriving.
 *
 * This is what `error === null` cannot tell us. react-query's default
 * `networkMode: "online"` **pauses** a query with no network rather than
 * failing it — `fetchStatus` becomes `"paused"`, the data is retained and the
 * error stays null — and a server that accepts the socket and never answers
 * leaves the request in flight for ever, because nothing in this tree sets a
 * timeout. Both look exactly like a healthy bench until a clock says otherwise.
 */
export const STALE_AFTER_MS = OVERVIEW_POLL_MS * 2;

/** The query keys, all under one prefix so `REHYDRATE_KEYS` covers them with `["system"]`. */
const SESSIONS_KEY = ["system", "sessions"] as const;
const CREDENTIALS_KEY = ["system", "credentials"] as const;
const INTEGRATIONS_KEY = ["system", "integrations"] as const;
const ATTENTION_KEY = ["system", "attention"] as const;
const statusKey = (name: string) => ["system", "integration-status", name] as const;

export interface Quiet {
  /** What the switch reports — the last thing a read of the overview said. */
  active: boolean;
  /** When the quiet ends, or null for no expiry. Not the same fact as `active`. */
  until: string | null;
  /** How many notifications the queue is holding back. */
  held: number;
  /** The write is in flight; the switch is busy and does not move. */
  setting: boolean;
  /** Why the last write was refused, or why its position could not be confirmed. */
  error: string | null;
  set: (active: boolean, until: string | null) => void;
  /** Open the Held-back sheet. Threaded from the Room, which owns that route. */
  onHeld: () => void;
}

export interface Sessions {
  list: AuthSession[];
  /**
   * Sessions this client has ended, by `session_id` → when the server confirmed
   * it. Not when the button was pressed: task 9 prints this as `ended 21:15 ·
   * applied`, and `applied` is a confirmed-state word.
   */
  ended: Record<string, number>;
  /** Sessions with a `DELETE` in flight, by `session_id`. */
  ending: Record<string, boolean>;
  end: (id: string) => void;
  /** The read's failure, or the last end's. Null while the bench is not showing. */
  error: string | null;
}

/** The registered passkeys. Nothing here removes one — that is a foot-gun with no design. */
export interface Credentials {
  list: Credential[];
  error: string | null;
}

export interface Pairing {
  /** The minted code, until the bench is left. There is no way to re-show it. */
  code: string | null;
  expiresAt: string | null;
  minting: boolean;
  error: string | null;
  mint: () => void;
}

/**
 * What one service's credential save is doing. One entry per name: one service
 * refusing a credential says nothing about the other five.
 */
export interface CredentialSave {
  /** The `PUT`, and the probe that follows it, are in flight. */
  saving: boolean;
  /** When the server took the credentials — the row's `saved · testing`. */
  savedAt: number | null;
  error: string | null;
  /**
   * That refusal was the trusted-network gate, not a bad value. The one write on
   * this bench behind two gates: off the home network every read works and this
   * alone 403s, and the row must say which of the two it was.
   */
  gated: boolean;
}

export interface Integrations {
  list: Integration[];
  /** Each integration's state word, round trip and last failure status, by name. */
  rows: Record<string, ServiceRow>;
  /** One entry per name this client has tried to save credentials for. */
  saves: Record<string, CredentialSave>;
  save: (name: string, values: Record<string, string>) => void;
  error: string | null;
}

export interface Attention {
  domains: AttentionDomain[];
  /** Domains with a write in flight, by name. */
  saving: Record<string, boolean>;
  /** Let the Reflex act on this entity without asking. */
  allow: (domain: string, entity: string) => void;
  /** Take it back — a sticky removal the YAML seed will not undo. */
  ask: (domain: string, entity: string) => void;
  error: string | null;
}

export interface Maintenance {
  /** The Librarian's last pass and what it reviewed, from the overview. */
  last: string | null;
  reviewed: number | null;
  next: string | null;
  /**
   * The chat session's idle timeout in minutes, or **null** until the overview
   * has answered. Never 0: a zero would print `0 minutes`, which is a guess
   * wearing a number, and the row has nothing to say until it is told.
   */
  idleMinutes: number | null;
  /** When this client queued a drain — never evidence the notifier sent anything. */
  drainedAt: number | null;
  /** When this client queued a Librarian run — never evidence that it ran. */
  ranAt: number | null;
  /**
   * Why the last drain was refused, and why the last run was. Two slots, not
   * one: the two controls sit in different sections of the bench, and a single
   * shared string would put the notifier's 503 under the Librarian's button.
   * Each is rendered as its own control's description, never as a loose line at
   * the end of a card, so the reader who pressed the thing is the one told.
   */
  drainError: string | null;
  runError: string | null;
  drain: () => void;
  run: () => void;
}

export interface System {
  /** The raw vitals, for the spend card's own formatters. */
  overview: Overview | undefined;
  health: Health;
  /**
   * When the overview last landed, in epoch ms — null before it ever has. The
   * Health stamp prints it and the offline grid dates itself from it, so both
   * name the same instant.
   */
  readAt: number | null;
  /**
   * This bench's own reads are still landing: an overview arrived inside the
   * last `STALE_AFTER_MS` and the poll is not paused. Not the chat socket's
   * liveness — every number on the grid comes from this poll, so this poll is
   * what the grid is entitled to speak for — and deliberately not
   * `error === null`, which stays true through an aeroplane-mode evening.
   */
  online: boolean;
  quiet: Quiet;
  sessions: Sessions;
  credentials: Credentials;
  integrations: Integrations;
  attention: Attention;
  pairing: Pairing;
  maintenance: Maintenance;
  /**
   * One of this bench's own reads is in flight. Deliberately not the overview's:
   * that one polls every 30 s and is shared with the Room, so anything bound to
   * it would blink on an idle bench for a request nobody made.
   */
  loading: boolean;
  /**
   * The overview's failure — the bench's spine, which every derived card reads.
   * A section that could not be read complains on its own sub-object instead, so
   * one unreadable list does not take the whole screen down with it.
   */
  error: string | null;
}

/**
 * The System bench's one hook: the Room's overview, five reads of its own, and
 * the writes each section owns.
 *
 * `enabled` is `bench === "system"`. It gates every read — the overview
 * included, via the parameter `useOverview` takes for exactly this — because a
 * bench nobody is looking at must not probe every integration in the house. The
 * hook lives in `WorkshopPanel` rather than in the bench, so a queued drain and
 * every save note survive a trip to Triggers.
 *
 * `onHeld` opens the Held-back sheet, which is the Room's route rather than the
 * Workshop's; Quiet's third row is the only thing on this bench that leaves it.
 */
export function useSystem(enabled: boolean, onHeld: () => void): System {
  const queryClient = useQueryClient();

  const [settingDnd, setSettingDnd] = useState(false);
  const [dndError, setDndError] = useState<string | null>(null);
  const [ended, setEnded] = useState<Record<string, number>>({});
  const [ending, setEnding] = useState<Record<string, boolean>>({});
  const [endError, setEndError] = useState<string | null>(null);
  const [saves, setSaves] = useState<Record<string, CredentialSave>>({});
  const [attentionSaving, setAttentionSaving] = useState<Record<string, boolean>>({});
  const [attentionError, setAttentionError] = useState<string | null>(null);
  const [pairing, setPairing] = useState<{ code: string; expires_at: string } | null>(null);
  const [minting, setMinting] = useState(false);
  const [pairingError, setPairingError] = useState<string | null>(null);
  const [drainedAt, setDrainedAt] = useState<number | null>(null);
  const [ranAt, setRanAt] = useState<number | null>(null);
  const [drainError, setDrainError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  // ── The supersession guard, shared by all six controls ─────────────────────

  // Every request this hook has sent, counted. Per hook rather than per control,
  // so no two attempts ever share a number — which is what makes dropping a
  // control's entry safe.
  const sent = useRef(0);
  // The attempt each control is waiting on, by its own key: one per switch, one
  // per session row, one per integration, one per domain. An answer whose number
  // is no longer here is no longer that control's news — a later tap replaced it.
  const waiting = useRef(new Map<string, number>());
  // Aborts whatever the unmounting hook still has in flight. Only the pairing
  // mint is on it: it is the one write whose *success* does harm after the
  // caller has gone, because the code it returns cannot be shown to anyone.
  const leaving = useRef(new AbortController());

  useEffect(() => {
    // A fresh controller per mount, not the one the ref was born with: React
    // runs this effect twice under StrictMode, and re-using an already-aborted
    // signal would make every mint on the second mount fail before it was sent.
    const controller = new AbortController();
    const claims = waiting.current;
    leaving.current = controller;
    return () => {
      // Clearing the map is also what retires every claim still outstanding:
      // a guard whose key is no longer here answers false, so an answer landing
      // after `WorkshopPanel` unmounts writes no state. No separate `mounted`
      // flag — a second condition that can never disagree with this one is a
      // branch no test can tell apart from its absence.
      claims.clear();
      controller.abort();
    };
  }, []);

  /**
   * Claim a control for this attempt. The returned guard is true only while this
   * is still the newest attempt on that control — every state write below goes
   * behind it, so a slow first answer can never speak for a faster second one,
   * and an unmounted tree (whose claims the cleanup above clears) can speak for
   * nothing at all.
   */
  const claim = useCallback((key: string): (() => boolean) => {
    const mine = ++sent.current;
    waiting.current.set(key, mine);
    return () => waiting.current.get(key) === mine;
  }, []);

  // ── The reads ──────────────────────────────────────────────────────────────

  // The Room's poll, shared rather than repeated: this is the same key the
  // status line and the Workshop header watch, and a second query for it would
  // double the requests for as long as the layer is up.
  const overviewQuery = useOverview(enabled);
  const overview = overviewQuery.data;
  const refetchOverview = overviewQuery.refetch;

  // ── Is any of this still true? ─────────────────────────────────────────────

  // `dataUpdatedAt` is 0 until the first answer, which is the never-read case
  // and not the went-stale one: the grid has its own `not read yet` wording for
  // that, and `unknown since --:--` is not a time.
  const readAt = overviewQuery.dataUpdatedAt === 0 ? null : overviewQuery.dataUpdatedAt;

  const [aged, setAged] = useState(false);

  useEffect(() => {
    // One timer that fires at the moment the last answer stops being evidence,
    // rather than a clock ticking every second to ask the same question of an
    // arithmetic that cannot change in between. Re-armed on each fresh read,
    // which is also what takes the flag back down.
    if (!enabled || readAt === null) return;
    const timer = setTimeout(
      () => setAged(true),
      Math.max(0, readAt + STALE_AFTER_MS - Date.now()),
    );
    return () => clearTimeout(timer);
  }, [enabled, readAt]);

  // Adjusted during render rather than in that effect, which cannot lower the
  // flag without painting one frame of a stale grid over a read that has just
  // landed. `readAt` is the input it follows.
  const [agedFor, setAgedFor] = useState(readAt);
  if (agedFor !== readAt) {
    setAgedFor(readAt);
    setAged(false);
  }

  const online =
    enabled &&
    readAt !== null &&
    !aged &&
    // Paused is react-query's word for "there is no network to try on". It
    // keeps the data and raises no error, so nothing else here would notice.
    overviewQuery.fetchStatus !== "paused";

  // None of the five carries a `refetchInterval`. A session list, a passkey, an
  // integration and an attention set change when a person changes them, and the
  // moments this client has reason to doubt one are the ones it caused itself.
  const sessionsQuery = useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: fetchAuthSessions,
    enabled,
    staleTime: STALE_MS,
  });

  const credentialsQuery = useQuery({
    queryKey: CREDENTIALS_KEY,
    queryFn: fetchCredentials,
    enabled,
    staleTime: STALE_MS,
  });

  const integrationsQuery = useQuery({
    queryKey: INTEGRATIONS_KEY,
    queryFn: fetchIntegrations,
    enabled,
    staleTime: STALE_MS,
  });

  const attentionQuery = useQuery({
    queryKey: ATTENTION_KEY,
    queryFn: fetchAttention,
    enabled,
    staleTime: STALE_MS,
    // 503 here is "the store is down", which is a fact to report rather than a
    // question to ask twice — the same reading the setup gate takes of the same
    // endpoint, and the one this module's own `fetchAttention` documents.
    retry: false,
  });

  const list = useMemo(() => integrationsQuery.data ?? [], [integrationsQuery.data]);

  // One probe per integration, each on its own key so a service that cannot be
  // reached costs its own row rather than the section. Retries are left at the
  // app's policy on purpose: a *sick* service answers 200 with `healthy: false`,
  // so a thrown probe is a transport failure — the proxy reloading under us —
  // and with no interval and a 30 s staleTime, not retrying it would leave the
  // row blaming a service for a hiccup in front of it.
  const statusQueries = useQueries({
    queries: list.map((entry) => ({
      queryKey: statusKey(entry.name),
      queryFn: () => fetchIntegrationStatus(entry.name),
      enabled,
      staleTime: STALE_MS,
    })),
  });

  // `useQueries` hands back a fresh array every render, so this is derived in the
  // render body rather than memoised: any dependency list honest enough to cover
  // it would have to be rebuilt from the probe states by hand, and the work is a
  // loop over a handful of integrations.
  const probes: (ProbeState | undefined)[] = statusQueries.map((probe) => ({
    data: probe.data,
    isPending: probe.isPending,
    isError: probe.isError,
    status: probe.error instanceof ApiError ? probe.error.status : null,
  }));
  const rows = serviceRows(list, probes, saves);

  const health = healthGrid({
    overview,
    // Only once the registry has answered is "not registered" a fact about the
    // house rather than a claim made ahead of the read that would settle it.
    registryRead: integrationsQuery.isSuccess,
    home: probes[list.findIndex((entry) => entry.name === HOME_SERVICE)],
  });

  // ── Quiet ──────────────────────────────────────────────────────────────────

  const dnd = overview?.dnd;

  const setQuiet = useCallback(
    (active: boolean, until: string | null) => {
      const fresh = claim("dnd");
      setSettingDnd(true);
      setDndError(null);
      void setDnd(active, until).then(
        async () => {
          if (!fresh()) return;
          // Busy tracks *this write*, and nothing else. It clears the moment the
          // write answers — tying it to the read below would leave the switch
          // spinning for ever on a request that has no timeout anywhere in this
          // tree, which is a worse lie than a switch that has not moved yet.
          setSettingDnd(false);
          // The route wrote (or deleted) the Redis key before it answered, so
          // the overview read behind it reports the new position. The switch
          // moves on *that* read and not on this client's echo of what it asked
          // for: one source of truth, and no stale claim to resurrect the old
          // position the next time something else moves the switch.
          const { error } = await refetchOverview();
          if (!fresh()) return;
          // The write landed and the confirmation did not. Saying nothing would
          // leave a switch that visibly snapped back with no explanation on it.
          if (error) setDndError(DND_UNCONFIRMED);
        },
        (error: unknown) => {
          if (!fresh()) return;
          setSettingDnd(false);
          setDndError(errorText(error));
        },
      );
    },
    [claim, refetchOverview],
  );

  // ── Sessions ───────────────────────────────────────────────────────────────

  const end = useCallback(
    (id: string) => {
      const fresh = claim(`session:${id}`);
      setEnding((current) => ({ ...current, [id]: true }));
      setEndError(null);
      const settle = () =>
        setEnding((current) => {
          const next = { ...current };
          delete next[id];
          return next;
        });
      void endAuthSession(id).then(
        () => {
          if (!fresh()) return;
          // Stamped here, where the server confirmed it — not at the tap. The
          // row prints this as `ended 21:15 · applied`, and a client that
          // claimed `applied` before the answer would have to take it back.
          setEnded((current) => ({ ...current, [id]: Date.now() }));
          settle();
          // Ending your own clears the cookie, so this re-read is the request
          // that 401s and raises the Expired gate. That is `api`'s job; the
          // bench stays up behind it holding what it was last told.
          void queryClient.invalidateQueries({ queryKey: SESSIONS_KEY });
        },
        (error: unknown) => {
          if (!fresh()) return;
          settle();
          setEndError(errorText(error));
        },
      );
    },
    [claim, queryClient],
  );

  // ── Connected services ─────────────────────────────────────────────────────

  const save = useCallback(
    (name: string, values: Record<string, string>) => {
      const fresh = claim(`save:${name}`);
      setSaves((current) => ({
        ...current,
        [name]: { saving: true, savedAt: null, error: null, gated: false },
      }));
      void saveCredentials(name, values).then(
        async () => {
          if (!fresh()) return;
          // Built fresh rather than spread over whatever is there: an entry left
          // by an earlier attempt belongs to a different request.
          setSaves((current) => ({
            ...current,
            [name]: { saving: true, savedAt: Date.now(), error: null, gated: false },
          }));
          // Saved is not working. The round trip the row promises is this probe,
          // and only its answer turns `saved · testing` into `ok` or `failed`.
          //
          // `invalidateQueries`, not `refetchQueries`: while the bench is open
          // the two do the same thing — an active query refetches either way,
          // whatever `staleTime` says. They part company when the reader walks
          // away with a save in flight. Both skip a disabled query, but only the
          // invalidate leaves a mark on it, so the probe runs when they come
          // back; a refetch would drop it on the floor and the row would show
          // the answer from before the credentials changed, inside the 30 s
          // window, with nothing on its way to correct it.
          await queryClient.invalidateQueries({ queryKey: statusKey(name) });
          if (!fresh()) return;
          setSaves((current) => ({
            ...current,
            [name]: { ...current[name], saving: false },
          }));
          // `configured` moved, and the form's placeholders are drawn from it.
          void queryClient.invalidateQueries({ queryKey: INTEGRATIONS_KEY });
        },
        (error: unknown) => {
          if (!fresh()) return;
          setSaves((current) => ({
            ...current,
            [name]: {
              saving: false,
              savedAt: null,
              error: errorText(error),
              // The two gates answer with a status a bad value never does: 403
              // is the network, and the section says so rather than leaving the
              // reader retyping a password that was fine.
              gated: error instanceof ApiError && error.status === 403,
            },
          }));
        },
      );
    },
    [claim, queryClient],
  );

  // ── Reflex attention ───────────────────────────────────────────────────────

  const writeAttention = useCallback(
    (domain: string, allow: string[], ask: string[]) => {
      const fresh = claim(`attention:${domain}`);
      setAttentionSaving((current) => ({ ...current, [domain]: true }));
      setAttentionError(null);
      const settle = () =>
        setAttentionSaving((current) => {
          const next = { ...current };
          delete next[domain];
          return next;
        });
      void putAttention(domain, allow, ask).then(
        (updated) => {
          if (!fresh()) return;
          // The route reads the domain back inside its own guard before it
          // answers, so what came back *is* the stored set — a second GET would
          // only ask again for what this reply already carried. Appended when it
          // is not already cached: a domain the Reflex has only just observed is
          // not in a list read before it existed.
          queryClient.setQueryData<AttentionDomain[]>(ATTENTION_KEY, (current) => {
            const cached = current ?? [];
            return cached.some((row) => row.domain === updated.domain)
              ? cached.map((row) => (row.domain === updated.domain ? updated : row))
              : [...cached, updated];
          });
          settle();
        },
        (error: unknown) => {
          if (!fresh()) return;
          setAttentionError(errorText(error));
          settle();
          // The route writes one entity at a time and says outright that the
          // writes are not transactional, so a refusal can leave part of the
          // change applied. What is on screen is no longer evidence; ask again.
          void queryClient.invalidateQueries({ queryKey: ATTENTION_KEY });
        },
      );
    },
    [claim, queryClient],
  );

  const allow = useCallback(
    (domain: string, entity: string) => writeAttention(domain, [entity], []),
    [writeAttention],
  );
  const ask = useCallback(
    (domain: string, entity: string) => writeAttention(domain, [], [entity]),
    [writeAttention],
  );

  // ── Devices & identity ─────────────────────────────────────────────────────

  const mint = useCallback(() => {
    const fresh = claim("pairing");
    setMinting(true);
    setPairingError(null);
    void mintPairingCode(leaving.current.signal).then(
      (minted) => {
        // Without the guard, two mints answering out of order would leave the
        // older, shorter-lived code on screen — and the newer one live on the
        // server with nothing showing it.
        if (!fresh()) return;
        setPairing(minted);
        setMinting(false);
      },
      (error: unknown) => {
        if (!fresh()) return;
        setPairingError(errorText(error));
        setMinting(false);
      },
    );
  }, [claim]);

  // A code is shown until the bench is left, and there is no way to re-show it:
  // one left standing on a screen the reader has walked away from is a secret
  // with nobody watching it. Adjusted during render — the pattern React
  // documents for resetting state when an input changes — rather than in an
  // effect, which would paint the code once more on the frame after the bench
  // closed and would be a cascading render besides.
  const [shownFor, setShownFor] = useState(enabled);
  if (shownFor !== enabled) {
    setShownFor(enabled);
    if (!enabled) {
      setPairing(null);
      setPairingError(null);
    }
  }

  // ── Maintenance ────────────────────────────────────────────────────────────

  const queueMaintenance = useCallback(
    (
      key: string,
      send: () => Promise<void>,
      stamp: (at: number) => void,
      complain: (why: string | null) => void,
    ) => {
      const fresh = claim(key);
      complain(null);
      void send().then(
        // Stamped only once the server has taken it, and never as evidence the
        // work happened: both routes publish an internal action and return.
        () => void (fresh() && stamp(Date.now())),
        (error: unknown) => void (fresh() && complain(errorText(error))),
      );
    },
    [claim],
  );

  const drain = useCallback(
    () => queueMaintenance("drain", drainDeferred, setDrainedAt, setDrainError),
    [queueMaintenance],
  );
  const run = useCallback(
    () => queueMaintenance("librarian", runLibrarian, setRanAt, setRunError),
    [queueMaintenance],
  );

  // ── What the bench reads ───────────────────────────────────────────────────

  const quietActive = dnd?.active ?? false;
  const librarian = overview?.librarian;
  const idle = finiteNumber(overview?.session?.idle_minutes);

  /** A section's own failure, and nothing at all from a bench nobody is looking at. */
  const complaint = (read: Error | null, wrote: string | null): string | null => {
    if (!enabled) return null;
    return read !== null ? errorText(read) : wrote;
  };

  return {
    overview,
    health,
    readAt,
    online,
    quiet: {
      active: quietActive,
      // `until` belongs to an active quiet. A cleared one carrying a stale
      // instant would otherwise print an expiry for a switch that is off.
      until: quietActive ? (dnd?.until ?? null) : null,
      held: overview?.counts.deferred ?? 0,
      setting: settingDnd,
      error: enabled ? dndError : null,
      set: setQuiet,
      onHeld,
    },
    sessions: {
      list: sessionsQuery.data ?? [],
      ended,
      ending,
      end,
      error: complaint(sessionsQuery.error, endError),
    },
    credentials: {
      list: credentialsQuery.data ?? [],
      error: complaint(credentialsQuery.error, null),
    },
    integrations: {
      list,
      rows,
      saves,
      save,
      error: complaint(integrationsQuery.error, null),
    },
    attention: {
      domains: attentionQuery.data ?? [],
      saving: attentionSaving,
      allow,
      ask,
      error: complaint(attentionQuery.error, attentionError),
    },
    pairing: {
      code: pairing?.code ?? null,
      expiresAt: pairing?.expires_at ?? null,
      minting,
      error: enabled ? pairingError : null,
      mint,
    },
    maintenance: {
      last: librarian?.last_run_at ?? null,
      reviewed: librarian?.reviewed ?? null,
      next: librarian?.next_run_at ?? null,
      idleMinutes: idle !== null && idle > 0 ? idle : null,
      drainedAt,
      ranAt,
      drainError: enabled ? drainError : null,
      runError: enabled ? runError : null,
      drain,
      run,
    },
    loading:
      sessionsQuery.isFetching ||
      credentialsQuery.isFetching ||
      integrationsQuery.isFetching ||
      attentionQuery.isFetching ||
      statusQueries.some((probe) => probe.isFetching),
    error: complaint(overviewQuery.error, null),
  };
}
