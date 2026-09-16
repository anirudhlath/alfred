import { useId, useMemo, useState, type KeyboardEvent } from "react";
import { dayLabel, hhmm } from "@/lib/format";
import {
  episodicMeta,
  type EpisodicRow,
  type Scratchpad as ScratchpadState,
  type SemanticFile,
} from "@/lib/memory";
import { RoutineRow } from "./RoutineRow";
import { tabId, tabKeyDown } from "./tabs";
import type { Consolidation, Memory, MemoryTab } from "./useMemory";

export interface MemoryBenchProps {
  memory: Memory;
}

/**
 * The four sub-tabs and the sentence each one opens with, verbatim from the
 * handoff (§6). Episodic's is the most important sentence on the bench: it is
 * the reason the endpoint passes `update_stats=False`, and a memory browser
 * that silently reinforced whatever you looked at would corrupt the very decay
 * it is showing you.
 *
 * Scratchpad's is the one the handoff asked for and did not write; it is in the
 * handoff's voice and says only what `LibrarianAgent` does.
 */
const TABS: { id: MemoryTab; label: string; note: string }[] = [
  {
    id: "episodic",
    label: "Episodic",
    note: "Browsing here does not count as recall. Nothing you open is kept warmer or colder for it.",
  },
  {
    id: "semantic",
    label: "Semantic",
    note: "Human-readable documents the conscious mind reads before every reply. Rewritten by the nightly consolidation.",
  },
  {
    id: "routines",
    label: "Routines",
    note: "Patterns Alfred noticed on its own. Ignored suggestions lose confidence and slide right until archived.",
  },
  {
    id: "scratchpad",
    label: "Scratchpad",
    note: "Working notes Alfred keeps between consolidations. The nightly pass reads them and rewrites semantic memory.",
  },
];

/** The same four, in the same order, as the keyboard walks them. */
const TAB_IDS: readonly MemoryTab[] = TABS.map((tab) => tab.id);

/** How many lines of a semantic file are shown before `Show all`. */
const CLAMP_LINES = 12;
/** Roughly what a 393 px column fits on one line at 14 px. */
const CLAMP_CHARS = 60;

/**
 * How many lines a file will take once it has wrapped — an estimate, not a
 * measurement. A real one costs a layout effect and a `scrollHeight` read per
 * card (`ActivityBench.tsx` does that for the one place it matters), and jsdom
 * lays nothing out, so a measured clamp would be untestable as well as
 * expensive.
 *
 * Neither way of being wrong hides anything: the clamp and the `Show all`
 * button are gated on this one number, so a short estimate draws the whole file
 * with no clamp at all, and a long one offers a button with nothing to show.
 */
function estimateLines(content: string): number {
  return content
    .split("\n")
    .reduce((lines, line) => lines + Math.max(1, Math.ceil(line.length / CLAMP_CHARS)), 0);
}

/** `-webkit-line-clamp` from the constant the estimate compares against, so the two cannot drift. */
const CLAMP_STYLE = {
  display: "-webkit-box",
  WebkitBoxOrient: "vertical",
  WebkitLineClamp: CLAMP_LINES,
  overflow: "hidden",
} as const;

/** `07:02 earlier today` — the stamp shape `episodicMeta` uses, for the lines that are not rows. */
function stamp(at: string | number, now: number): string {
  const date = new Date(at);
  return `${hhmm(date)} ${dayLabel(date, new Date(now))}`;
}

