import { useEffect, useId, useState, type ReactNode } from "react";
import { dayLabel, hhmm, hhmmss, isoMs, whenLabel } from "@/lib/format";
import { spendFraction, spendNote, type Health, type HealthCell } from "@/lib/system";
import type { Overview } from "@/lib/types";
import type { Maintenance, Quiet, System } from "./useSystem";

export interface SystemBenchProps {
  system: System;
}

/** The four cards, in the order the grid draws them (handoff §8). */
const CELLS: (keyof Health)[] = ["bus", "reflex", "rate", "home"];

/** The handoff's three sentences about what quiet means, one per state (§8). */
const QUIET_OFF = "off · urgent still speaks regardless";
const QUIET_FOREVER = "on · no expiry · queue will not drain on its own";

/**
 * Deviation 11, and the difference between a screen that is wrong and one that
 * is honest about its blind spot: `GET /api/admin/dnd` reports the Redis key
 * this bench writes, and a meeting in the calendar quiets Alfred by another
 * route entirely, which nothing here can see.
 */
const CALENDAR_FOOTNOTE =
  "A meeting in your calendar can also quiet Alfred; that is not shown here.";

/**
 * What a drain does and does not tell us. `POST …/notifications/drain`
 * publishes an internal action and returns; the notifier picks it up when it
 * next reads the queue, and nothing in the response knows whether it did.
 * Never `Sent` — the same bargain `TriggerRow`'s fire makes with its own route.
 */
const DRAIN_TAIL = "the notifier sends them when it next reads the queue";
const DRAIN_NOTE = `queued only; ${DRAIN_TAIL}`;

/** The same for the Librarian: queued here, reported on the events stream. */
const RUN_TAIL = "progress shows on the events stream as consolidation.*";
const RUN_NOTE = "queued only; the run reports on the events stream, not here";

/**
 * One section of the System bench: a caps label, optionally something on its
 * right, and a card of rows under it.
 *
 * Exported because task 9's four sections share this frame; the 22 px between
 * sections is the label's own top margin rather than a gap on the column, so
 * the first label sits 16 px under the header (handoff §8) and every one after
 * it 22 px under the card above.
 */
export function Section({
  title,
  aside,
  children,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mt-[22px] first:mt-4">
      <div className="mb-2 flex items-end justify-between gap-3">
        {/* `.t-meta-strong`, not `.t-label` — which is this exact 11 px caps at
            500, and carries `--muted`: 3.46:1 on `--bg` in light, under AA at
            this size. The handoff's label, in the token that can be read. */}
        <h3 className="t-meta-strong m-0 uppercase" style={{ letterSpacing: "0.08em" }}>
          {title}
        </h3>
        {aside}
      </div>
      <div
        className="overflow-hidden rounded-xl border border-line"
        style={{ background: "var(--surface)" }}
      >
        {children}
      </div>
    </section>
  );
}

/**
 * One 56 px row inside a section's card, divided from the row above it. The
 * divider is the row's own top border rather than the card's `divide-y`, so a
 * row that only appears in one state (the expiry chips) takes its line with it.
 */
function Row({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-14 items-center justify-between gap-3 border-t border-line px-3 first:border-t-0">
      {children}
    </div>
  );
}

/**
 * The Health section's own claim about itself: `live · 21:14:07` while the
 * reads are landing, and `unknown since 21:14` once they stop.
 *
 * Its own component, and its own state, so the clock ticks without re-rendering
 * the bench around it. The seconds are the point — they are what distinguishes
 * a live panel from a photograph of one — and the moment it stops ticking is
 * exactly the moment the stamp has to freeze at: `at` holds the last second the
 * house was known to be answering, which is what `unknown since` means.
 *
 * `--:--` rather than the mount time for a bench that has never been live: a
 * stamp is a claim to have known something at that moment, and this one never
 * did. `.t-meta-strong` rather than the handoff's `--muted`, for `Section`'s
 * reason one block up — this line is the whole basis for trusting the grid
 * under it.
 */
