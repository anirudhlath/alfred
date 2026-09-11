export type Bench = "activity" | "memory" | "triggers" | "system";

const BENCHES: { id: Bench; label: string }[] = [
  { id: "activity", label: "Activity" },
  { id: "memory", label: "Memory" },
  { id: "triggers", label: "Triggers" },
  { id: "system", label: "System" },
];

export interface BenchSwitcherProps {
  bench: Bench;
  onChange: (bench: Bench) => void;
}

/**
 * The handoff's four-up segmented control (README §4, Workshop header): a
 * `surface` track, `field` under the chosen bench, 44 px tall so every bench is
 * a legal target.
 *
 * The handoff asks for `muted` on the inactive three; this uses `--fg2`
 * instead. `--muted` on `--surface` is 4.53:1 in the dark theme but **3.20:1 in
 * light** — under AA for 13 px text, the same trap Tasks 5 and 6 found on
 * `--bg` (3.46:1). `--fg2` is 9.36:1 dark / 7.66:1 light and still reads a
 * clear step below the chosen bench's `--fg`.
 */
export function BenchSwitcher({ bench, onChange }: BenchSwitcherProps) {
  return (
    <div
      role="tablist"
      aria-label="Bench"
      className="grid h-11 grid-cols-4 gap-[3px] rounded-xl p-[3px]"
      style={{ background: "var(--surface)" }}
    >
      {BENCHES.map(({ id, label }) => {
        const active = id === bench;
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(id)}
            className="rounded-[9px] border-0 text-[13px] font-medium"
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
  );
}