/** The empty-list block every tab uses: a sentence, and the evidence under it. */
/**
 * What an empty list means, and the two things it must not be mistaken for. A
 * read that was refused and one that has not landed are both "nothing on
 * screen", and neither is "the store is empty" — `ActivityBench`'s `emptyNote`
 * draws the same three-way distinction for the same reason, and it is the one
 * §5.2 is most often lost to: the bench that says `No routines learned yet.`
 * over a 500 has invented an answer the server never gave.
 *
 * **A landed read outranks the error beside it.** On Episodic the two do not
 * even belong to the same request: `Memory.error` carries the *search's*
 * refusal as well as the browse's, and the list under this sentence is the
 * browse. A 503 from the embedder was leaving a browse that answered perfectly
 * well labelled `Episodic memory could not be read.` — the browse was read; the
 * search failed, and the region above the list is where that is said.
 *
 * The refusal still outranks an *unlanded* read, which is the case the order
 * was written for: a query that has only ever errored has no `data` and no
 * `dataUpdatedAt`, so `!read` and `error` are true at once and only one of them
 * is the news.
 */
function emptyLine(memory: Memory, subject: string, nothing: string): string {
  if (memory.read) return nothing;
  if (memory.error !== null) return `${subject} could not be read.`;
  return `${subject} has not been read yet.`;
}

function Empty({ line, note }: { line: string; note?: string }) {
  return (
    <div className="flex flex-col items-center gap-1.5 py-10 text-center">
      <span className="t-body">{line}</span>
      {note && <span className="t-meta-strong">{note}</span>}
    </div>
  );
}

function EpisodicItem({ row, now }: { row: EpisodicRow; now: number }) {
  const hot = row.store === "hot";
  return (
    <li
      className="flex shrink-0 flex-col gap-0.5 py-[11px]"
      style={{ borderTop: "1px solid var(--line)" }}
    >
      {/* The handoff mutes a cold row; --fg2 rather than --muted, which is
          3.46:1 on --bg in light (index.css, .t-meta-strong). */}
      <span className="t-body line-clamp-2" style={hot ? undefined : { color: "var(--fg2)" }}>
        {row.text}
      </span>
      <span className="flex items-center gap-1.5">
        {/* Redundancy for the eye only: the meta line beside it already says
            `hot` or `cold` in words. Filled versus an empty ring, in
            `HealthStat`'s and `StateDot`'s spelling: `--accent-text` rather
            than raw `--accent` (2.34:1 on --bg in light, under the 3:1 1.4.11
            asks of a graphic that carries state) and a `--muted` ring rather
            than `--line`, which at 1.18:1 left the cold dot invisible. Both
            measured in `test/contrast.test.ts`. */}
        <span
          aria-hidden="true"
          data-testid="store-dot"
          className="h-1.5 w-1.5 shrink-0 rounded-full border"
          style={{
            background: hot ? "var(--accent-text)" : "transparent",
            borderColor: hot ? "var(--accent-text)" : "var(--muted)",
          }}
        />
        <span className="t-meta-strong">{episodicMeta(row, now)}</span>
      </span>
      {/* Decorative: the line above carries everything load-bearing. */}
      {row.entities.length > 0 && <span className="t-meta">{row.entities.join(" · ")}</span>}
    </li>
  );
}