function Stamp({ online }: { online: boolean }) {
  const [at, setAt] = useState<number | null>(null);

  useEffect(() => {
    if (!online) return;
    // In an effect, never in the render body: the hooks purity rule bans
    // reading the clock while rendering, and this is the one place on the
    // bench that wants the clock rather than a stamp the server sent. Stepped
    // at once as well as every second — `DoorProvider`'s fuse takes the same
    // shape for the same reason: without the first step a panel that has just
    // come back reads `live · --:--:--` until the second is up.
    const step = () => setAt(Date.now());
    step();
    const tick = setInterval(step, 1000);
    return () => clearInterval(tick);
  }, [online]);

  return (
    <span
      data-testid="health-stamp"
      className="t-meta-strong"
      style={online ? undefined : { color: "var(--accent-text)" }}
    >
      {online
        ? `live · ${at === null ? "--:--:--" : hhmmss(at)}`
        : `unknown since ${at === null ? "--:--" : hhmm(at)}`}
    </span>
  );
}

/**
 * One stat card. The dot reads the cell's own `alive` flag and never the word
 * beside it: a grid that lit up because a string happened to read `alive` is
 * one rename away from lying. It is `aria-hidden` and redundant by design —
 * the value beside it says `alive` or `unknown` in words — which is why no
 * ratio is asked of `--green` here; `MemoryBench`'s store dot makes the same
 * bargain with the same three lines of markup.
 *
 * A dot goes out with the rest of the grid once the reads stop landing, even
 * though `alive` is still true: react-query keeps the last answer, so that
 * flag outlives the evidence for it, and a green light over a dimmed number is
 * the §5.2 failure in miniature.
 *
 * Dimmed by token and not by an opacity: a whole-card alpha composites every
 * layer under it and is invisible to `src/test/contrast.ts`, which is
 * `TriggerRow`'s reason for stepping a spent one-shot back the same way.
 */
function Cell({ cell, index, online }: { cell: HealthCell; index: number; online: boolean }) {
  const edges = `${index > 1 ? "border-t border-line " : ""}${index % 2 === 1 ? "border-l border-line" : ""}`;
  return (
    <div className={`flex flex-col gap-1 px-3.5 py-3 ${edges}`}>
      <span
        aria-hidden="true"
        data-testid="health-dot"
        className="h-2 w-2 rounded-full"
        style={{ background: online && cell.alive ? "var(--green)" : "var(--muted)" }}
      />
      <span
        data-testid="health-value"
        className="t-title font-mono"
        style={{ color: online ? "var(--fg)" : "var(--fg2)" }}
      >
        {cell.value}
      </span>
      <span className="t-meta-strong">{cell.note}</span>
    </div>
  );
}

/**
 * Today's cloud spend, its cap, and how much of it is gone.
 *
 * One sentence, not two: `spendNote` already opens with the handoff's
 * `$1.42 of $5.00`, so a separate headline above it would print the amount
 * twice six pixels apart. Every clause the server did not send is dropped
 * there rather than filled in here, and the zero-cap guard that keeps a `NaN`
 * off this card lives with it in `lib/system.ts` — one place, so the width and
 * the sentence can never disagree about whether there is a cap.
 *
 * The bar is labelled by that sentence rather than carrying a second copy of
 * it: a graphic with no text equivalent is a fact only the sighted reader gets.
 */
function SpendCard({ cost }: { cost: Overview["cost"] }) {
  const noteId = useId();
  const note = spendNote(cost);
  // One decimal, so a width of 28.400000000000002% never reaches the DOM.
  const width = `${(spendFraction(cost) * 100).toFixed(1)}%`;
  return (
    <div data-testid="spend-card" className="flex flex-col gap-2 border-t border-line px-3.5 py-3">
      <span className="t-row">Cloud spend today</span>
      <span id={noteId} className="t-meta-strong">
        {note}
      </span>
      <div
        role="img"
        aria-labelledby={noteId}
        className="h-1 w-full overflow-hidden rounded-full"
        style={{ background: "var(--line)" }}
      >
        <div
          data-testid="spend-fill"
          className="h-full rounded-full"
          style={{ width, background: "var(--accent)" }}
        />
      </div>
    </div>
  );
}

