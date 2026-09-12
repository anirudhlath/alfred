import { hhmmss, shortId } from "./format";
import {
  compareIds,
  fetchStreamPage,
  idMs,
  record,
  scalar,
  STREAM_INFO,
  STREAMS,
  strings,
  summarise,
  type StreamRef,
} from "./streams";

/** An id join may reach this far either side of the anchor. */
export const JOIN_WINDOW_MS = 600_000;
/** An unjoined entry this close to any joined node is shown dashed… */
export const ADJACENT_MS = 5_000;
/** …but no more than this many of them, nearest the anchor first. */
export const MAX_ADJACENT = 6;
/** A state change of the entity an action named, within this of the action, is that action's doing. */
export const ENTITY_MS = 60_000;
/** Entries read per stream: the page ending JOIN_WINDOW_MS after the anchor. */
export const CANDIDATE_COUNT = 100;

/**
 * How a node got into the thread. `anchor` and a `{ key, value }` join draw a
 * solid connector — an id the server holds, or a name one schema carries for
 * another. `adjacent` draws a dashed one: near in time, joined to nothing.
 */
export type Link = "anchor" | "adjacent" | { key: string; value: string };

export interface ThreadNode extends StreamRef {
  link: Link;
}

export interface Thread {
  /** Oldest first; the anchor is among them. */
  nodes: ThreadNode[];
  /** How many of the eight streams answered the candidate read. */
  searched: number;
}

export interface Candidates {
  candidates: StreamRef[];
  searched: number;
}

interface Token {
  key: string;
  value: string;
}

function refKey(ref: StreamRef): string {
  return `${ref.stream}:${ref.entry.id}`;
}

/**
 * The ids an entry can be joined on, from the fields `bus/schemas/events.py`
 * and `core/notifications/schema.py` carry. A reflex observation carries the
 * originating event's full dump, so it joins to that event's `event_id` (and
 * `trigger_id`) as well as to its own action's `request_id`. A notification is
 * not a bus event and has no `event_id`; a confirmation names its action.
 */
function tokens({ stream, entry: { event } }: StreamRef): Token[] {
  const out: Token[] = [];
  const add = (key: string, value: unknown) => {
    const text = scalar(value);
    if (text) out.push({ key, value: text });
  };
  add("event_id", event.event_id);
  switch (stream) {
    case "user_requests":
    case "user_responses":
      add("session_id", event.session_id);
      break;
    case "events":
      add("trigger_id", event.trigger_id);
      break;
    case "actions":
    case "home_action_results":
      add("request_id", event.request_id);
      break;
    case "reflex_observations": {
      const seen = record(event.trigger_event);
      add("event_id", seen?.event_id);
      add("trigger_id", seen?.trigger_id);
      add("request_id", record(event.action)?.request_id);
      break;
    }
    case "notifications":
      add("request_id", record(event.metadata)?.pending_action_id);
      break;
    case "home_state":
      // Joined by name only: the entity an action set (see `namedEntity`).
      break;
  }
  return out;
}

/** `user_responses.actions_taken` names `actions.tool_name`, and the action is no later than the reply. */
function namedTool(reply: StreamRef, action: StreamRef): Token | null {
  if (reply.stream !== "user_responses" || action.stream !== "actions") return null;
  const tool = scalar(action.entry.event.tool_name);
  if (!tool || !strings(reply.entry.event.actions_taken).includes(tool)) return null;
  if (compareIds(action.entry.id, reply.entry.id) > 0) return null;
  return { key: "actions_taken", value: tool };
}

function actedEntity({ stream, entry: { event } }: StreamRef): string | null {
  if (stream === "actions") return scalar(record(event.parameters)?.entity_id);
  if (stream === "reflex_observations") {
    return scalar(record(record(event.action)?.parameters)?.entity_id);
  }
  return null;
}

/**
 * An action (or a reflex observation's action) named an entity, and that
 * entity's state changed within ENTITY_MS. Either side: an action precedes
 * the change it causes, while an observation is written after its result —
 * which is after the change.
 */
function namedEntity(actor: StreamRef, state: StreamRef): Token | null {
  if (state.stream !== "home_state") return null;
  const entity = scalar(state.entry.event.entity_id);
  if (!entity || actedEntity(actor) !== entity) return null;
  if (Math.abs(idMs(state.entry.id) - idMs(actor.entry.id)) > ENTITY_MS) return null;
  return { key: "entity_id", value: entity };
}

/** The joins the schemas carry as names rather than ids, checked both ways round. */
function namedJoin(a: StreamRef, b: StreamRef): Token | null {
  return namedTool(a, b) ?? namedTool(b, a) ?? namedEntity(a, b) ?? namedEntity(b, a);
}

