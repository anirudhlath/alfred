import { useCallback, useMemo, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, errorText } from "@/lib/api";
import {
  drainDeferred,
  endAuthSession,
  fetchAttention,
  fetchAuthSessions,
  fetchCredentials,
  fetchIntegrations,
  fetchIntegrationStatus,
  HOME_SERVICE,
  mintPairingCode,
  putAttention,
  runLibrarian,
  saveCredentials,
  setDnd,
  type AttentionDomain,
  type AuthSession,
  type Credential,
  type Integration,
  type ServiceState,
} from "@/lib/system";
import { rateText, useOverview } from "@/room/useOverview";
import type { Overview } from "@/lib/types";

/**
 * How long a read stays fresh. None of these five changes at chat speed — a
 * session list changes when someone signs in, an integration when someone saves
 * a credential — so a trip to Memory and back re-uses what is already in hand.
 */
const STALE_MS = 30_000;

/** The query keys, all under one prefix so `REHYDRATE_KEYS` covers them with `["system"]`. */
const SESSIONS_KEY = ["system", "sessions"] as const;
const CREDENTIALS_KEY = ["system", "credentials"] as const;
const INTEGRATIONS_KEY = ["system", "integrations"] as const;
const ATTENTION_KEY = ["system", "attention"] as const;
const OVERVIEW_KEY = ["overview"] as const;
const statusKey = (name: string) => ["system", "integration-status", name] as const;

/**
 * One stat card on the health grid. `alive` is a flag rather than a word the
 * view matches on: a card that dims because the string it was handed happened
 * not to read `alive` is a screen one rename away from lying.
 */
export interface HealthCell {
  value: string;
  note: string;
  alive: boolean;
}

/** The four cards, in the order the grid draws them. No GPU figure, no service count. */
export interface Health {
  bus: HealthCell;
  reflex: HealthCell;
  rate: HealthCell;
  home: HealthCell;
}

export interface Quiet {
  /** What the switch reports — the server's answer, or this client's confirmed write. */
  active: boolean;
  /** When the quiet ends, or null for no expiry. Not the same fact as `active`. */
  until: string | null;
  /** How many notifications the queue is holding back. */
  held: number;
  /** A write is in flight; the switch is busy and does not move. */
  setting: boolean;
  /** Why the last write was refused, if it was. */
  error: string | null;
  set: (active: boolean, until: string | null) => void;
}

export interface Sessions {
  list: AuthSession[];
  /**
   * Sessions this client has ended, by `session_id` → when. Kept past the
   * re-read that removes the row: it is what this client did, not a guess at
   * what the list now holds.
   */
  ended: Record<string, number>;
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
   * That refusal was the trusted-network gate, not a bad value. The one write
   * on this bench behind two gates: off the home network every read works and
   * this alone 403s, and the row must say which of the two it was.
   */
  gated: boolean;
}

export interface Integrations {
  list: Integration[];
  /** Each integration's state word, by name. */
  state: Record<string, ServiceState>;
  /** The last probe's round trip in ms, by name; null when it never answered. */
  latency: Record<string, number | null>;
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
  /** The chat session's idle timeout in minutes, so the row never guesses it. */
  idleMinutes: number;
  /** When this client queued a drain — never evidence the notifier sent anything. */
  drainedAt: number | null;
  /** When this client queued a Librarian run — never evidence that it ran. */
  ranAt: number | null;
  error: string | null;
  drain: () => void;
  run: () => void;
}

