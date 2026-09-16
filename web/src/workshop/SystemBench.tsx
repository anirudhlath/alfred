import { useEffect, useId, useState } from "react";
import { dayLabel, hhmm, hhmmss, isoMs, whenLabel } from "@/lib/format";
import {
  spendFraction,
  spendHeadline,
  spendNote,
  staleGrid,
  type Health,
  type HealthCell,
} from "@/lib/system";
import type { Overview } from "@/lib/types";
import { Switch } from "./Switch";
import { SystemRow, SystemSection } from "./SystemFrame";
import {
  IdentitySection,
  ReflexSection,
  ServicesSection,
  SessionsSection,
} from "./SystemSections";
import type { LibrarianPass, Maintenance, Quiet, System } from "./useSystem";

export interface SystemBenchProps {
  system: System;
}

/** The four cards, in the order the grid draws them (handoff §8). */
const CELLS: (keyof Health)[] = ["bus", "reflex", "rate", "home"];

/**
 * What quiet means, in each of the four states it can be in (handoff §8). The
 * handoff writes three; the fourth is an expiry the house is still holding
 * *after* the moment it names, which its prototype never drew and the server
 * can certainly report — the notifier clears the key when it next looks.
 */
const QUIET_OFF = "off · urgent still speaks regardless";
const QUIET_FOREVER = "on · no expiry · queue will not drain on its own";
const QUIET_DRAINS = "queue drains then";
const QUIET_PASSED = "that moment has passed";

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
 * Never `sent`, never `done` — the same bargain `TriggerRow`'s fire makes with
 * its own route.
 */
const DRAIN_TAIL = "the notifier sends them when it next reads the queue";
const DRAIN_NOTE = `queued only; ${DRAIN_TAIL}`;

/** The same for the Librarian: queued here, reported on the events stream. */
const RUN_TAIL = "progress shows on the events stream as consolidation.*";
const RUN_NOTE = "queued only; the run reports on the events stream, not here";

/**
 * The Health section's own claim about itself: `live · 21:14:07` while the
 * reads are landing, and `unknown since 21:14` once they stop.
 *
 * Its own component, and its own state, so the clock ticks without re-rendering
 * the bench around it. The seconds are the point — they are what distinguishes
 * a live panel from a photograph of one — and the moment it stops ticking is
 * the moment the stamp has to freeze at. It freezes at `readAt`, the hook's
 * record of when the overview last landed, rather than at its own last tick:
 * that is the instant the grid under it stopped being evidence, and it is the
 * same instant the offline grid's bus card prints.
 *
 * `--:--` rather than the mount time for a bench that has never been live: a
 * stamp is a claim to have known something at that moment, and this one never
 * did. `.t-meta-strong` rather than the handoff's `--muted`, for
 * `SystemSection`'s reason — this line is the whole basis for trusting the grid
 * under it.
 */
function HealthStamp({ online, readAt }: { online: boolean; readAt: number | null }) {
  const [at, setAt] = useState<number | null>(null);

  useEffect(() => {
    if (!online) return;
    // In an effect, never in the render body: a render that reads the clock
    // gives a different answer every time it runs, which is what the purity
    // rule is about — a lazy `useState` initialiser reads it once and keeps
    // the answer, which is why `now` below and the two sibling benches may.
    // This is the one place on the bench that wants the clock ticking rather
    // than a stamp the server sent. Stepped
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
        : `unknown since ${readAt === null ? "--:--" : hhmm(readAt)}`}
    </span>
  );
}

/**
 * One stat card. The dot reads the cell's own `alive` flag and never the word
 * beside it: a grid that lit up because a string happened to read `alive` is
 * one rename away from lying.
 *
 * Filled when alive and an empty ring when not, which is `MemoryBench`'s store
 * dot exactly (`MemoryBench.tsx`): two filled circles a hue apart are 1.51:1 in
 * light, so hue would be the only channel carrying the state and a reader who
 * cannot separate those two hues would have nothing. Fill-versus-outline is a
 * second channel and costs one line. `--green-text` rather than `--green`,
 * which is 2.12:1 on a card in light where 1.4.11 asks 3:1 of a state
 * indicator; both it and the `--muted` ring are measured in
 * `test/contrast.test.ts`. The dot stays `aria-hidden` and redundant: the value
 * beside it says `alive` or `unknown` in words.
 *
 * A dot goes out with the rest of the grid once the reads stop landing, even
 * though `alive` is still true: react-query keeps the last answer, so that flag
 * outlives the evidence for it, and a green light over a dimmed number is the
 * §5.2 failure in miniature. `Alfred.dc.html` does the same — its offline grid
 * sets every dot to `--muted`.
 *
 * Dimmed by token and not by an opacity: a whole-card alpha composites every
 * layer under it and is invisible to `src/test/contrast.ts`, which is
 * `TriggerRow`'s reason for stepping a spent one-shot back the same way.
 */
