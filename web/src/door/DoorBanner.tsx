import { fusePercent, fuseRemaining, type TrackedAction } from "@/lib/actions";
import { humaniseTool, mmss } from "@/lib/format";
import { DANGER_SECONDS, FuseRing } from "@/door/FuseRing";

export interface DoorBannerProps {
  tracked: TrackedAction;
  /** Epoch ms from `useDoor().now`, so every fuse on screen agrees. */
  now: number;
  onOpen: () => void;
}

/**
 * Above the composer while an approval waits. Ink on paper, so it reads as the
 * Door's own colour arriving early — and the whole bar is the tap target, which
 * is how a 34 px ring and a 13 px word can sit in a 44 pt control.
 */
export function DoorBanner({ tracked, now, onOpen }: DoorBannerProps) {
  const remaining = fuseRemaining(tracked.action, now);
  const percent = fusePercent(tracked.action, now);

  return (
    <button
      type="button"
      onClick={onOpen}
      className="relative z-[1] mx-4 mb-2.5 flex items-center gap-3.5 rounded-2xl border-0 px-4 py-3.5 text-left"
      style={{ background: "var(--ink)", color: "var(--paper)" }}
    >
      <FuseRing percent={percent} size={34} danger={remaining <= DANGER_SECONDS} />

      <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
        <div className="text-[16px] font-medium">{humaniseTool(tracked.action.tool_name)}</div>
        {/* Not `.t-meta`: that class pins the colour to --muted, which is a Room
            token and disappears against ink. */}
        <div className="font-mono text-[11px] leading-[1.5]" style={{ color: "var(--paper-muted)" }}>
          expires in {mmss(remaining)} · asked by Alfred, for you
        </div>
      </div>

      {/* Inherits the button's `--paper` — 13.46:1 dark / 13.95:1 light on ink
          — and says so by carrying no colour of its own. It had a `color:
          var(--paper)` of its own, which changed nothing and only read as a
          decision.
          Not the accent, and not `--accent-text`: accent on --ink is 1.77:1 in
          the dark theme, the very number index.css's --on-accent comment names
          as the thing never to do, and --accent-text is darkened *for paper*
          (1.77 dark, 2.82 light on ink). What is left to separate `Open` from
          the tool name above it is position, size and weight — no colour. An
          `--accent-on-ink` token would settle it, and is tracked with
          `FuseRing` in `docs/backlog/low/pwa-phase2-followups.md` §8 rather
          than invented here. */}
      <div className="text-[13px] font-medium">Open</div>
    </button>
  );
}
