import type { CSSProperties, ReactNode } from "react";

/** At or under this many seconds left, the arc goes from accent to paper (handoff). */
export const DANGER_SECONDS = 30;

export interface FuseRingProps {
  /** 0–100, the fraction of the TTL still to run. */
  percent: number;
  size: 34 | 168;
  /** Under thirty seconds the arc goes from accent to paper (handoff). */
  danger: boolean;
  children?: ReactNode;
}

/**
 * The fuse, at both the sizes the design uses: 34 px in the banner, 168 px in
 * the Door. The arc and its mask are CSS (`.fuse-arc`, `.fuse-34`, `.fuse-168`);
 * this only decides how far round it has gone and what colour that is.
 */
export function FuseRing({ percent, size, danger, children }: FuseRingProps) {
  const pct = percent.toFixed(1);
  const arcStyle = {
    "--fuse-pct": `${pct}%`,
    "--fuse-color": danger ? "var(--paper)" : "var(--accent)",
  } as CSSProperties;

  return (
    <div
      data-testid="fuse-ring"
      className="relative flex shrink-0 items-center justify-center"
      style={{ width: `${size}px`, height: `${size}px` }}
    >
      <div
        aria-hidden="true"
        data-testid="fuse-arc"
        data-percent={pct}
        className={`fuse-arc fuse-${size} absolute inset-0 rounded-full`}
        style={arcStyle}
      />
      {children}
    </div>
  );
}
