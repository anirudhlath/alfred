import { api, post } from "./api";
import { dayLabel, dayMonth, hhmm } from "./format";
import type { Urgency } from "./types";

/**
 * A stored record's `trigger_type` — the three the registry knows
 * (`core/triggers/types/`). A record naming anything else was written by a
 * version of the engine this client has not met; `triggerKind` still places it.
 */
export type TriggerType = "time" | "sensor" | "composite";

/**
 * What the bench groups by. `schedule` is not a stored type: a `time` trigger
 * carrying a `cron` recurs, one carrying a `run_at` happens once, and the
 * handoff's chips (§7) draw that distinction even though the record does not.
 */
export type TriggerKind = "time" | "schedule" | "sensor" | "composite";

/** The kind chips, in the handoff's order. `all` is a filter, never a kind. */
export const TRIGGER_KINDS = [
  "all",
  "time",
  "schedule",
  "sensor",
  "composite",
] as const satisfies readonly ("all" | TriggerKind)[];

/** `ActionPayload`, mirroring core/triggers/models.py. Null when nothing is bound. */
export interface TriggerAction {
  tool_name: string;
  target_service: string;
  parameters: Record<string, unknown>;
}

/**
 * One trigger as `GET /api/admin/triggers` sends it: `BaseTrigger.model_dump_json()`
 * read straight back out of the Redis hash, so `created_at`/`last_fired` are ISO
 * strings and `conditions` is whichever subclass `Conditions` model wrote it.
 *
 * `conditions` is deliberately `Record<string, unknown>` rather than a union:
 * the three shapes are the subclasses' business, the admin route re-serves them
 * unvalidated, and every read of them in this file is guarded. Only the helpers
 * below look inside one.
 */
export interface Trigger {
  trigger_id: string;
  trigger_type: TriggerType;
  name: string;
  enabled: boolean;
  one_shot: boolean;
  created_by: string;
  created_at: string;
  last_fired: string | null;
  action: TriggerAction | null;
  urgency: Urgency;
  conditions: Record<string, unknown>;
}

/** A condition field that is actually there: a non-blank string, or nothing. */
const str = (value: unknown): string | null =>
  typeof value === "string" && value.trim() !== "" ? value : null;

/** An ISO stamp that parses. An unreadable one is no time at all, never 1970. */
const at = (value: unknown): Date | null => {
  const iso = str(value);
  if (iso === null) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
};

const DAY_MS = 86_400_000;

const startOfDay = (date: Date): number =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

/**
 * `08:40` today · `08:40 tomorrow` · `08:40 12 Sep` · `20:52 yesterday`.
 *
 * `dayLabel` alone cannot do this: it was written for a feed, where everything
 * is in the past, so it answers "earlier today" for every future date. A trigger
 * line looks forward — `runs 08:40` on a row due next Thursday would be a lie —
 * so the future is labelled here and the past is handed to `dayLabel`, which
 * already spells it the way the rest of the client does.
 */
function whenLabel(date: Date, now: Date): string {
  const days = Math.round((startOfDay(date) - startOfDay(now)) / DAY_MS);
  if (days === 0) return hhmm(date);
  if (days === 1) return `${hhmm(date)} tomorrow`;
  if (days > 1) return `${hhmm(date)} ${dayMonth(date)}`;
  return `${hhmm(date)} ${dayLabel(date, now)}`;
}

/**
 * Which chip a trigger belongs under. `sensor` and `composite` are the stored
 * type; a `time` record splits on whether it holds a cron, and a type this
 * client has never heard of is read as time-ish rather than dropped from every
 * filtered list.
 */
export function triggerKind(trigger: Trigger): TriggerKind {
  if (trigger.trigger_type === "sensor" || trigger.trigger_type === "composite") {
    return trigger.trigger_type;
  }
  return str(trigger.conditions.cron) === null ? "time" : "schedule";
}

/** The middle clause: what this trigger actually waits for. */
function waitsFor(trigger: Trigger, now: Date): string {
  const conditions = trigger.conditions;

  switch (triggerKind(trigger)) {
    case "sensor": {
      const entity = str(conditions.entity_id);
      if (entity === null) return "no entity stored";
      const state = str(conditions.state_match);
      return state === null ? `${entity} on any change` : `${entity} is ${state}`;
    }

    case "composite": {
      const children = Array.isArray(conditions.children) ? conditions.children.length : null;
      const required = typeof conditions.require === "number" ? conditions.require : null;
      if (children === null || required === null) return "no conditions stored";
      return `${required} of ${children} conditions`;
    }

    // One case for both halves of a time record, because `schedule` is only
    // ever a time record that had a cron. A record holding both is drawn by its
    // cron, so this line agrees with the kind beside it — though
    // `TimeTrigger.next_fire_time` reads `run_at` first and would fire that
    // one. Nothing writes both: the LLM's tool sets one field or the other.
    case "time":
    case "schedule": {
      const cron = str(conditions.cron);
      // Printed, not resolved: `next_fire_time()` lives in the triggers process
      // and needs the user's timezone and `last_fired` to mean anything. A cron
      // we can only show; inventing "fires 19:00 Thursday" here would be a guess.
      if (cron !== null) return `cron ${cron}`;
      const runAt = at(conditions.run_at);
      return runAt === null ? "no schedule stored" : `runs ${whenLabel(runAt, now)}`;
    }
  }
}

/**
 * `one-shot · runs 08:40 tomorrow · created from conversation 20:52` — the
 * row's second line, in the handoff's three clauses (§7): how often it can
 * fire, what it waits for, and who asked for it when.
 *
 * `now` because a trigger list spans weeks in both directions: a bare clock on
 * a row created last Wednesday, or due next Thursday, says a time and means
 * nothing. `last_fired` is deliberately absent — the handoff does not put it
 * here, and the expanded detail has room for it.
 */
export function triggerMeta(trigger: Trigger, now: number): string {
  const clock = new Date(now);
  const created = at(trigger.created_at);
  return [
    trigger.one_shot ? "one-shot" : "recurring",
    waitsFor(trigger, clock),
    `created from ${trigger.created_by} ${created === null ? "--:--" : whenLabel(created, clock)}`,
  ].join(" · ");
}

/** `GET /api/admin/triggers` — newest first, unparseable records already dropped. */
export async function fetchTriggers(): Promise<Trigger[]> {
  const body = await api<{ triggers?: Trigger[] }>("/api/admin/triggers");
  return body.triggers ?? [];
}

/**
 * `POST /api/admin/triggers/{id}/enabled` — 200 is `queued`, not applied: the
 * route publishes to the actions stream and the triggers process picks the
 * change up inside its own 60 s cache window. The row must not move on this.
 *
 * The id is encoded because triggers are created by the LLM's own tool call,
 * so `trigger_id` is whatever it chose to name one.
 */
export async function setTriggerEnabled(id: string, enabled: boolean): Promise<void> {
  await post<{ status: string }>(`/api/admin/triggers/${encodeURIComponent(id)}/enabled`, {
    enabled,
  });
}

/** `POST /api/admin/triggers/{id}/fire` — queued the same way, 404 if it is gone. */
export async function fireTrigger(id: string): Promise<void> {
  await post<{ status: string }>(`/api/admin/triggers/${encodeURIComponent(id)}/fire`);
}