function HealthStat({
  cell,
  index,
  online,
}: {
  cell: HealthCell;
  index: number;
  online: boolean;
}) {
  const alive = online && cell.alive;
  const edges = `${index > 1 ? "border-t border-line " : ""}${index % 2 === 1 ? "border-l border-line" : ""}`;
  return (
    <div className={`flex flex-col gap-1 px-3.5 py-3 ${edges}`}>
      <span
        aria-hidden="true"
        data-testid="health-dot"
        className="h-2 w-2 rounded-full border"
        style={{
          background: alive ? "var(--green-text)" : "transparent",
          borderColor: alive ? "var(--green-text)" : "var(--muted)",
        }}
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
 * Today's cloud spend, its cap, and how much of it is gone — a title and the
 * amount on one baseline row, the bar, then the note (handoff §8).
 *
 * Two lines and not one. `spendHeadline` is the money; `spendNote` is what the
 * money bought and what happens when it runs out, and that last sentence is
 * copy rather than a field: `core/conscious/engine.py` returns the System-1
 * fallback saying precisely that when the budget is spent. It is the only thing
 * on this bench that says what the cap *does*, which is the whole reason a cap
 * is on screen.
 *
 * The bar is labelled by both of them rather than carrying a third copy of the
 * fact: a graphic with no text equivalent is something only the sighted reader
 * gets. It takes an `outline` rather than the inset edge the switch uses,
 * because the fill is a child flush to the track's edge and would paint over an
 * inset shadow; an outline is drawn outside the box and cannot be covered.
 *
 * Once the reads stop landing the card steps back with the grid above it — the
 * handoff dims this card too — and the *fill* leaves the accent for `--fg2`.
 * Receding here means losing the attention colour, not losing contrast: a
 * graphic dimmed under 3:1 would trade §5.2 for 1.4.11.
 */
function SpendCard({ cost, online }: { cost: Overview["cost"]; online: boolean }) {
  const base = useId();
  const amountId = `${base}-amount`;
  const noteId = `${base}-note`;
  const note = spendNote(cost);
  // One decimal, so a width of 28.400000000000002% never reaches the DOM.
  const width = `${(spendFraction(cost) * 100).toFixed(1)}%`;
  return (
    <div data-testid="spend-card" className="flex flex-col gap-2 border-t border-line px-3.5 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="t-row" style={online ? undefined : { color: "var(--fg2)" }}>
          Cloud spend today
        </span>
        <span id={amountId} className="t-meta-strong shrink-0 font-mono">
          {spendHeadline(cost)}
        </span>
      </div>
      <div
        role="img"
        // A day with no cost recorded has no note; a label pointing at an id
        // that is not on the page names nothing at all.
        aria-labelledby={note === "" ? amountId : `${amountId} ${noteId}`}
        data-testid="spend-track"
        className="h-1 w-full overflow-hidden rounded-full"
        style={{ background: "var(--line)", outline: "1px solid var(--muted)" }}
      >
        <div
          data-testid="spend-fill"
          className="h-full rounded-full"
          style={{ width, background: online ? "var(--accent-text)" : "var(--fg2)" }}
        />
      </div>
      {note !== "" && (
        <span id={noteId} className="t-meta-strong">
          {note}
        </span>
      )}
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
  readAt,
}: {
  health: Health;
  cost: Overview["cost"];
  online: boolean;
  readAt: number | null;
}) {
  return (
    <SystemSection title="Health" aside={<HealthStamp online={online} readAt={readAt} />}>
      <div className="grid grid-cols-2">
        {CELLS.map((key, index) => (
          <HealthStat key={key} cell={health[key]} index={index} online={online} />
        ))}
      </div>
      <SpendCard cost={cost} online={online} />
    </SystemSection>
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
  // `<=`, so `until 22:00` tapped at exactly 22:00:00 names tomorrow rather
  // than an expiry that has already arrived.
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

/** What this client last asked the house for, and which chip it came from. */
interface Asked {
  active: boolean;
  until: string | null;
  /** The switch asks for no expiry, which is `none` — the same request the chip sends. */
  chip: ChipKind;
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

  /**
   * This client's last request, and only while the house is reporting exactly
   * it. One derivation rather than a stored confirmation, because `applied` is
   * a claim about *this client's* last write and so has to expire the moment
   * that write stops describing the world: a meeting in the calendar turning
   * quiet off by another route would otherwise leave the screen asserting it
   * applied a state nobody here asked for. Nothing else on the bench outlives
   * its evidence either — `TriggerRow`'s pending self-dismisses after 60 s, and
   * `sessions.ended` is keyed to the row it belongs to.
   *
   * It is also what identifies the chip: a rolling `1 h` recomputed against any
   * later clock can only match in the millisecond it was tapped.
   *
   * A refusal outranks it. The refused request stays in `asked` until the next
   * one replaces it, and without this term the house drifting into that state
   * by some other route would read as a write that landed after all.
   *
   * No clock on it. `sessions.ended` is stamped in the hook, where the server's
   * answer arrives; there is no such stamp for the switch, and the tap's own
   * time is not the confirmation's (`useSystem`, `end`).
   */
  const mine =
    quiet.error === null &&
    asked !== null &&
    quiet.active === asked.active &&
    isoMs(quiet.until) === isoMs(asked.until)
      ? asked
      : null;

  function ask(active: boolean, until: string | null, chip: ChipKind) {
    setAsked({ active, until, chip });
    quiet.set(active, until);
  }

  const untilMs = isoMs(quiet.until);
  // An expiry the house is still holding after the moment it names is not a
  // drain that is coming; it is one that has not happened.
  const ahead = untilMs !== null && untilMs > now;
  const sub = !quiet.active
    ? QUIET_OFF
    : untilMs === null
      ? QUIET_FOREVER
      : `on · until ${whenLabel(new Date(untilMs), new Date(now))} · ${ahead ? QUIET_DRAINS : QUIET_PASSED}`;
  const note = quiet.error ?? (mine !== null ? "applied" : null);
  // A queue that nothing is going to drain, and something in it to drain. An
  // empty queue is not growing, whatever the switch is set to — and neither is
  // one nobody has read.
  const growing = quiet.active && quiet.held !== null && quiet.held > 0 && !ahead;

  const drained = maintenance.drainedAt;
  // A refusal is the news, and it belongs to the control that caused it rather
  // than to a loose line at the end of the card — `TriggerRow`'s `rowNote`
  // folds its own the same way. Without this the button's description still
  // reads `queued only` after the POST came back 503.
  const drainNote =
    maintenance.drainError ??
    (drained === null ? DRAIN_NOTE : `queued ${hhmm(drained)} · ${DRAIN_TAIL}`);
  const drainQueued = maintenance.drainError === null && drained !== null;

  return (
    <SystemSection title="Quiet">
      <SystemRow>
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
        {/* The one switch in the Workshop that **moves**: `POST /api/admin/dnd`
            sets or deletes the Redis key itself and answers with the state, so
            there is something to confirm — unlike a trigger's `enabled`, which
            is queued for a process that reads it within 60 s (decision 6).

            It still moves on the *read* rather than on the tap: `useSystem`
            leaves the cache alone and re-reads the overview behind the write,
            so what is drawn is always the last thing the server said. The
            visible gap between the tap and the move is the point.

            Inert and busy are the same fact here — there is one reason this
            switch refuses a press, and it is the write in flight. */}
        <Switch
          on={quiet.active}
          label="Do-not-disturb"
          inert={quiet.setting}
          busy={quiet.setting}
          describedBy={note === null ? subId : `${subId} ${noteId}`}
          onToggle={() => ask(!quiet.active, null, "none")}
        />
      </SystemRow>

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
            // The chip this client asked for and the house confirmed, when
            // there is one: `1 h` is a rolling target, and matching it against
            // a recomputed instant can only ever succeed in the millisecond it
            // was tapped. For an expiry set somewhere else — a second phone, a
            // previous session — there is nothing to match but the instant
            // itself, read against the mount clock so the marks do not move
            // under the reader. The two fixed hours and `no expiry` answer that
            // way; an arbitrary instant an hour out is not knowably `1 h`.
            const marked =
              mine !== null ? mine.chip === kind : isoMs(quiet.until) === isoMs(chipUntil(kind, now));
            return (
              <button
                key={kind}
                type="button"
                aria-pressed={marked}
                onClick={() => ask(true, chipUntil(kind, Date.now()), kind)}
                className="h-11 flex-1 rounded-[10px] border px-1 text-[13px] font-medium whitespace-nowrap"
                style={{
                  background: marked ? "var(--ink)" : "transparent",
                  color: marked ? "var(--paper)" : "var(--fg2)",
                  // `--muted` rather than `--line`, for the reason the Triggers
                  // chips give: four adjacent tap targets edged in `--line` sit
                  // at 1.09:1 on this card, which is no edge at all.
                  borderColor: marked ? "transparent" : "var(--muted)",
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
          {/* The count appears when the read does, as `SystemSections`'
              `N registered` does: `0 held` before the overview has answered is
              a count of a queue nobody has looked at. The row itself stays —
              it is a way into the sheet whether or not the number is known. */}
          {quiet.held !== null && (
            <span
              className="t-meta-strong"
              style={growing ? { color: "var(--accent-text)" } : undefined}
            >
              {growing ? `${quiet.held} · growing` : `${quiet.held} held`}
            </span>
          )}
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
          style={drainQueued ? { color: "var(--accent-text)" } : undefined}
        >
          {drainNote}
        </span>
      </div>

      <p className="t-meta-strong m-0 border-t border-line px-3 py-2.5">{CALENDAR_FOOTNOTE}</p>
    </SystemSection>
  );
}

/**
 * `last 03:00 earlier today · 42 reviewed` — or `never run`, which is a fact
 * too, and is why this takes a `LibrarianPass` rather than the whole
 * `Maintenance`: `never run` is only sayable about a block the overview
 * actually sent, and the caller is what proves there is one.
 */
function consolidationLine(pass: LibrarianPass, now: number): string {
  const last = isoMs(pass.last);
  if (last === null) return "never run";
  const line = `last ${hhmm(last)} ${dayLabel(new Date(last), new Date(now))}`;
  return pass.reviewed === null ? line : `${line} · ${pass.reviewed} reviewed`;
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
  const pass = maintenance.consolidation;
  const next = pass === null ? null : isoMs(pass.next);
  const ran = maintenance.ranAt;
  const idle = maintenance.idleMinutes;
  // The refusal on the control that caused it, as the drain's is.
  const runNote =
    maintenance.runError ?? (ran === null ? RUN_NOTE : `queued ${hhmm(ran)} · ${RUN_TAIL}`);
  const runQueued = maintenance.runError === null && ran !== null;

  return (
    <SystemSection title="Maintenance">
      <SystemRow>
        <div className="flex min-w-0 flex-col gap-0.5 py-2">
          <span className="t-row">Nightly consolidation</span>
          {/* Nothing at all until the overview has answered — the gate the row
              two below already takes for `idleMinutes`, and the one
              `MemoryBench`'s own consolidation stat takes on the same key.
              `never run` is a claim about the Librarian, and an unread overview
              is no evidence for it. */}
          {pass !== null && <span className="t-meta-strong">{consolidationLine(pass, now)}</span>}
        </div>
        {next !== null && (
          <span className="t-meta-strong shrink-0">
            {`next ${whenLabel(new Date(next), new Date(now))}`}
          </span>
        )}
      </SystemRow>

      <div className="flex flex-col gap-1.5 border-t border-line px-3 py-2.5">
        {/* Outlined, not filled: this is not the bench's primary action, and
            `border-line` is 1.09:1 on this card — 1.4.11 asks for a boundary
            only where nothing else identifies the control, and this one carries
            its own name at 13.70:1. The same reading `TriggerRow`'s fire takes. */}
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
          style={runQueued ? { color: "var(--accent-text)" } : undefined}
        >
          {runNote}
        </span>
      </div>

      <SystemRow>
        <span className="t-row">Session idle timeout</span>
        {/* Nothing at all until the overview has answered: `idleMinutes` is
            null rather than 0 for exactly this, and `0 minutes` would be a
            guess wearing a number. */}
        {idle !== null && (
          <span className="t-meta-strong shrink-0">{`${idle} ${idle === 1 ? "minute" : "minutes"}`}</span>
        )}
      </SystemRow>
    </SystemSection>
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
 * Seven sections, in the handoff's order: Health, Quiet, Sessions, Connected
 * services, Devices & identity, Reflex, Maintenance. The four in the middle
 * live in `SystemSections.tsx` — they share this file's frame and nothing else,
 * and the bench was already the largest view in `src/workshop/` without them.
 */
export function SystemBench({ system }: SystemBenchProps) {
  // The clock every stamp on the bench is dated against — one clock, so two
  // stamps a pixel apart never disagree about which day `tomorrow` is. The
  // chips send the clock as it is at the *tap*, which is the instant the house
  // will hold.
  //
  // It ticks, where `MemoryBench` and `TriggersBench` freeze theirs at mount.
  // They can: their rows are dated in days. This bench prints `signed in 23:50`
  // and `last used 23:50` off `sessionMeta`/`credentialMeta`, which choose
  // between `HH:MM`, `yesterday` and a date by comparing against this value —
  // so a Workshop left open across midnight would relabel last night's session
  // as tonight's and go on doing it until something else re-rendered. A minute
  // is the finest thing either formatter resolves, so a minute is what it costs.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    // Not aligned to the top of the minute: the drift is at most 59 s on a
    // boundary a reader cannot see, and an aligned timer is a second timer to
    // get wrong. The *tick* is in an effect and never in the render body; the
    // initial read above is a lazy initialiser, which runs once at mount and
    // becomes state — the distinction `HealthStamp` spells out.
    const tick = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(tick);
  }, []);
  const { online, readAt } = system;
  // Two different silences. A grid that has *never* been read says `not read
  // yet` and keeps its own em-dashes; one that has stopped being refreshed
  // hands back the values it last derived, and those are now a photograph — a
  // dimmed `210 ms` is still a latency claim about a service that may be down.
  // `Alfred.dc.html`'s offline grid blanks all four and dates the bus card,
  // which is what `staleGrid` returns.
  const health = !online && readAt !== null ? staleGrid(readAt) : system.health;

  return (
    <>
      {/* Mounted whether or not it has anything to say: VoiceOver can miss a
          region inserted with its text already in it. What that buys is an
          error arriving while the bench is up, which is the common case — it
          cannot carry across a change of bench, because the bench and this
          region are unmounted together and come back holding whatever the hook
          still holds. The header's region (`Workshop.tsx`) is the only one on
          this surface that outlives a bench swap. `status` and not `alert` — a
          read that failed in the background is news, not an interrupt. Read
          failures only: a refused *write* is announced on the control that
          sent it, where the reader who pressed it will find it.
          The sections stay underneath holding what they were last told (spec
          §5.2); the Health stamp is what says that is now history.

          Five reads, not one. The visible line is the overview's — the spine
          every card derives from — and the four section reads are announced
          beside it and not shown, because each of them is already printed
          inside the card it belongs to. Shown twice would be clutter; said
          once is the point. */}
      <p
        role="status"
        aria-label="Read errors"
        className={system.error ? "t-meta-strong mx-4 mt-2.5 mb-0" : "sr-only"}
      >
        {system.error}
        {system.sectionErrors.length > 0 && (
          <span className="sr-only">{system.sectionErrors.join(" · ")}</span>
        )}
      </p>
      <div
        aria-busy={system.loading}
        className="flex-1 overflow-y-auto px-4"
        // 40 px, which is the handoff's and is what the siblings' `pb-10`
        // comes to; plus the home indicator, which this bench has no footer to
        // pay for on its behalf.
        style={{ paddingBottom: "calc(40px + env(safe-area-inset-bottom, 0px))" }}
      >
        <HealthSection
          health={health}
          cost={system.overview?.cost ?? null}
          online={online}
          readAt={readAt}
        />
        <QuietSection quiet={system.quiet} maintenance={system.maintenance} now={now} />
        {/* One clock for the whole bench, as the consolidation row and the
            expiry chips already share: a session list and a passkey list span
            weeks, and two stamps a pixel apart must not disagree about which
            day `tomorrow` is. */}
        <SessionsSection sessions={system.sessions} now={now} />
        <ServicesSection integrations={system.integrations} />
        <IdentitySection credentials={system.credentials} pairing={system.pairing} now={now} />
        <ReflexSection attention={system.attention} />
        <MaintenanceSection maintenance={system.maintenance} now={now} />
      </div>
    </>
  );
}
