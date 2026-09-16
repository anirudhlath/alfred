import { api } from "./api";
import { dayLabel, hhmm } from "./format";

export type MemoryStore = "hot" | "cold";

/**
 * One episodic memory as the bench draws it, whichever of the three shapes the
 * server answered in. Built only by `toEpisodicRow`; nothing above this file
 * branches on a store again.
 */
export interface EpisodicRow {
  /** React key. The row's id when it has one, `hot:<index>` when it does not. */
  key: string;
  id: string | null;
  store: MemoryStore;
  text: string;
  /** Epoch ms, or null when the row carried no readable time. */
  at: number | null;
  significance: number | null;
  /**
   * Deliberate recalls of this memory. Always 0 on a *browse* of the cold store:
   * that SELECT returns no retrieval stats at all, so `episodicMeta` prints
   * "never recalled" and `decaying` reads a zero it was never told. Only a
   * search result carries a cold row's real count.
   */
  recalled: number;
  lastRecalled: number | null;
  entities: string[];
  /** The match score, present only on a search result. */
  score: number | null;
  /** Low significance, cold, never recalled: on its way out at the next pass. */
  decaying: boolean;
}

/** `GET /api/admin/memory/semantic` — one Markdown file under preferences/ or profile/. */
export interface SemanticFile {
  name: string;
  dir: "preferences" | "profile";
  content: string;
  modified: string;
}

/** `RoutineStep`, mirroring core/memory/schemas.py. */
export interface RoutineStep {
  description: string;
  action: { tool_name: string; target_service: string; parameters: Record<string, unknown> } | null;
}

export type RoutineState = "candidate" | "active" | "dormant" | "archived";

/** `RoutineSpec`, mirroring core/memory/schemas.py. `confidence_history` is newest last. */
export interface Routine {
  name: string;
  trigger_pattern: string;
  steps: RoutineStep[];
  confidence: number;
  learned_from: string[];
  state: RoutineState;
  last_hit: string | null;
  consecutive_misses: number;
  last_suggested: string | null;
  confidence_history: number[];
}

/** `GET /api/admin/memory/scratchpad`. */
export interface Scratchpad {
  content: string;
  pending_queue: number;
}

/** The Librarian's lifecycle, in the order the rail draws it. */
export const ROUTINE_STAGES = [
  "candidate",
  "active",
  "dormant",
  "archived",
] as const satisfies readonly RoutineState[];

/** Below this, with nothing ever recalling it, a cold row is on its way out. */
const DECAY_FLOOR = 0.3;

const num = (value: unknown): number | null => {
  // `Number("")` is 0, which would read a blank hash field as a real zero.
  if (typeof value === "string" && value.trim() === "") return null;
  const parsed = typeof value === "string" ? Number(value) : value;
  return typeof parsed === "number" && Number.isFinite(parsed) ? parsed : null;
};

const parseJson = (value: string): unknown => {
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return undefined;
  }
};

/**
 * Epoch seconds (number or string) or an ISO string, all to epoch ms. `num` runs
 * first, or `Date.parse("1758006120")` silently becomes the year 1758.
 *
 * A non-positive result is null, not 1970: the hot hash writes `last_retrieved:
 * 0.0` to mean "nothing has ever recalled this", and no memory is older than the
 * epoch.
 */
const time = (value: unknown): number | null => {
  const seconds = num(value);
  if (seconds !== null) return seconds > 0 ? Math.round(seconds * 1000) : null;
  if (typeof value !== "string") return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) || parsed <= 0 ? null : parsed;
};

/**
 * The three stores spell significance three ways: a string float in the hot
 * Redis hash, JSON text in the cold store's TEXT column, and a dumped
 * `SignificanceScore` object from search. All of them mean `overall`. A bare
 * number is read too, which no store sends today but every one of them could.
 */
const significanceOf = (value: unknown): number | null => {
  const direct = num(value);
  if (direct !== null) return direct;
  const parsed: unknown = typeof value === "string" ? parseJson(value) : value;
  if (parsed !== null && typeof parsed === "object" && "overall" in parsed) {
    return num(parsed.overall);
  }
  return null;
};

const entityList = (value: unknown): string[] => {
  if (Array.isArray(value)) return value.filter((entity): entity is string => typeof entity === "string");
  if (typeof value !== "string" || value === "") return [];
  if (value.startsWith("[")) {
    const parsed = parseJson(value);
    // Half a JSON array is no entities, never one entity called "[oops".
    return Array.isArray(parsed)
      ? parsed.filter((entity): entity is string => typeof entity === "string")
      : [];
  }
  return value.split(",").map((entity) => entity.trim()).filter(Boolean);
};

const text = (value: unknown): string => (typeof value === "string" ? value : "");

/**
 * The one place the three episodic shapes become one row. `index` is the row's
 * position in the response, used as a key only for hot browse rows: the server
 * `HGETALL`s them with the Redis key discarded, so they carry no id at all. The
 * list is replaced whole on every read, so an index key is correct here and
 * nowhere else.
 */