/**
 * The thread through `candidates` from `anchor` (spec §7). Everything joined
 * to the anchor, or to something joined to it, within JOIN_WINDOW_MS; then
 * whatever else sits within ADJACENT_MS of a joined node, dashed. Oldest
 * first, catalogue order on a tie.
 */
export function buildThread(anchor: StreamRef, candidates: StreamRef[]): ThreadNode[] {
  const anchorMs = idMs(anchor.entry.id);
  const inWindow = candidates.filter((ref) => Math.abs(idMs(ref.entry.id) - anchorMs) <= JOIN_WINDOW_MS);

  const byToken = new Map<string, StreamRef[]>();
  for (const ref of inWindow) {
    for (const { key, value } of tokens(ref)) {
      const id = `${key}=${value}`;
      byToken.set(id, [...(byToken.get(id) ?? []), ref]);
    }
  }

  const nodes = new Map<string, ThreadNode>([[refKey(anchor), { ...anchor, link: "anchor" }]]);
  const queue: StreamRef[] = [anchor];
  const admit = (ref: StreamRef, link: Token) => {
    const key = refKey(ref);
    if (nodes.has(key)) return;
    nodes.set(key, { ...ref, link });
    queue.push(ref);
  };
  for (let node = queue.shift(); node; node = queue.shift()) {
    for (const token of tokens(node)) {
      for (const ref of byToken.get(`${token.key}=${token.value}`) ?? []) admit(ref, token);
    }
    for (const ref of inWindow) {
      const named = namedJoin(node, ref);
      if (named) admit(ref, named);
    }
  }

  const joined = [...nodes.values()];
  const distance = (ref: StreamRef) => Math.abs(idMs(ref.entry.id) - anchorMs);
  const adjacent = inWindow
    .filter((ref) => !nodes.has(refKey(ref)))
    .filter((ref) => joined.some((node) => Math.abs(idMs(ref.entry.id) - idMs(node.entry.id)) <= ADJACENT_MS))
    .sort((a, b) => distance(a) - distance(b))
    .slice(0, MAX_ADJACENT);
  for (const ref of adjacent) nodes.set(refKey(ref), { ...ref, link: "adjacent" });

  return [...nodes.values()].sort(
    (a, b) => compareIds(a.entry.id, b.entry.id) || STREAMS.indexOf(a.stream) - STREAMS.indexOf(b.stream),
  );
}

/** Ids are shown short, the way rows show them; a tool or entity name is shown whole. */
const SHORT_KEYS = new Set(["event_id", "request_id", "session_id"]);

/** `21:13:20 · AC · request 4b1d · home-service · joined by request_id 4b1d` — the node's meta line. */
export function nodeMeta(node: ThreadNode): string {
  const { link } = node;
  const how =
    link === "anchor"
      ? "this row"
      : link === "adjacent"
        ? "adjacent in time only"
        : `joined by ${link.key} ${SHORT_KEYS.has(link.key) ? shortId(link.value) : link.value}`;
  return [
    hhmmss(idMs(node.entry.id)),
    STREAM_INFO[node.stream].mono,
    summarise(node.stream, node.entry.event).meta,
    how,
  ]
    .filter((part) => part.length > 0)
    .join(" · ");
}

/**
 * One page of every stream, ending JOIN_WINDOW_MS after the anchor (`before`
 * is exclusive) and reaching CANDIDATE_COUNT entries back. A stream that
 * cannot be read is skipped and not counted in `searched`; the anchor's own
 * entry is dropped. When nothing could be read there is no thread to show —
 * the first failure is the error.
 */
export async function fetchThreadCandidates(anchor: StreamRef): Promise<Candidates> {
  const before = `${idMs(anchor.entry.id) + JOIN_WINDOW_MS + 1}-0`;
  const results = await Promise.allSettled(
    STREAMS.map(async (stream) => ({ stream, page: await fetchStreamPage(stream, before, CANDIDATE_COUNT) })),
  );
  const self = refKey(anchor);
  const candidates: StreamRef[] = [];
  let searched = 0;
  for (const result of results) {
    if (result.status !== "fulfilled") continue;
    searched += 1;
    const { stream, page } = result.value;
    for (const entry of page.entries) {
      const ref = { stream, entry };
      if (refKey(ref) !== self) candidates.push(ref);
    }
  }
  if (searched === 0) {
    for (const result of results) if (result.status === "rejected") throw result.reason;
  }
  return { candidates, searched };
}

export async function fetchThread(anchor: StreamRef): Promise<Thread> {
  const { candidates, searched } = await fetchThreadCandidates(anchor);
  return { nodes: buildThread(anchor, candidates), searched };
}
