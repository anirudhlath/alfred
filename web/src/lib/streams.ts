import { api } from "./api";
import { notificationText, shortId } from "./format";
import type { StreamEntry, StreamPage } from "./types";

/** The eight catalogued streams, in the handoff's chip order (README §Workshop). */
export const STREAMS = [
  "user_requests",
  "user_responses",
  "events",
  "actions",
  "reflex_observations",
  "notifications",
  "home_state",
  "home_action_results",
] as const;

export type StreamName = (typeof STREAMS)[number];

/** Monogram and ring hue per stream. The Room's act rows use 120/210/255 of these. */
export const STREAM_INFO: Record<StreamName, { mono: string; hue: number }> = {
  user_requests: { mono: "UR", hue: 30 },
  user_responses: { mono: "AL", hue: 75 },
  events: { mono: "EV", hue: 120 },
  actions: { mono: "AC", hue: 165 },
  reflex_observations: { mono: "RX", hue: 210 },
  notifications: { mono: "NT", hue: 255 },
  home_state: { mono: "HS", hue: 300 },
  home_action_results: { mono: "HR", hue: 345 },
};

export function isStreamName(value: string): value is StreamName {
  return (STREAMS as readonly string[]).includes(value);
}

/** The handoff's stream ring: one chroma, one lightness, eight hues. */
export function ring(hue: number): string {
  return `oklch(0.62 0.11 ${hue})`;
}

/** `1788815640000-0` → `[1788815640000, 0]`. Anything unreadable is 0, never NaN, so sorts stay total. */
function idParts(id: string): [number, number] {
  const [ms, seq] = id.split("-");
  return [Number(ms) || 0, Number(seq) || 0];
}

/**
 * When an entry happened: the millisecond half of its Redis id, which is the
 * server's clock at the moment of the write. Preferred over `event.timestamp`
 * in the Workshop because an ISO stamp without a zone is ambiguous and the id
 * never is.
 */
export function idMs(id: string): number {
  return idParts(id)[0];
}

/** Redis stream order: milliseconds, then sequence. Negative when `a` is older. */
export function compareIds(a: string, b: string): number {
  const [aMs, aSeq] = idParts(a);
  const [bMs, bSeq] = idParts(b);
  return aMs - bMs || aSeq - bSeq;
}

export const PAGE_COUNT = 50;

/**
 * One page of a stream, newest first. `before` is exclusive on the server
 * (`max_id = "(" + before`), so passing the oldest id you hold gives the page
 * that ends just before it. The server clamps `count` to 1..200.
 */
export async function fetchStreamPage(
  name: StreamName,
  before?: string | null,
  count: number = PAGE_COUNT,
): Promise<StreamPage> {
  const params = new URLSearchParams({ count: String(count) });
  if (before) params.set("before", before);
  const page = await api<Partial<StreamPage>>(`/api/admin/streams/${name}?${params}`);
  return { entries: page.entries ?? [], next_before: page.next_before ?? null };
}

/** One entry of one stream — what a row, a `why?` and a thread node all point at. */
export interface StreamRef {
  stream: StreamName;
  entry: StreamEntry;
}

export interface Summary {
  /** The row's line: 14 px, ellipsised. */
  text: string;
  /** The row's meta, without the stamp: mono 11, ellipsised. Parts joined by ` · `. */
  meta: string;
}