/**
 * What the house is doing, in the four readings it can answer for (handoff §8,
 * deviation 8 — no service count and no GPU figure, because neither has a
 * source anywhere in this API).
 */
function HealthSection({
  health,
  cost,
  online,
}: {
  health: Health;
  cost: Overview["cost"];
  online: boolean;
}) {
  return (
    <Section title="Health" aside={<Stamp online={online} />}>
      <div className="grid grid-cols-2">
        {CELLS.map((key, index) => (
          <Cell key={key} cell={health[key]} index={index} online={online} />
        ))}
      </div>
      <SpendCard cost={cost} />
    </Section>
  );
}

/** The switch's two knob positions in a 52 px track (handoff §7, via `TriggerRow`). */
const KNOB_OFF = "3px";
const KNOB_ON = "23px";

/**
 * The Do-not-disturb switch. The one switch in the Workshop that **moves**:
 * `POST /api/admin/dnd` sets or deletes the Redis key itself and answers with
 * the state, so there is something to confirm — unlike a trigger's `enabled`,
 * which is queued for a process that reads it within 60 s (decision 6).
 *
 * It still moves on the *read* rather than on the tap: `useSystem` deliberately
 * leaves the cache alone and re-reads the overview behind the write, so what is
 * drawn here is always the last thing the server said. The visible gap between
 * the tap and the move is the point.
 *
 * `aria-disabled`, never `disabled`: a disabled control cannot take focus, so
 * the note describing it is never announced and a reader hears nothing for the
 * whole window. The colours are `TriggerRow`'s — an inset `--muted` track edge
 * and a knob in the token that reads on the track it is on, all three pairs
 * measured in `test/contrast.test.ts`, because the handoff's own pairing is
 * 1.18:1 in light where WCAG 1.4.11 asks 3:1.
 */
function Switch({
  on,
  busy,
  describedBy,
  onToggle,
}: {
  on: boolean;
  busy: boolean;
  describedBy: string;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label="Do-not-disturb"
      aria-busy={busy ? true : undefined}
      aria-disabled={busy ? true : undefined}
      aria-describedby={describedBy}
      onClick={() => {
        // `aria-disabled` does not stop the event; this does. A second write
        // over one still in flight would race its own confirmation.
        if (!busy) onToggle();
      }}
      className="relative h-8 w-[52px] shrink-0 rounded-2xl border-0 after:absolute after:inset-x-0 after:-inset-y-1.5 after:content-['']"
      style={{
        background: on ? "var(--accent)" : "var(--line)",
        boxShadow: "inset 0 0 0 1px var(--muted)",
      }}
    >
      <span
        aria-hidden="true"
        data-testid="dnd-knob"
        className="absolute top-[3px] h-[26px] w-[26px] rounded-full transition-[left] duration-200"
        style={{
          left: on ? KNOB_ON : KNOB_OFF,
          background: on ? "var(--on-accent)" : "var(--fg2)",
        }}
      />
    </button>
  );
}

/** The four expiries the handoff offers, and what each one works out to. */
type ChipKind = "hour" | "noon" | "night" | "none";
const CHIPS: { kind: ChipKind; label: string }[] = [
  { kind: "hour", label: "1 h" },
  { kind: "noon", label: "until noon" },
  { kind: "night", label: "until 22:00" },
  { kind: "none", label: "no expiry" },
];

/** The next time the clock reads `hour`:00 — today while it is still ahead. */
function nextAt(now: number, hour: number): Date {
  const clock = new Date(now);
  const at = new Date(clock.getFullYear(), clock.getMonth(), clock.getDate(), hour, 0, 0, 0);
  if (at.getTime() <= now) at.setDate(at.getDate() + 1);
  return at;
}

