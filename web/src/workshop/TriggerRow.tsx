import { useId } from "react";
import { dayLabel, hhmm, rawCall } from "@/lib/format";
import { triggerKind, triggerMeta, type Trigger } from "@/lib/triggers";
import type { Pending } from "./useTriggers";

export interface TriggerRowProps {
  trigger: Trigger;
  /**
   * The bench's one clock, in epoch ms. A prop rather than a `Date.now()` in
   * here for the two reasons `MemoryBench` holds its own: the hooks purity rule
   * bans reading the clock in render, and a trigger list spans weeks in both
   * directions, so every row on the bench has to date itself against the same
   * instant or two rows a pixel apart disagree about which day "tomorrow" is.
   */
  now: number;
  /** This row's request, while one is in flight or inside its 60 s window. */
  pending?: Pending;
  /** When *this client* last queued a fire — never when the trigger ran. */
  firedAt?: number;
  open: boolean;
  /** Called with the `trigger_id` — the hook keys the one open row by it. */
  onToggleOpen: (id: string) => void;
  onToggle: (trigger: Trigger) => void;
  onFire: (trigger: Trigger) => void;
}

/** The knob's two positions in a 52 px track: 3 px in from either end (handoff §7). */
const KNOB_OFF = "3px";
const KNOB_ON = "23px";

/** The standing sentence under `Fire now`, before this client has queued one. */
const FIRE_NOTE = "queued only; look for trigger.fired on the events stream to know it ran";

/** `20:52 earlier today` — the stamp shape the Memory bench uses, for a row's own detail. */
function stamp(iso: string, now: number): string {
  const date = new Date(iso);
  // A record whose stamp will not parse says so rather than printing `NaN undefined`.
  if (Number.isNaN(date.getTime())) return "--:--";
  return `${hhmm(date)} ${dayLabel(date, new Date(now))}`;
}

/**
 * What the meta line deliberately leaves out (`lib/triggers.ts`): who asked for
 * this trigger and on what day, how loudly it speaks, and whether it has ever
 * gone off. `never fired` rather than a missing clause — a trigger that has
 * never run is a fact about it, not an absence.
 */
function triggerDetail(trigger: Trigger, now: number): string {
  return [
    `created by ${trigger.created_by}`,
    stamp(trigger.created_at, now),
    `urgency ${trigger.urgency}`,
    trigger.last_fired === null ? "never fired" : `last fired ${stamp(trigger.last_fired, now)}`,
  ].join(" · ");
}

/** `500 · <detail> · …` with the clause the server did not send dropped, never blank. */
function corruptNote(detail: string | undefined): string {
  return ["500", detail, "the scheduler skips it", "fix in the store or delete"]
    .filter((clause): clause is string => Boolean(clause))
    .join(" · ");
}

/**
 * The row's own note: one line, under the meta, saying what this client has
 * asked for and does not yet know the outcome of.
 *
 * `queued` is accent, because it is a decision waiting on the world rather than
 * something that went well (decision 7). A refusal is settled — it takes
 * `.t-meta-strong`'s own --fg2 and no more, and there is no red on this bench.
 *
 * A *fire* has no line here: its note belongs under the button that sent it,
 * where the handoff puts it, and the control and its note travel together. A
 * fire that was *refused* does appear, because a refusal is news about the row
 * whether or not the reader has it open.
 */
function switchNote(pending: Pending | undefined): { text: string; queued: boolean } | null {
  if (pending === undefined) return null;
  if (pending.error !== undefined) {
    // The status when a server answered with one; otherwise what we do have.
    // A request that never arrived must not print a number nobody sent.
    return {
      text: `${pending.status ?? pending.error} · that did not land · the scheduler still has the old setting`,
      queued: false,
    };
  }
  if (pending.kind === "firing") return null;
  return {
    text: `queued ${hhmm(pending.at)} · ${pending.kind} · takes effect within 60 s`,
    queued: true,
  };
}

/**
 * One stored trigger (handoff §7). The kind, the name, what it waits for, and a
 * switch that **does not move when you press it**.
 *
 * That is the whole point of the row. `POST …/enabled` answers `queued`: the
 * triggers process applies the change inside its own 60 s cache window, and
 * nothing tells this client when. So the switch keeps reporting the `enabled`
 * the last read gave it, gains `aria-busy`, and the note underneath carries the
 * state that was *asked for* — which is the only thing we know (decision 6,
 * spec §5.2). The row flips when a fresh read says it has.
 *
 * Nothing here claims an outcome the server did not confirm: `Fire again`,
 * never `Fired`. Only `trigger.fired` on the events stream knows that.
 */
