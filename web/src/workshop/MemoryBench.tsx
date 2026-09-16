import { useId, useState, type KeyboardEvent } from "react";
import { hhmm } from "@/lib/format";
import { episodicMeta, type EpisodicRow, type Scratchpad, type SemanticFile } from "@/lib/memory";
import { RoutineRow } from "./RoutineRow";
import type { Memory, MemoryTab } from "./useMemory";

export interface MemoryBenchProps {
  memory: Memory;
}

const TABS: { id: MemoryTab; label: string }[] = [
  { id: "episodic", label: "Episodic" },
  { id: "semantic", label: "Semantic" },
  { id: "routines", label: "Routines" },
  { id: "scratchpad", label: "Scratchpad" },
];

/**
 * The single most important sentence on the bench. The episodic endpoint passes
 * `update_stats=False` for it: a memory browser that quietly reinforced
 * whatever you looked at would corrupt the very decay it is showing you.
 */
const BROWSE_NOTE =
  "Browsing here does not count as recall. Nothing you open is kept warmer or colder for it.";

/** How many lines of a semantic file are shown before `Show all`. */
const CLAMP_LINES = 12;
/**
 * Roughly what a phone column fits on one line at 14 px. Only an estimate, and
 * deliberately a generous one: offering `Show all` on a file that turned out to
 * fit costs a reader one no-op tap, while withholding it on one that did not
 * hides the end of the document behind a clamp with no way past it.
 */
const CLAMP_CHARS = 60;

function overflows(content: string): boolean {
  return content.split("\n").length > CLAMP_LINES || content.length > CLAMP_LINES * CLAMP_CHARS;
}

/** The empty-list block every tab uses: a sentence, and the evidence under it. */
function Empty({ line, note }: { line: string; note?: string }) {
  return (
    <div className="flex flex-col items-center gap-1.5 py-10 text-center">
      <span className="t-body">{line}</span>
      {note && <span className="t-meta-strong">{note}</span>}
    </div>
  );
}

function EpisodicItem({ row }: { row: EpisodicRow }) {
  const hot = row.store === "hot";
  return (
    <li className="flex shrink-0 flex-col gap-0.5 py-[11px]" style={{ borderTop: "1px solid var(--line)" }}>
      <span className="t-body line-clamp-2">{row.text}</span>
      <span className="flex items-center gap-1.5">
        {/* Redundancy for the eye only: the meta line beside it already says
            `hot` or `cold` in words. */}
        <span
          aria-hidden="true"
          data-testid="store-dot"
          className="h-1.5 w-1.5 shrink-0 rounded-full border"
          style={{
            background: hot ? "var(--accent)" : "transparent",
            borderColor: hot ? "transparent" : "var(--line)",
          }}
        />
        <span className="t-meta-strong">{episodicMeta(row)}</span>
      </span>
      {/* Decorative: the line above carries everything load-bearing. */}
      {row.entities.length > 0 && <span className="t-meta">{row.entities.join(" · ")}</span>}
    </li>
  );
}