export function toEpisodicRow(raw: Record<string, unknown>, index: number): EpisodicRow {
  const store: MemoryStore = raw.store === "cold" ? "cold" : "hot";
  const id = typeof raw.id === "string" && raw.id !== "" ? raw.id : null;
  const significance = significanceOf(raw.significance);
  const recalled = Math.max(0, Math.trunc(num(raw.retrieval_count) ?? 0));
  return {
    key: id ?? `${store}:${index}`,
    id,
    store,
    // `summary` on cold and search rows, `content` on hot ones.
    text: text(raw.summary) || text(raw.content),
    at: time(raw.timestamp),
    significance,
    recalled,
    lastRecalled: time(raw.last_retrieved),
    entities: entityList(raw.entities),
    score: num(raw.score),
    decaying: store === "cold" && significance !== null && significance < DECAY_FLOOR && recalled === 0,
  };
}

/**
 * `07:02 earlier today · significance 0.40 · recalled 2× · cold · match 0.62` —
 * the row's second line, in the handoff's order (§6): when, how much it
 * weighed, how often it has been reached for, which store it is in, and — only
 * on a search result — how well it matched.
 *
 * `now` because a memory browser goes back weeks: a bare wall clock on a row
 * from last Tuesday says 07:02 and means nothing. A row whose time could not be
 * read stamps `--:--` and claims no day at all rather than guessing at one.
 * Significance is dropped entirely when the row did not carry one — the cold
 * browse sends it, the hot hash sends it, but a row that did not is not a zero.
 */
export function episodicMeta(row: EpisodicRow, now: number): string {
  const recall = row.decaying
    ? "decaying"
    : row.recalled === 0
      ? "never recalled"
      : `recalled ${row.recalled}×`;
  const parts =
    row.at === null ? ["--:--"] : [`${hhmm(row.at)} ${dayLabel(new Date(row.at), new Date(now))}`];
  if (row.significance !== null) parts.push(`significance ${row.significance.toFixed(2)}`);
  parts.push(recall, row.store);
  if (row.score !== null) parts.push(`match ${row.score.toFixed(2)}`);
  return parts.join(" · ");
}

/**
 * `0.82 · +0.11 since last week` — the headline confidence and its move against
 * the previous consolidation. "Since last week" because consolidation is the
 * Librarian's nightly-into-weekly pass.
 *
 * The number is `confidence`, not `confidence_history.at(-1)`: history is what
 * the last pass recorded, `confidence` is what the routine is worth now. It is
 * handed back formatted as well as spelled into the sentence, so the sparkline
 * that labels itself with the same number does not round it a second way.
 */
export function routineTrend(routine: Routine): {
  text: string;
  rising: boolean;
  confidence: string;
} {
  const history = routine.confidence_history;
  const confidence = routine.confidence.toFixed(2);
  const previous = history.length >= 2 ? history[history.length - 2] : null;
  if (previous === null) return { text: `${confidence} · first reading`, rising: false, confidence };
  const delta = routine.confidence - previous;
  const sign = delta >= 0 ? "+" : "-";
  return {
    text: `${confidence} · ${sign}${Math.abs(delta).toFixed(2)} since last week`,
    rising: delta > 0,
    confidence,
  };
}

/** `2 steps · learned from 1 memory` — the expanded routine's first line. */
export function routineDetail(routine: Routine): string {
  const steps = routine.steps.length;
  const evidence = routine.learned_from.length;
  const stepText = steps === 1 ? "1 step" : `${steps} steps`;
  const evidenceText =
    evidence === 0
      ? "no evidence kept"
      : `learned from ${evidence} ${evidence === 1 ? "memory" : "memories"}`;
  return `${stepText} · ${evidenceText}`;
}

/**
 * Browse when the query is empty, search by meaning when it is not. The 503 a
 * down embedder answers with is deliberately not caught: `useMemory` needs to
 * see it to set the model pill.
 */
export async function fetchEpisodic(query: string): Promise<EpisodicRow[]> {
  const path = query
    ? `/api/admin/memory/episodic?${new URLSearchParams({ q: query }).toString()}`
    : "/api/admin/memory/episodic";
  const body = await api<{ entries?: Record<string, unknown>[] }>(path);
  return (body.entries ?? []).map((entry, index) => toEpisodicRow(entry, index));
}

export async function fetchSemantic(): Promise<SemanticFile[]> {
  const body = await api<{ files?: SemanticFile[] }>("/api/admin/memory/semantic");
  return body.files ?? [];
}

export async function fetchRoutines(): Promise<Routine[]> {
  const body = await api<{ routines?: Routine[] }>("/api/admin/memory/routines");
  return body.routines ?? [];
}

export async function fetchScratchpad(): Promise<Scratchpad> {
  const body = await api<Partial<Scratchpad>>("/api/admin/memory/scratchpad");
  return { content: body.content ?? "", pending_queue: body.pending_queue ?? 0 };
}