function Episodic({ memory, now }: { memory: Memory; now: number }) {
  return (
    <>
      <div className="flex-1 overflow-y-auto px-4 pt-2.5">
        {memory.model === "503" && (
          // The rows stay on screen underneath: a refused search is no reason
          // to take the last true thing we were told away (spec §5.2). The
          // handoff's line 1 is "Embedding model is still loading", which we
          // cannot know — a 503 says only that nothing answered.
          <div
            className="mb-2.5 flex flex-col gap-1 rounded-[10px] px-3 py-2"
            style={{ background: "var(--surface)" }}
          >
            <span className="t-body">The embedder is not answering.</span>
            <span className="t-meta-strong">
              503 · search by meaning unavailable · the list below is by recency
            </span>
          </div>
        )}
        {memory.rows.length === 0 ? (
          // Nothing at all while a search is in flight — which is what
          // `searching` is for, and until now no component read it.
          // `placeholderData: keepPreviousData` hands back the *previous*
          // search's answer while the next key is pending, so a previous `[]`
          // arrives here as this search's own empty result, with `submitted`
          // already advanced to words the server has not been asked about.
          // `Nothing close enough to "<new query>".` over a request in flight is
          // the §5.2 failure the flag's own contract forbids; and the other
          // sentence is no better, since the browse underneath may be holding
          // rows. A search that has not answered has no empty state.
          memory.searching ? null : memory.searched ? (
            // `searched` is a search that *answered*, so a refused one never
            // claims the server rejected anything. The sentence quotes what the
            // server was asked, not what the field holds now.
            <Empty
              line={`Nothing close enough to "${memory.submitted}".`}
              note="searched by meaning · the server does not report what it rejected"
            />
          ) : (
            <Empty line={emptyLine(memory, "Episodic memory", "No episodic memories yet.")} />
          )
        ) : (
          <ul
            role="list"
            aria-label="Episodic memories"
            className="m-0 flex list-none flex-col p-0"
          >
            {memory.rows.map((row) => (
              <EpisodicItem key={row.key} row={row} now={now} />
            ))}
          </ul>
        )}
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          memory.submit();
        }}
        className="flex items-center gap-2.5 px-4 pt-2.5"
        style={{
          borderTop: "1px solid var(--line)",
          paddingBottom: "calc(12px + env(safe-area-inset-bottom, 0px))",
        }}
      >
        {/* 16 px or iOS zooms the whole layer on focus. Submit-only: every
            keystroke would be a recall() embedding the query on the GPU, so
            the single field submits implicitly on the keyboard's return. */}
        <input
          type="search"
          aria-label="Search memory"
          placeholder="Search by meaning"
          enterKeyHint="search"
          value={memory.query}
          onChange={(event) => memory.setQuery(event.target.value)}
          className="h-[50px] min-w-0 flex-1 rounded-[25px] border-0 px-4 text-[16px]"
          style={{ background: "var(--field)", color: "var(--fg)" }}
        />
        {/* There is no readiness probe: `unknown` until a search has answered,
            and green only for the answer that proves the embedder alive. */}
        <span
          className="t-meta-strong shrink-0"
          style={memory.model === "ok" ? { color: "var(--green-text)" } : undefined}
        >
          {`model: ${memory.model}`}
        </span>
      </form>
    </>
  );
}

/**
 * One semantic document. Which card is unfolded is this card's own business and
 * nobody else's — unlike the open routine, which `useMemory` holds because only
 * one may be open at a time and it has to survive a trip to another bench.
 * Several of these can be open at once, nothing else needs to know which, and a
 * clamp is a way of *showing* a file rather than a selection within the bench,
 * so it is fair for it to fold again on the way back.
 */
function SemanticCard({ file, now }: { file: SemanticFile; now: number }) {
  const [open, setOpen] = useState(false);
  const long = useMemo(() => estimateLines(file.content) > CLAMP_LINES, [file.content]);
  return (
    <li
      className="flex shrink-0 flex-col gap-1.5 rounded-xl p-3.5"
      style={{ background: "var(--surface)" }}
    >
      <span className="t-body font-mono">{file.name}</span>
      {/* The day as well as the clock: these are rewritten at nightly-into-
          weekly cadence, so a bare 03:00 could be a week old. */}
      <span className="t-meta-strong">{`${file.dir} · modified ${stamp(file.modified, now)}`}</span>
      <p
        className="m-0 text-[14px] leading-[1.55] break-words whitespace-pre-wrap"
        style={{ color: "var(--fg2)", ...(long && !open ? CLAMP_STYLE : {}) }}
      >
        {file.content}
      </p>
      {long && (
        <button
          type="button"
          onClick={() => setOpen((shown) => !shown)}
          className="t-meta-strong min-h-11 text-left"
          style={{ color: "var(--accent-text)" }}
        >
          {open ? "Show less" : "Show all"}
        </button>
      )}
    </li>
  );
}