function Episodic({ memory }: { memory: Memory }) {
  return (
    <>
      <div className="flex-1 overflow-y-auto px-4 pt-2.5">
        <p className="t-meta-strong m-0">{BROWSE_NOTE}</p>
        {memory.model === "503" && (
          // The rows stay on screen underneath: a refused search is no reason
          // to take the last true thing we were told away (spec §5.2).
          <p
            className="t-body mt-2.5 mb-0 rounded-[10px] px-3 py-2"
            style={{ background: "var(--surface)" }}
          >
            The embedder is not answering, so meaning search is off. Browsing still works.
          </p>
        )}
        {memory.rows.length === 0 ? (
          // `searched` is a search that *answered*, so a pending or refused one
          // never claims the server rejected anything.
          memory.searched ? (
            <Empty
              line="Nothing scored above the server's threshold."
              note="searched by meaning · the server does not report what it rejected"
            />
          ) : (
            <Empty line="No episodic memories yet." />
          )
        ) : (
          <ul role="list" aria-busy={memory.searching} className="m-0 mt-2.5 flex list-none flex-col p-0">
            {memory.rows.map((row) => (
              <EpisodicItem key={row.key} row={row} />
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

function SemanticCard({ file }: { file: SemanticFile }) {
  // View state, not server state: which file is unfolded is nobody's business
  // but this card's, and it is fair for it to fold again on the way back.
  const [open, setOpen] = useState(false);
  const clampable = overflows(file.content);
  return (
    <li className="flex shrink-0 flex-col gap-1.5 rounded-xl p-3.5" style={{ background: "var(--surface)" }}>
      <span className="t-body font-mono">{file.name}</span>
      <span className="t-meta-strong">{`${file.dir} · modified ${hhmm(file.modified)}`}</span>
      <pre
        className={`m-0 font-sans text-[14px] leading-[1.55] break-words whitespace-pre-wrap${
          clampable && !open ? " line-clamp-[12]" : ""
        }`}
        style={{ color: "var(--fg2)" }}
      >
        {file.content}
      </pre>
      {clampable && (
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

function Semantic({ files }: { files: SemanticFile[] }) {
  return (
    <div className="flex-1 overflow-y-auto px-4 pt-2.5 pb-10">
      {files.length === 0 ? (
        <Empty line="No semantic memory files yet." />
      ) : (
        <ul role="list" aria-label="Semantic files" className="m-0 flex list-none flex-col gap-3 p-0">
          {files.map((file) => (
            <SemanticCard key={`${file.dir}/${file.name}`} file={file} />
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
        <Empty line="No routines learned yet." />
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

function ScratchpadView({ scratchpad }: { scratchpad: Scratchpad | null }) {
  // Null is "not read yet", which is the Workshop header's business to say.
  if (scratchpad === null) return null;
  return (
    <div className="flex flex-1 flex-col gap-1.5 overflow-y-auto px-4 pt-2.5 pb-10">
      {/* Never hidden when it is zero: an empty queue is a fact about the
          nightly consolidation, not the absence of one. */}
      <span className="t-meta-strong">{`${scratchpad.pending_queue} in the queue`}</span>
      {scratchpad.content === "" ? (
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
 * The bench carries no live region of its own — the Workshop's header status
 * span is the one that survives a change of bench, and a second region here
 * would announce the same trouble twice.
 */
export function MemoryBench({ memory }: MemoryBenchProps) {
  const base = useId();
  const panelId = `${base}-panel`;
  const tabId = (tab: MemoryTab) => `${base}-${tab}`;
  const index = TABS.findIndex((entry) => entry.id === memory.tab);

  // Automatic activation, wrapping — `BenchSwitcher`'s keyboard, because this
  // is the same control one level in and two segmented controls that behave
  // differently would be worse than either.
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (step === 0) return;
    // Otherwise the arrow also scrolls the bench underneath.
    event.preventDefault();
    const next = (index + step + TABS.length) % TABS.length;
    memory.setTab(TABS[next].id);
    event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  }

  return (
    <>
      <div
        role="tablist"
        aria-label="Memory"
        onKeyDown={onKeyDown}
        className="mx-4 mt-2.5 grid h-11 shrink-0 grid-cols-4 gap-[3px] rounded-xl p-[6px]"
        style={{ background: "var(--surface)" }}
      >
        {TABS.map(({ id, label }) => {
          const active = id === memory.tab;
          return (
            <button
              key={id}
              id={tabId(id)}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={panelId}
              // Roving tabindex: one stop for the control, then the arrows.
              tabIndex={active ? 0 : -1}
              onClick={() => memory.setTab(id)}
              // 32 px of segment inside a 44 px track, and the `after` box
              // gives the other 12 px back to the finger.
              className="relative rounded-lg border-0 text-[13px] font-medium after:absolute after:inset-x-0 after:-inset-y-1.5 after:content-['']"
              style={{
                background: active ? "var(--field)" : "transparent",
                color: active ? "var(--fg)" : "var(--fg2)",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>
      {/* Not a region: see the component note. `t-meta-strong` because this is
          the line saying part of the bench is missing. */}
      {memory.error && <p className="t-meta-strong mx-4 mt-2.5 mb-0">{memory.error}</p>}
      <div
        id={panelId}
        role="tabpanel"
        aria-labelledby={tabId(memory.tab)}
        aria-busy={memory.loading}
        className="flex min-h-0 flex-1 flex-col"
      >
        {memory.tab === "episodic" && <Episodic memory={memory} />}
        {memory.tab === "semantic" && <Semantic files={memory.files} />}
        {memory.tab === "routines" && <Routines memory={memory} />}
        {memory.tab === "scratchpad" && <ScratchpadView scratchpad={memory.scratchpad} />}
      </div>
    </>
  );
}