export interface System {
  /** The raw vitals, for the spend card's own formatters. */
  overview: Overview | undefined;
  health: Health;
  quiet: Quiet;
  sessions: Sessions;
  credentials: Credentials;
  integrations: Integrations;
  attention: Attention;
  pairing: Pairing;
  maintenance: Maintenance;
  /** Some read is in flight. */
  loading: boolean;
  /**
   * The overview's failure — the bench's spine, which every derived card reads.
   * A section that could not be read complains on its own sub-object instead,
   * so one unreadable list does not take the whole screen down with it.
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
 * hook lives in `WorkshopPanel` rather than in the bench, so a minted pairing
 * code, a queued drain and every save note survive a trip to Triggers.
 */
export function useSystem(enabled: boolean): System {
  const queryClient = useQueryClient();

  const [settingDnd, setSettingDnd] = useState(false);
  const [dndError, setDndError] = useState<string | null>(null);
  const [ended, setEnded] = useState<Record<string, number>>({});
  const [endError, setEndError] = useState<string | null>(null);
  const [saves, setSaves] = useState<Record<string, CredentialSave>>({});
  const [attentionSaving, setAttentionSaving] = useState<Record<string, boolean>>({});
  const [attentionError, setAttentionError] = useState<string | null>(null);
  const [pairing, setPairing] = useState<{ code: string; expires_at: string } | null>(null);
  const [minting, setMinting] = useState(false);
  const [pairingError, setPairingError] = useState<string | null>(null);
  const [drainedAt, setDrainedAt] = useState<number | null>(null);
  const [ranAt, setRanAt] = useState<number | null>(null);
  const [maintenanceError, setMaintenanceError] = useState<string | null>(null);

  // The Room's poll, shared rather than repeated: this is the same key the
  // status line and the Workshop header watch, and a second query for it would
  // double the requests for as long as the layer is up.
  const overviewQuery = useOverview(enabled);
  const overview = overviewQuery.data;

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
  });

  const list = useMemo(() => integrationsQuery.data ?? [], [integrationsQuery.data]);

  // One probe per integration, each on its own key so a service that cannot be
  // reached costs its own row rather than the section.
  const statusQueries = useQueries({
    queries: list.map((entry) => ({
      queryKey: statusKey(entry.name),
      queryFn: () => fetchIntegrationStatus(entry.name),
      enabled,
      staleTime: STALE_MS,
      // A probe that fails is an answer about the service, not a failure to
      // reach the house: retrying it would only ask the same question twice.
      retry: false,
    })),
  });

  // ── Quiet ──────────────────────────────────────────────────────────────────

  const dnd = overview?.dnd;

  const setQuiet = useCallback(
    (active: boolean, until: string | null) => {
      setSettingDnd(true);
      setDndError(null);
      void setDnd(active, until).then(
        async () => {
          // Unlike a trigger control, this one may move the switch: the route
          // writes (or deletes) the Redis key itself before it answers, so the
          // overview read behind this invalidate reports the new position.
          //
          // The switch moves on *that* read and not on this client's echo of
          // what it asked for. One source of truth, and no claim to retire by
          // hand — a stale one would resurrect the old position the next time
          // something else (a meeting in the calendar) moved the switch. The
          // control stays busy until the read lands, which is the honest length
          // of the gap rather than a hidden one.
          await queryClient.invalidateQueries({ queryKey: OVERVIEW_KEY });
          setSettingDnd(false);
        },
        (error: unknown) => {
          setDndError(errorText(error));
          setSettingDnd(false);
        },
      );
    },
    [queryClient],
  );

  // ── Sessions ───────────────────────────────────────────────────────────────

  const end = useCallback(
    (id: string) => {
      const at = Date.now();
      setEnded((current) => ({ ...current, [id]: at }));
      setEndError(null);
      void endAuthSession(id).then(
        () => {
          // Ending your own clears the cookie, so this re-read is the request
          // that 401s and raises the Expired gate. That is `api`'s job; the
          // bench stays up behind it holding what it was last told.
          void queryClient.invalidateQueries({ queryKey: SESSIONS_KEY });
        },
        (error: unknown) => {
          // The row did not end. A note saying it did would outlive its evidence.
          setEnded((current) => {
            const next = { ...current };
            delete next[id];
            return next;
          });
          setEndError(errorText(error));
        },
      );
    },
    [queryClient],
  );

  // ── Connected services ─────────────────────────────────────────────────────

  const save = useCallback(
    (name: string, values: Record<string, string>) => {
      setSaves((current) => ({
        ...current,
        [name]: { saving: true, savedAt: null, error: null, gated: false },
      }));
      void saveCredentials(name, values).then(
        async () => {
          setSaves((current) => ({
            ...current,
            [name]: { saving: true, savedAt: Date.now(), error: null, gated: false },
          }));
          // Saved is not working. The round trip the row promises is this probe,
          // and only its answer turns `saved · testing` into `ok` or `failed`.
          await queryClient.refetchQueries({ queryKey: statusKey(name) });
          setSaves((current) => ({ ...current, [name]: { ...current[name], saving: false } }));
          // `configured` moved, and the form's placeholders are drawn from it.
          void queryClient.invalidateQueries({ queryKey: INTEGRATIONS_KEY });
        },
        (error: unknown) => {
          setSaves((current) => ({
            ...current,
            [name]: {
              saving: false,
              savedAt: null,
              error: errorText(error),
              // The two gates answer with the same status as a bad value never
              // does: 403 is the network, and the section says so rather than
              // leaving the reader retyping a password that was fine.
              gated: error instanceof ApiError && error.status === 403,
            },
          }));
        },
      );
    },
    [queryClient],
  );

  // Derived in the render body rather than memoised: `useQueries` hands back a
  // fresh array on every render, so any dependency list honest enough to cover
  // it would have to be rebuilt from the probe states by hand — and a loop over
  // a handful of integrations is cheaper than that key ever was.
  const state: Record<string, ServiceState> = {};
  const latency: Record<string, number | null> = {};
  list.forEach((entry, index) => {
    const probe = statusQueries[index];
    latency[entry.name] = probe?.data?.latency_ms ?? null;
    state[entry.name] = serviceState(entry, probe, saves[entry.name]);
  });

  // ── Reflex attention ───────────────────────────────────────────────────────

  const writeAttention = useCallback(
    (domain: string, allow: string[], ask: string[]) => {
      setAttentionSaving((current) => ({ ...current, [domain]: true }));
      setAttentionError(null);
      const done = () =>
        setAttentionSaving((current) => {
          const next = { ...current };
          delete next[domain];
          return next;
        });
      void putAttention(domain, allow, ask).then(
        (updated) => {
          // The route reads the domain back inside its own guard before it
          // answers, so what came back *is* the stored set — a second GET would
          // only ask again for what this reply already carried.
          queryClient.setQueryData<AttentionDomain[]>(ATTENTION_KEY, (current) =>
            (current ?? []).map((row) => (row.domain === updated.domain ? updated : row)),
          );
          done();
        },
        (error: unknown) => {
          setAttentionError(errorText(error));
          done();
        },
      );
    },
    [queryClient],
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
    setMinting(true);
    setPairingError(null);
    void mintPairingCode().then(
      (minted) => {
        setPairing(minted);
        setMinting(false);
      },
      (error: unknown) => {
        setPairingError(errorText(error));
        setMinting(false);
      },
    );
  }, []);

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
    (send: () => Promise<void>, stamp: (at: number) => void) => {
      setMaintenanceError(null);
      void send().then(
        // Stamped only once the server has taken it, and never as evidence the
        // work happened: both routes publish an internal action and return.
        () => stamp(Date.now()),
        (error: unknown) => setMaintenanceError(errorText(error)),
      );
    },
    [],
  );

  const drain = useCallback(
    () => queueMaintenance(drainDeferred, setDrainedAt),
    [queueMaintenance],
  );
  const run = useCallback(() => queueMaintenance(runLibrarian, setRanAt), [queueMaintenance]);

  // ── The health grid ────────────────────────────────────────────────────────

  const health: Health = ((): Health => {
    const connected = overview?.redis.connected === true;
    const streamCount = Object.keys(overview?.streams ?? {}).length;
    const reflex = overview?.reflex;
    const lastMs = reflex?.last_ms ?? null;
    const streams = overview?.streams;

    const home = list.find((entry) => entry.name === HOME_SERVICE);
    const homeLatency = home === undefined ? null : latency[home.name];

    return {
      bus: {
        value: connected ? "alive" : "unknown",
        note: `bus · redis · ${streamCount} streams`,
        alive: connected,
      },
      reflex: {
        value: lastMs === null ? "—" : `${Math.round(lastMs)} ms`,
        note: reflex?.model ? `reflex · ${reflex.model}` : "reflex · no model reported",
        alive: lastMs !== null,
      },
      rate: {
        value: rateText(overview),
        note: "event rate · 5-minute mean",
        alive: streams !== undefined && Object.keys(streams).length > 0,
      },
      home: {
        value: home === undefined ? "—" : state[home.name],
        note:
          home === undefined
            ? "home assistant · not registered"
            : homeLatency === null
              ? "home assistant · no round trip measured"
              : `home assistant · ${Math.round(homeLatency)} ms`,
        alive: home !== undefined && state[home.name] === "ok",
      },
    };
  })();

  const quietActive = dnd?.active ?? false;

  const librarian = overview?.librarian;
  const readError = enabled && overviewQuery.error ? errorText(overviewQuery.error) : null;

  return {
    overview,
    health,
    quiet: {
      active: quietActive,
      until: quietActive ? (dnd?.until ?? null) : null,
      held: overview?.counts.deferred ?? 0,
      setting: settingDnd,
      error: dndError,
      set: setQuiet,
    },
    sessions: {
      list: sessionsQuery.data ?? [],
      ended,
      end,
      error: enabled ? (sessionsQuery.error ? errorText(sessionsQuery.error) : endError) : null,
    },
    credentials: {
      list: credentialsQuery.data ?? [],
      error: enabled && credentialsQuery.error ? errorText(credentialsQuery.error) : null,
    },
    integrations: {
      list,
      state,
      latency,
      saves,
      save,
      error: enabled && integrationsQuery.error ? errorText(integrationsQuery.error) : null,
    },
    attention: {
      domains: attentionQuery.data ?? [],
      saving: attentionSaving,
      allow,
      ask,
      error: enabled
        ? attentionQuery.error
          ? errorText(attentionQuery.error)
          : attentionError
        : null,
    },
    pairing: {
      code: pairing?.code ?? null,
      expiresAt: pairing?.expires_at ?? null,
      minting,
      error: pairingError,
      mint,
    },
    maintenance: {
      last: librarian?.last_run_at ?? null,
      reviewed: librarian?.reviewed ?? null,
      next: librarian?.next_run_at ?? null,
      idleMinutes: overview?.session.idle_minutes ?? 0,
      drainedAt,
      ranAt,
      error: maintenanceError,
      drain,
      run,
    },
    loading:
      overviewQuery.isFetching ||
      sessionsQuery.isFetching ||
      credentialsQuery.isFetching ||
      integrationsQuery.isFetching ||
      attentionQuery.isFetching ||
      statusQueries.some((probe) => probe.isFetching),
    error: readError,
  };
}

/** One probe's fetch state, as much of `useQuery`'s result as `serviceState` reads. */
interface Probe {
  data?: { healthy: boolean; latency_ms: number | null };
  isPending: boolean;
  isError: boolean;
}

/**
 * Which word a service's row wears. The order is the point: a save this client
 * sent outranks a probe, a probe still in flight outranks its last answer, and
 * an integration with nothing stored says so rather than reporting the health of
 * a connection it was never given the credentials for.
 */
function serviceState(
  entry: Integration,
  probe: Probe | undefined,
  save: CredentialSave | undefined,
): ServiceState {
  // `saved · testing` only once the server has taken the credentials; while the
  // `PUT` itself is in flight nothing has been saved yet.
  if (save?.saving === true) return save.savedAt === null ? "testing" : "queued";
  if (probe === undefined || probe.isPending) return "testing";
  const fields = Object.keys(entry.schema.fields);
  if (fields.length > 0 && fields.every((field) => entry.configured[field] !== true)) {
    return "unset";
  }
  if (probe.isError) return "failed";
  return probe.data?.healthy === true ? "ok" : "failed";
}