function Semantic({ memory, now }: { memory: Memory; now: number }) {
  const files = memory.files;
  return (
    <div className="flex-1 overflow-y-auto px-4 pt-2.5 pb-10">
      {files.length === 0 ? (
        <Empty line={emptyLine(memory, "Semantic memory", "No semantic memory files yet.")} />
      ) : (
        <ul role="list" aria-label="Semantic files" className="m-0 flex list-none flex-col gap-3 p-0">
          {files.map((file) => (
            <SemanticCard key={`${file.dir}/${file.name}`} file={file} now={now} />
          ))}
        </ul>
      )}
    </div>
  );
}

function Routines({ memory }: { memory: Memory }) {
  return (
    <div className="flex-1 overflow-y-auto px-4 pt-2.5 pb-10">
      {memory.routines.length === 0 ? (
        <Empty line={emptyLine(memory, "Routines", "No routines learned yet.")} />
      ) : (
        <ul role="list" aria-label="Routines" className="m-0 flex list-none flex-col p-0">
          {memory.routines.map((routine) => (
            <RoutineRow
              key={routine.name}
              routine={routine}
              open={memory.openRoutine === routine.name}
              onToggle={memory.toggleRoutine}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

/** One of the scratchpad's two stat cards (handoff §6): a number, and what it counts. */
function Stat({ value, note }: { value: string; note: string }) {
  return (
    <div
      className="flex flex-1 flex-col gap-1 rounded-xl p-3.5"
      style={{ border: "1px solid var(--line)" }}
    >
      <span className="t-title font-mono">{value}</span>
      <span className="t-meta-strong">{note}</span>
    </div>
  );
}

function Scratchpad({
  scratchpad,
  consolidation,
  error,
  now,
}: {
  scratchpad: ScratchpadState | null;
  consolidation: Consolidation | null;
  /** The read's failure: `null` here is an unread pad, and a 503 is not that. */
  error: string | null;
  now: number;
}) {
  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 pt-2.5 pb-10">
      <div className="flex gap-3">
        {/* Never hidden when it is zero: an empty queue is a fact about the
            nightly consolidation, not the absence of one. */}
        {scratchpad && (
          <Stat value={String(scratchpad.pending_queue)} note="episodes queued, unscored" />
        )}
        {/* The Librarian's schedule, off the overview. `never run` rather than
            an invented stamp, and `--:--` for a pass that is not scheduled —
            `next_run_at` is null when the Librarian is not running. */}
        {consolidation && (
          <Stat
            value={consolidation.next === null ? "--:--" : hhmm(consolidation.next)}
            note={`next consolidation · last ${
              consolidation.last === null ? "never run" : stamp(consolidation.last, now)
            }`}
          />
        )}
      </div>
      {scratchpad === null ? (
        <Empty line={error === null ? "The scratchpad has not been read yet." : "The scratchpad could not be read."} />
      ) : scratchpad.content === "" ? (
        <Empty line="The scratchpad is empty." />
      ) : (
        <pre
          className="m-0 rounded-xl p-3.5 font-mono text-[12px] leading-[1.65] break-words whitespace-pre-wrap"
          style={{ background: "var(--surface)", color: "var(--fg2)" }}
        >
          {scratchpad.content}
        </pre>
      )}
    </div>
  );
}

/**
 * The Memory bench (handoff §6; spec §8 phase 3): episodic recall hot and cold,
 * the semantic documents, the routine lifecycle and the scratchpad. A pure view
 * over `useMemory`'s one state object, exactly as `ActivityBench` is over
 * `Activity` — it fetches nothing, which is what lets its tests build a state
 * rather than a `QueryClient`.
 *
 * **Read-only, and it does not pretend otherwise.** No forget, no redact, no
 * semantic edit, no promote: none of them has a route, so none of them has a
 * button here.
 *
 * It carries one live region, for its read errors, and none for the
 * connection: the Workshop's header owns that one and is the only one that
 * survives a change of bench. The two are not the same trouble — `live` in the
 * header over `redis gone` in the middle of the screen is exactly the state
 * this region exists to announce.
 */
export function MemoryBench({ memory }: MemoryBenchProps) {
  const base = useId();
  const panelId = `${base}-panel`;
  // The clock every stamp on the bench is dated against: a memory browser goes
  // back weeks, so `07:02` alone says nothing and `episodicMeta` wants a `now`.
  // Read once when the bench mounts — `DoorProvider` takes the same lazy
  // initialiser, which runs once and becomes state, where a `Date.now()` in the
  // render body would answer differently on every render — so every row agrees
  // about which day it is. It does not tick: a bench left open across midnight
  // would keep saying "earlier today" until the Workshop is next opened, and a
  // second-by-second clock in a read-only browser is motion for nothing.
  const [now] = useState(() => Date.now());
  const note = TABS.find((tab) => tab.id === memory.tab)?.note;

  // The arrows, Home and End, and the roving focus that goes with them, are
  // `tabs.ts`'s — the same keyboard the bench switcher above has, which is the
  // one thing these two controls must not differ in.
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    tabKeyDown(event, TAB_IDS, memory.tab, memory.setTab);
  }

  return (
    <>
      {/* The handoff's pills (§6), not the switcher's segments (§4): 44 px,
          radius 22, the chosen one filled `--ink` on `--paper`. */}
      <div
        role="tablist"
        aria-label="Memory"
        onKeyDown={onKeyDown}
        className="mx-4 mt-2.5 grid shrink-0 grid-cols-4 gap-2"
      >
        {TABS.map(({ id, label }) => {
          const active = id === memory.tab;
          return (
            <button
              key={id}
              id={tabId(base, id)}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={panelId}
              // Roving tabindex: one stop for the control, then the arrows.
              tabIndex={active ? 0 : -1}
              onClick={() => memory.setTab(id)}
              className="h-11 rounded-[22px] border-0 text-[13px] font-medium"
              style={{
                background: active ? "var(--ink)" : "transparent",
                color: active ? "var(--paper)" : "var(--fg2)",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>
      <p className="t-meta-strong mx-4 mt-2.5 mb-0">{note}</p>
      {/* This bench's read errors, announced. The Workshop's header speaks for
          the *connection* and cannot speak for this: a 500 from
          `/memory/routines` with the socket up leaves the header saying `live`
          while `redis gone` paints mid-screen, and a reader who cannot see the
          screen is told nothing at all. The Triggers and System benches carry
          the same region for the same reason, and none of the three duplicates
          the header — a refused read and a dropped socket are different facts.

          Mounted whether or not it has anything to say: VoiceOver can miss a
          region inserted with its text already in it. Empty, it is `sr-only`.
          `status` and not `alert` — a read that failed in the background is
          news, not an interrupt. `t-meta-strong` because this is the line that
          says part of the bench is missing. */}
      <p
        role="status"
        aria-label="Read errors"
        className={memory.error ? "t-meta-strong mx-4 mt-2.5 mb-0" : "sr-only"}
      >
        {memory.error}
      </p>
      <div
        id={panelId}
        role="tabpanel"
        aria-labelledby={tabId(base, memory.tab)}
        aria-busy={memory.loading}
        // Always on, for `Workshop.tsx`'s reason one level up: the APG makes
        // the panel's tab stop required when the panel holds nothing focusable,
        // and Scratchpad never does while Semantic does only when a file is
        // long enough to need `Show all`. A stop that appears and disappears
        // under the reader is worse than one that is always there.
        tabIndex={0}
        className="flex min-h-0 flex-1 flex-col"
      >
        {memory.tab === "episodic" && <Episodic memory={memory} now={now} />}
        {memory.tab === "semantic" && <Semantic memory={memory} now={now} />}
        {memory.tab === "routines" && <Routines memory={memory} />}
        {memory.tab === "scratchpad" && (
          <Scratchpad
            scratchpad={memory.scratchpad}
            consolidation={memory.consolidation}
            error={memory.error}
            now={now}
          />
        )}
      </div>
    </>
  );
}