/** The instant a chip names, as the ISO string the route wants. Null is no expiry. */
function chipUntil(kind: ChipKind, now: number): string | null {
  switch (kind) {
    case "hour":
      return new Date(now + 3_600_000).toISOString();
    case "noon":
      return nextAt(now, 12).toISOString();
    case "night":
      return nextAt(now, 22).toISOString();
    case "none":
      return null;
  }
}

/** What this client last asked the house for, while it waits to be told it landed. */
interface Asked {
  active: boolean;
  until: string | null;
}

/**
 * Quiet hours (handoff §8): the switch, the expiry it runs to, what is waiting
 * behind it, and the one thing this screen cannot see.
 *
 * `drain` belongs to `maintenance` in the hook and to this section on screen:
 * the queue it drains is the one the row above it counts.
 */
function QuietSection({
  quiet,
  maintenance,
  now,
}: {
  quiet: Quiet;
  maintenance: Maintenance;
  now: number;
}) {
  const base = useId();
  const subId = `${base}-sub`;
  const noteId = `${base}-note`;
  const drainId = `${base}-drain`;

  const [asked, setAsked] = useState<Asked | null>(null);
  const [applied, setApplied] = useState(false);

  // `applied` is a confirmed-state word, so it is written when the house agrees
  // with what was asked of it — not when the write answered, which is a moment
  // earlier and says nothing about the position the overview will report. A
  // change this client did not ask for — a calendar meeting moving the switch —
  // matches no request and claims nothing.
  //
  // Adjusted during render, the pattern React documents for state that has to
  // follow an input, and the one `useSystem` uses to retire a pairing code:
  // in an effect this would paint one frame of the old answer under the new
  // position first. It converges — the branch clears the request it reads.
  //
  // No clock on it. `sessions.ended` is stamped in the hook, where the server's
  // answer arrives; there is no such stamp for the switch, and the tap's own
  // time is not the confirmation's (`useSystem`, `end`).
  if (asked !== null) {
    const settled =
      quiet.active === asked.active && isoMs(quiet.until) === isoMs(asked.until);
    if (quiet.error !== null || settled) {
      setAsked(null);
      setApplied(quiet.error === null);
    }
  }

  function ask(active: boolean, until: string | null) {
    // The old confirmation is not news about the new request.
    setApplied(false);
    setAsked({ active, until });
    quiet.set(active, until);
  }

  const untilMs = isoMs(quiet.until);
  const sub = !quiet.active
    ? QUIET_OFF
    : untilMs === null
      ? QUIET_FOREVER
      : `on · until ${hhmm(untilMs)} · queue drains then`;
  // A refusal outranks a confirmation: while the house is disputing the last
  // write, nothing here may claim it applied.
  const note = quiet.error ?? (applied ? "applied" : null);
  // A queue with no drain is a different fact from a queue with one.
  const growing = quiet.active && untilMs === null;
  const drained = maintenance.drainedAt;

  return (
    <Section title="Quiet">
      <Row>
        <div className="flex min-w-0 flex-col gap-0.5 py-2">
          <span className="t-row">Do-not-disturb</span>
          <span id={subId} className="t-meta-strong">
            {sub}
          </span>
          {note !== null && (
            <span id={noteId} className="t-meta-strong">
              {note}
            </span>
          )}
        </div>
        <Switch
          on={quiet.active}
          busy={quiet.setting}
          describedBy={note === null ? subId : `${subId} ${noteId}`}
          onToggle={() => ask(!quiet.active, null)}
        />
      </Row>

      {/* Only while it is on: an expiry for a quiet that is off would be a
          setting with nothing to set. A toggle group, walked with Tab —
          `tabs.ts`'s roving arrows are a tablist's contract, not a group's. */}
      {quiet.active && (
        <div
          role="group"
          aria-label="Quiet until"
          className="flex items-center gap-1.5 border-t border-line px-3 py-2"
        >
          {CHIPS.map(({ kind, label }) => {
            // Against the mount clock, so the marks do not move under the
            // reader while the tap sends the instant as it is *then*. `1 h` is
            // a rolling target and so is only ever marked in the seconds after
            // its own tap — the two fixed hours and `no expiry` are the ones
            // this can answer for, and they are what a reader looks for.
            const chosen = isoMs(quiet.until) === isoMs(chipUntil(kind, now));
            return (
              <button
                key={kind}
                type="button"
                aria-pressed={chosen}
                onClick={() => ask(true, chipUntil(kind, Date.now()))}
                className="h-11 flex-1 rounded-[10px] border px-1 text-[13px] font-medium whitespace-nowrap"
                style={{
                  background: chosen ? "var(--ink)" : "transparent",
                  color: chosen ? "var(--paper)" : "var(--fg2)",
                  // `--muted` (3.46:1) rather than `--line`, for the reason the
                  // Triggers chips give: four adjacent tap targets edged in
                  // `--line` sit at 1.18:1, which is no edge at all.
                  borderColor: chosen ? "transparent" : "var(--muted)",
                }}
              >
                {label}
              </button>
            );
          })}
        </div>
      )}

      <button
        type="button"
        onClick={quiet.onHeld}
        className="flex min-h-14 w-full items-center justify-between gap-3 border-t border-line px-3 text-left"
      >
        <span className="t-row">Held back</span>
        <span className="flex items-center gap-1.5">
          <span
            className="t-meta-strong"
            style={growing ? { color: "var(--accent-text)" } : undefined}
          >
            {growing ? `${quiet.held} · growing` : `${quiet.held} held`}
          </span>
          <span aria-hidden="true" className="t-meta-strong">
            ›
          </span>
        </span>
      </button>

      <div className="flex flex-col gap-1 border-t border-line px-3 py-2.5">
        {/* `--accent-text` rather than `--fg`: the row above it is a label with
            a count, and a control that looks like one more label is one nobody
            presses. 4.85:1 on `--surface` in light (`test/contrast.test.ts`). */}
        <button
          type="button"
          aria-describedby={drainId}
          onClick={maintenance.drain}
          className="t-row min-h-11 self-start text-left"
          style={{ color: "var(--accent-text)" }}
        >
          Send them now
        </button>
        <span
          id={drainId}
          className="t-meta-strong"
          style={drained === null ? undefined : { color: "var(--accent-text)" }}
        >
          {drained === null ? DRAIN_NOTE : `queued ${hhmm(drained)} · ${DRAIN_TAIL}`}
        </span>
      </div>

      <p className="t-meta-strong m-0 border-t border-line px-3 py-2.5">{CALENDAR_FOOTNOTE}</p>
    </Section>
  );
}