/** A string, number or boolean as text; anything else (including "") is `null`. Shared with `trace.ts`. */
export function scalar(value: unknown): string | null {
  if (typeof value === "string") return value.length > 0 ? value : null;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

export function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** ` · `-join, dropping anything empty, so a missing field leaves no ` ·  · `. */
function join(parts: (string | null | false | undefined)[]): string {
  return parts.filter((part): part is string => typeof part === "string" && part.length > 0).join(" · ");
}

/**
 * One line and one meta line for any event on any of the eight streams, from
 * the fields `bus/schemas/events.py` and `core/notifications/schema.py`
 * actually carry. Every field is optional here: a producer that omitted one
 * gets a shorter line, never a crash and never a made-up value (spec §5.2).
 */
export function summarise(stream: StreamName, event: Record<string, unknown>): Summary {
  const source = scalar(event.source);
  switch (stream) {
    case "user_requests": {
      const session = scalar(event.session_id);
      return {
        text: scalar(event.content) ?? "voice message",
        meta: join([session && `session ${session}`, scalar(event.channel), scalar(event.content_type)]),
      };
    }
    case "user_responses": {
      const session = scalar(event.session_id);
      const tools = strings(event.actions_taken);
      return {
        text: scalar(event.text) ?? "reply",
        meta: join([
          session && `session ${session}`,
          `mood ${scalar(event.mood) ?? "neutral"}`,
          tools.length > 0 ? tools.join(", ") : "no tools",
        ]),
      };
    }
    case "events": {
      const type = scalar(event.event_type);
      if (type === "trigger_fired") {
        return {
          text: `trigger fired: ${scalar(event.trigger_name) ?? "trigger"}`,
          meta: join([
            `fired by ${scalar(event.fired_by) ?? "engine"}`,
            scalar(event.trigger_type),
            scalar(event.urgency),
          ]),
        };
      }
      if (type === "trigger_created") {
        const by = scalar(event.created_by);
        return {
          text: `trigger created: ${scalar(event.name) ?? "trigger"}`,
          meta: join([event.one_shot === true ? "one-shot" : "recurring", scalar(event.trigger_type), by && `by ${by}`]),
        };
      }
      if (type === "service_registered") {
        return {
          text: `service registered: ${scalar(event.service_name) ?? "service"}`,
          meta: join([source && `source ${source}`]),
        };
      }
      return { text: type ? type.replace(/_/g, " ") : "event", meta: join([source && `source ${source}`]) };
    }
    case "actions": {
      const tool = scalar(event.tool_name) ?? "action";
      const entity = scalar(record(event.parameters)?.entity_id);
      const request = scalar(event.request_id);
      return {
        text: entity ? `${tool} ${entity}` : tool,
        meta: join([
          request && `request ${shortId(request)}`,
          scalar(event.target_service),
          source,
          event.confirmed === true && "confirmed",
        ]),
      };
    }
    case "reflex_observations": {
      const seen = record(event.trigger_event);
      const subject =
        scalar(seen?.entity_id) ?? scalar(seen?.trigger_name) ?? scalar(event.origin) ?? "event";
      const action = record(event.action);
      const request = scalar(action?.request_id);
      const decision = scalar(event.decision_context);
      const failed = scalar(record(event.result)?.status) === "error";
      // Spec §10: a passive observation reads "watched, took no action".
      return {
        text: `observed ${subject} · ${action ? "acted" : "watched, took no action"}`,
        meta: join([
          action && (scalar(action.tool_name) ?? "action"),
          request && `request ${shortId(request)}`,
          decision && `decision "${decision}"`,
          failed && "failed",
        ]),
      };
    }
    case "notifications": {
      const pending = scalar(record(event.metadata)?.pending_action_id);
      return {
        text: notificationText(event.body, event.title) ?? "notification",
        meta: join([scalar(event.urgency), source, pending && `confirmation ${shortId(pending)}`]),
      };
    }
    case "home_state": {
      const entity = scalar(event.entity_id) ?? "entity";
      return {
        text: `${entity} → ${scalar(event.new_state) ?? "unknown"}`,
        meta: join([
          scalar(event.domain),
          `was ${scalar(event.old_state) ?? "unknown"}`,
          source && `via ${source}`,
        ]),
      };
    }
    case "home_action_results": {
      const request = scalar(event.request_id);
      return {
        text: `${scalar(event.tool_name) ?? "action"} ${scalar(event.status) ?? "unknown"}`,
        meta: join([request && `request ${shortId(request)}`, scalar(event.error)]),
      };
    }
  }
}