export function TriggerRow({
  trigger,
  now,
  pending,
  firedAt,
  open,
  onToggleOpen,
  onToggle,
  onFire,
}: TriggerRowProps) {
  const base = useId();
  const panelId = `${base}-panel`;
  const noteId = `${base}-note`;

  // Deviation 6's one reachable case: `GET /api/admin/triggers` drops records it
  // cannot parse, so a corrupt one is never in the list — it can only be met by
  // toggling a record that decayed since the read. The card self-dismisses when
  // the hook's window closes 60 s after the tap, deliberately: a client-side
  // claim must not outlive the evidence for it, and the next read either shows
  // the trigger again or does not.
  const corrupt = pending !== undefined && pending.status === 500;
  // One request at a time per row: a second tap inside the window would queue
  // an action against a state neither end knows, and would take the note that
  // is the only record of the first.
  const busy = pending !== undefined;
  const note = corrupt ? null : switchNote(pending);
  // It has happened and it cannot happen again; it should not read as live.
  const spent = trigger.one_shot && trigger.last_fired !== null;

  return (
    <li
      className="shrink-0"
      style={{ borderTop: "1px solid var(--line)", opacity: spent ? 0.55 : undefined }}
    >
      <div className="flex items-start gap-3">
        <button
          type="button"
          aria-expanded={open}
          // Only while open: `aria-controls` pointing at an absent id is invalid.
          aria-controls={open ? panelId : undefined}
          onClick={() => onToggleOpen(trigger.trigger_id)}
          className="flex min-h-11 min-w-0 flex-1 flex-col py-[11px] text-left"
          style={{ color: "var(--fg)" }}
        >
          {/* `--accent-text`, not the raw accent, which is 2.34:1 on paper
              (index.css, --accent-text). */}
          <span
            className="font-mono text-[10px] leading-[1.4]"
            style={{ color: "var(--accent-text)" }}
          >
            {triggerKind(trigger)}
          </span>
          <span className="t-row w-full truncate">{trigger.name}</span>
          {/* The card below replaces this line when the record cannot be read:
              the meta is built from conditions the server has just told us it
              cannot parse. The name stays — a card that does not say *which*
              record is broken is not worth drawing. */}
          {!corrupt && <span className="t-meta-strong">{triggerMeta(trigger, now)}</span>}
        </button>
        <button
          type="button"
          role="switch"
          // The stored state, always. Never the state this client asked for.
          aria-checked={trigger.enabled}
          aria-label={`${trigger.name} enabled`}
          aria-busy={busy ? true : undefined}
          aria-describedby={note ? noteId : undefined}
          disabled={busy || corrupt}
          onClick={() => onToggle(trigger)}
          className="relative mt-[11px] h-8 w-[52px] shrink-0 rounded-2xl border-0 after:absolute after:inset-x-0 after:-inset-y-1.5 after:content-['']"
          style={{ background: trigger.enabled ? "var(--accent)" : "var(--line)" }}
        >
          <span
            aria-hidden="true"
            data-testid="switch-knob"
            className="absolute top-[3px] h-[26px] w-[26px] rounded-full transition-[left] duration-200"
            style={{ left: trigger.enabled ? KNOB_ON : KNOB_OFF, background: "var(--bg)" }}
          />
        </button>
      </div>
      {corrupt && (
        <div
          className="mb-[11px] flex flex-col gap-1 rounded-[10px] px-3 py-2"
          style={{ background: "var(--surface)" }}
        >
          <span className="t-body">This record can&apos;t be read.</span>
          <span className="t-meta-strong">{corruptNote(pending.error)}</span>
        </div>
      )}
      {note && (
        <p
          id={noteId}
          className="t-meta-strong mt-0 mb-[11px]"
          style={note.queued ? { color: "var(--accent-text)" } : undefined}
        >
          {note.text}
        </p>
      )}
      {open && (
        <div id={panelId} className="mb-3 flex flex-col gap-2">
          <span className="t-meta-strong">{triggerDetail(trigger, now)}</span>
          {trigger.action ? (
            <pre
              data-testid="trigger-action"
              className="m-0 rounded-lg px-3 py-2.5 font-mono text-[11px] leading-[1.55] break-words whitespace-pre-wrap"
              style={{ background: "var(--surface)", color: "var(--fg2)" }}
            >
              {rawCall(trigger.action.tool_name, trigger.action.parameters)}
            </pre>
          ) : (
            // A trigger with nothing bound still does something: it speaks.
            <span className="t-meta-strong">no action · notification only</span>
          )}
          {/* The conditions as the server holds them — no field hidden or
              renamed, the same bargain `EventRow` makes with an event. */}
          <pre
            data-testid="trigger-conditions"
            className="m-0 rounded-lg px-3 py-2.5 font-mono text-[11px] leading-[1.55] break-words whitespace-pre-wrap"
            style={{ background: "var(--surface)", color: "var(--fg2)" }}
          >
            {JSON.stringify(trigger.conditions, null, 2)}
          </pre>
          <div className="flex flex-col gap-1">
            <button
              type="button"
              disabled={busy || corrupt}
              onClick={() => onFire(trigger)}
              className="h-11 self-start rounded-[22px] border border-line bg-transparent px-[18px] text-[14px] font-medium"
              style={{ color: "var(--fg)" }}
            >
              {/* Never `Fired`. The server queued it; what the trigger then did
                  is on the events stream and nowhere in this response. */}
              {firedAt === undefined ? "Fire now" : "Fire again"}
            </button>
            <span
              className="t-meta-strong"
              style={firedAt === undefined ? undefined : { color: "var(--accent-text)" }}
            >
              {firedAt === undefined
                ? FIRE_NOTE
                : `queued ${hhmm(firedAt)} · look for trigger.fired on the events stream to know it ran`}
            </span>
          </div>
        </div>
      )}
    </li>
  );
}