/** `last 03:00 earlier today · 42 reviewed` — or `never run`, which is a fact too. */
function consolidationLine(maintenance: Maintenance, now: number): string {
  const last = isoMs(maintenance.last);
  if (last === null) return "never run";
  const line = `last ${hhmm(last)} ${dayLabel(new Date(last), new Date(now))}`;
  return maintenance.reviewed === null ? line : `${line} · ${maintenance.reviewed} reviewed`;
}

/**
 * Maintenance (handoff §8): the nightly pass, a way to ask for one now, and the
 * timeout the server applies to a chat session.
 *
 * No restart, no shutdown, no log download: none of the three has a route, and
 * a button that cannot do what it says is worse than its absence.
 */
function MaintenanceSection({ maintenance, now }: { maintenance: Maintenance; now: number }) {
  const runId = useId();
  const next = isoMs(maintenance.next);
  const ran = maintenance.ranAt;
  const idle = maintenance.idleMinutes;

  return (
    <Section title="Maintenance">
      <Row>
        <div className="flex min-w-0 flex-col gap-0.5 py-2">
          <span className="t-row">Nightly consolidation</span>
          <span className="t-meta-strong">{consolidationLine(maintenance, now)}</span>
        </div>
        {next !== null && (
          <span className="t-meta-strong shrink-0">
            {`next ${whenLabel(new Date(next), new Date(now))}`}
          </span>
        )}
      </Row>

      <div className="flex flex-col gap-1.5 border-t border-line px-3 py-2.5">
        {/* Outlined, not filled: this is not the bench's primary action, and
            `border-line` is 1.18:1 on the card — 1.4.11 asks for a boundary
            only where nothing else identifies the control, and this one carries
            its own name in `--fg`. The same reading `TriggerRow`'s fire takes. */}
        <button
          type="button"
          aria-describedby={runId}
          onClick={maintenance.run}
          className="h-11 self-start rounded-[22px] border border-line bg-transparent px-[18px] text-[14px] font-medium"
          style={{ color: "var(--fg)" }}
        >
          {ran === null ? "Run consolidation now" : "Run again"}
        </button>
        <span
          id={runId}
          className="t-meta-strong"
          style={ran === null ? undefined : { color: "var(--accent-text)" }}
        >
          {ran === null ? RUN_NOTE : `queued ${hhmm(ran)} · ${RUN_TAIL}`}
        </span>
      </div>

      <Row>
        <span className="t-row">Session idle timeout</span>
        {/* Nothing at all until the overview has answered: `idleMinutes` is
            null rather than 0 for exactly this, and `0 minutes` would be a
            guess wearing a number. */}
        {idle !== null && (
          <span className="t-meta-strong shrink-0">{`${idle} ${idle === 1 ? "minute" : "minutes"}`}</span>
        )}
      </Row>

      {maintenance.error !== null && (
        <p className="t-meta-strong m-0 border-t border-line px-3 py-2.5">{maintenance.error}</p>
      )}
    </Section>
  );
}

