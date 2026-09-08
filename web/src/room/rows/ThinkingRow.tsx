const DELAYS = [0, 0.2, 0.4];

/**
 * Three breathing dots, staggered, and the name of the mind that is busy. A
 * status, so a screen reader hears that Alfred is working, not silence.
 */
export function ThinkingRow({ detail }: { detail: string }) {
  return (
    <div role="status" className="flex flex-col gap-[7px]">
      <div
        aria-hidden="true"
        data-testid="thinking-dots"
        className="flex h-[22px] items-center gap-1"
      >
        {DELAYS.map((delay) => (
          <span
            key={delay}
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: "var(--accent)",
              animation: `breathe 1.2s ${delay}s ease-in-out infinite`,
            }}
          />
        ))}
      </div>
      <div className="t-meta">conscious mind · {detail}</div>
    </div>
  );
}
