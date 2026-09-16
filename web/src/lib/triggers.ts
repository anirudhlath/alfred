import { api, post } from "./api";
import { pastLabel, whenLabel } from "./format";
import type { Urgency } from "./types";

/**
 * A stored record's `trigger_type` — the three the registry knows
 * (`core/triggers/types/`). A record naming anything else was written by a
 * version of the engine this client has not met; `triggerKind` still places it.
 */
export type TriggerType = "time" | "sensor" | "composite";

/**
 * What the bench groups by. `schedule` is not a stored type: a `time` trigger
 * armed with a `cron` recurs, one armed with a `run_at` happens once, and the
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
 * strings and `conditions` is whatever the subclass `Conditions` model wrote.
 *
 * `conditions` is deliberately `Record<string, unknown>` rather than a union:
 * the three shapes are the subclasses' business, the admin route re-serves them
 * unvalidated, and the tool that writes them hands the model's raw dict to
 * pydantic — so every read of one below is guarded.
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

/** A condition field that is really there: a non-blank string, trimmed to what it says. */
const str = (value: unknown): string | null => {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
};

/**
 * An ISO stamp that parses, as epoch ms. Every trigger time is one —
 * `model_dump_json` writes `created_at`, `last_fired` and a time record's
 * `run_at` as ISO strings — so unlike `memory.ts`'s namesake this reads no epoch
 * numbers. Non-positive is null for the same reason it is there: no trigger was
 * written in 1969, and an epoch-0 stamp is a field that was never set.
 */
const time = (value: unknown): number | null => {
  const iso = str(value);
  if (iso === null) return null;
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) || parsed <= 0 ? null : parsed;
};

/** A count a row can print: finite, whole, never negative. */
const count = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : null;

/** A JSON object with something in it — an empty match constrains nothing. */
const record = (value: unknown): Record<string, unknown> | null => {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const entries = value as Record<string, unknown>;
  return Object.keys(entries).length === 0 ? null : entries;
};

/** What a time record is armed with. */
type TimeSchedule = { at: number } | { cron: string };

/**
 * `run_at` first, exactly as the engine reads it: `TimeTrigger.next_fire_time`
 * returns out of its `run_at` branch unconditionally, and returns nothing at all
 * once `last_fired >= run_at`, so it never falls through to a cron. A record
 * carrying both fires once and dies — and one can be written, because
 * `TimeTrigger.Conditions` has both fields optional with no validator between
 * them and the trigger tool passes the model's raw conditions dict in.
 *
 * The single place that precedence lives, so the chip and the row's own line
 * cannot disagree about which field is in charge.
 */
const timeSchedule = (conditions: Record<string, unknown>): TimeSchedule | null => {
  const at = time(conditions.run_at);
  if (at !== null) return { at };
  const cron = str(conditions.cron);
  return cron === null ? null : { cron };
};

/**
 * Which chip a trigger belongs under. `sensor` and `composite` are the stored
 * type; everything else is read as a time record — including a `trigger_type`
 * this client has never heard of, deliberately, so an unknown row still lands
 * under a chip instead of dropping out of every filtered list. One armed with a
 * cron is chipped `schedule` on exactly the evidence a known record would be.
 */
export function triggerKind(trigger: Trigger): TriggerKind {
  if (trigger.trigger_type === "sensor" || trigger.trigger_type === "composite") {
    return trigger.trigger_type;
  }
  const schedule = timeSchedule(trigger.conditions);
  return schedule !== null && "cron" in schedule ? "schedule" : "time";
}

/** The middle clause: what this trigger actually waits for. */
function waitsFor(trigger: Trigger, now: Date): string {
  const conditions = trigger.conditions;

  switch (triggerKind(trigger)) {
    case "sensor": {
      const entity = str(conditions.entity_id);
      if (entity === null) return "no entity stored";
      const state = str(conditions.state_match);
      const attributes = record(conditions.attribute_match);
      // Both clauses, because either one alone narrows the match: a row that
      // said "on any change" while an `attribute_match` was in force would be
      // describing a trigger that does not fire on any change.
      if (state === null && attributes === null) return `${entity} on any change`;
      const clauses = [entity];
      if (state !== null) clauses.push(`is ${state}`);
      if (attributes !== null) clauses.push(`with ${attributeList(attributes)}`);
      return clauses.join(" ");
    }

    case "composite": {
      const children = Array.isArray(conditions.children) ? conditions.children.length : null;
      const required = count(conditions.require);
      // Half a record is not a count. `2 of 3` read off a missing `require`
      // would be invented, and `? of 3` is not in the vocabulary.
      if (children === null || required === null) return "no conditions stored";
      return `${required} of ${children} conditions`;
    }

    case "time":
    case "schedule": {
      const schedule = timeSchedule(conditions);
      if (schedule === null) return "no schedule stored";
      // A cron is printed, never resolved: `next_fire_time()` runs in the
      // triggers process and needs the user's timezone and `last_fired` to mean
      // anything, and neither is on this response.
      if ("cron" in schedule) return `cron ${schedule.cron}`;
      return `runs ${whenLabel(new Date(schedule.at), now)}`;
    }
  }
}

/** `battery 12, mode "eco"` — the attributes a sensor match requires, as `rawCall` spells them. */
function attributeList(attributes: Record<string, unknown>): string {
  return Object.entries(attributes)
    .map(([key, value]) => `${key} ${JSON.stringify(value)}`)
    .join(", ");
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
  const created = time(trigger.created_at);
  const stamp = created === null ? "--:--" : pastLabel(new Date(created), clock);
  return [
    trigger.one_shot ? "one-shot" : "recurring",
    waitsFor(trigger, clock),
    `created from ${trigger.created_by} ${stamp}`,
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