/**
 * The System bench (handoff §8; spec §8 phase 3): what the house can say about
 * itself, what it is holding back, and the nightly work. A pure view over
 * `useSystem`'s one state object, exactly as `TriggersBench` is over `Triggers`
 * — it fetches nothing, which is what lets its tests build a state rather than
 * a `QueryClient`.
 *
 * "Online" here is this bench's own reads landing, not the chat socket: every
 * number on the grid comes from the overview poll, so the poll is what the
 * stamp above them is entitled to speak for. The telemetry pump's liveness is
 * the Workshop header's business and says nothing about these figures.
 *
 * Task 9 adds Sessions, Connected services, Devices & identity and Reflex
 * between Quiet and Maintenance.
 */
export function SystemBench({ system }: SystemBenchProps) {
  // The clock every stamp on the bench is dated against — read once when the
  // bench mounts, as `MemoryBench` and `TriggersBench` do, so the consolidation
  // row and the expiry chips agree about which day tomorrow is. The chips send
  // the clock as it is at the *tap*, which is the instant the house will hold.
  const [now] = useState(() => Date.now());
  const online = system.overview !== undefined && system.error === null;

  return (
    <>
      {/* Mounted whether or not it has anything to say: VoiceOver can miss a
          region inserted with its text already in it, which is why the
          Triggers bench and the Room's offline note both keep theirs. `status`
          and not `alert` — a read that failed in the background is news, not an
          interrupt. The sections stay underneath holding what they were last
          told (spec §5.2); the Health stamp is what says that is now history. */}
      <p
        role="status"
        aria-label="Read errors"
        className={system.error ? "t-meta-strong mx-4 mt-2.5 mb-0" : "sr-only"}
      >
        {system.error}
      </p>
      <div
        className="flex-1 overflow-y-auto px-4"
        style={{ paddingBottom: "calc(22px + env(safe-area-inset-bottom, 0px))" }}
      >
        <HealthSection
          health={system.health}
          cost={system.overview?.cost ?? null}
          online={online}
        />
        <QuietSection quiet={system.quiet} maintenance={system.maintenance} now={now} />
        <MaintenanceSection maintenance={system.maintenance} now={now} />
      </div>
    </>
  );
}
