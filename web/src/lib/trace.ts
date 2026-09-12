import { hhmmss, shortId } from "./format";
import {
  compareIds,
  fetchStreamPage,
  idMs,
  record,
  rowKey,
  scalar,
  STREAM_INFO,
  STREAMS,
  strings,
  summarise,
  type StreamRef,
} from "./streams";
import type { StreamPage } from "./types";

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
  /**
   * How many of those answered but could not be seen back far enough (see
   * `isPartial`). A stream busy enough to fill its page inside the window hides
   * the anchor's whole past, and a thread of one node would otherwise read as
   * "nothing else was involved" rather than "I could not see that far back".
   */
  partial: number;
}

/** What one candidate read returned; `searched` and `partial` mean what they mean on `Thread`. */
export interface Candidates {
  candidates: StreamRef[];
  searched: number;
  partial: number;
}

interface Token {
  key: string;
  value: string;
}

/**
 * The ids an entry can be joined on, from the fields `bus/schemas/events.py`
 * and `core/notifications/schema.py` carry. A reflex observation carries the
 * originating event's full dump, so it joins to that event's `event_id` as
 * well as to its own action's `request_id` — which is how a fired trigger
 * reaches the observation it caused. A notification is not a bus event and has
 * no `event_id`; a confirmation names its action.
 *
 * Deliberately not `trigger_id`: it names the trigger, not the firing, so two
 * firings of one recurring trigger inside the window would join — and the walk
 * would go on from the second firing to its own observation, action and
 * result, drawing solid connectors across two unrelated episodes. `event_id`
 * is the join that means *this* firing.
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
    case "actions":
    case "home_action_results":
      add("request_id", event.request_id);
      break;
    case "reflex_observations": {
      const seen = record(event.trigger_event);
      add("event_id", seen?.event_id);
      add("request_id", record(event.action)?.request_id);
      break;
    }
    case "notifications":
      add("request_id", record(event.metadata)?.pending_action_id);
      break;
    case "events":
      // No id beyond the event_id above: a trigger id names the rule rather
      // than the firing, so it is not a join (see above).
      break;
    case "home_state":
      // No id beyond the event_id above; the other join is by name (see `namedEntity`).
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
      const bucket = byToken.get(id);
      if (bucket) bucket.push(ref);
      else byToken.set(id, [ref]);
    }
  }

  const nodes = new Map<string, ThreadNode>([[rowKey(anchor), { ...anchor, link: "anchor" }]]);
  const queue: StreamRef[] = [anchor];
  const admit = (ref: StreamRef, link: Token) => {
    const key = rowKey(ref);
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
    .filter((ref) => !nodes.has(rowKey(ref)))
    .filter((ref) => joined.some((node) => Math.abs(idMs(ref.entry.id) - idMs(node.entry.id)) <= ADJACENT_MS))
    .sort((a, b) => distance(a) - distance(b))
    .slice(0, MAX_ADJACENT);
  for (const ref of adjacent) nodes.set(rowKey(ref), { ...ref, link: "adjacent" });

  return [...nodes.values()].sort(
    (a, b) => compareIds(a.entry.id, b.entry.id) || STREAMS.indexOf(a.stream) - STREAMS.indexOf(b.stream),
  );
}

/**
 * An id is shown short, the way rows show it — except a session id, which
 * `summarise` prints whole a few characters earlier on the same line, and one
 * id appearing two ways on one line is worse than a long one. A tool or entity
 * name is shown whole for the same reason.
 */
const SHORT_KEYS = new Set(["event_id", "request_id"]);

/** `21:13:20 · AC · request 4b1d · home-service · joined by request_id 4b1d` — the node's meta line. */
export function nodeMeta(node: ThreadNode): string {
  const { link } = node;
  const how =
    link === "anchor"
      ? "this row"
      : link === "adjacent"
        ? "adjacent in time only"
        : `joined by ${link.key} ${SHORT_KEYS.has(link.key) ? shortId(link.value) : link.value}`;
  // `idMs` is 0 for an id it cannot read, and 0 ms is a real instant — a 1970
  // clock. Say so instead, as the rows do (`EventRow`'s `stamp`).
  const at = idMs(node.entry.id);
  return [
    at > 0 ? hhmmss(at) : "--:--:--",
    STREAM_INFO[node.stream].mono,
    summarise(node.stream, node.entry.event).meta,
    how,
  ]
    .filter((part) => part.length > 0)
    .join(" · ");
}

/**
 * A stream that answered but could not be seen back far enough. The server
 * sets `next_before` only on a full page (`core/channels/admin_api.py`), so a
 * page that both fills and stops after the window's start has hidden the
 * anchor's whole past behind it — `home_state` need only produce 0.17
 * entries a second for that. Detected rather than read deeper: the sheet can
 * say the search was shallow, which a silent thread of one cannot.
 */
function isPartial(page: StreamPage, anchorMs: number): boolean {
  if (page.next_before === null || page.entries.length === 0) return false;
  const oldest = page.entries.reduce((min, entry) => Math.min(min, idMs(entry.id)), Infinity);
  return oldest > anchorMs - JOIN_WINDOW_MS;
}

/**
 * One page of every stream, ending JOIN_WINDOW_MS after the anchor (`before`
 * is exclusive) and reaching CANDIDATE_COUNT entries back. A stream that
 * cannot be read is skipped and not counted in `searched`; the anchor's own
 * entry is dropped. When nothing could be read there is no thread to show —
 * the first failure is the error.
 */
export async function fetchThreadCandidates(anchor: StreamRef): Promise<Candidates> {
  const anchorMs = idMs(anchor.entry.id);
  const before = `${anchorMs + JOIN_WINDOW_MS + 1}-0`;
  const results = await Promise.allSettled(
    STREAMS.map(async (stream) => ({ stream, page: await fetchStreamPage(stream, before, CANDIDATE_COUNT) })),
  );
  const self = rowKey(anchor);
  const candidates: StreamRef[] = [];
  let searched = 0;
  let partial = 0;
  for (const result of results) {
    if (result.status !== "fulfilled") continue;
    searched += 1;
    const { stream, page } = result.value;
    if (isPartial(page, anchorMs)) partial += 1;
    for (const entry of page.entries) {
      const ref = { stream, entry };
      if (rowKey(ref) !== self) candidates.push(ref);
    }
  }
  if (searched === 0) {
    for (const result of results) if (result.status === "rejected") throw result.reason;
  }
  return { candidates, searched, partial };
}

export async function fetchThread(anchor: StreamRef): Promise<Thread> {
  const { candidates, searched, partial } = await fetchThreadCandidates(anchor);
  return { nodes: buildThread(anchor, candidates), searched, partial };
}
