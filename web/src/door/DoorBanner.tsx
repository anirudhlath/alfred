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

      {/* The banner's own `--paper`, not the accent. Accent on --ink is
          1.77:1 in the dark theme — the very number index.css's --on-accent
          comment names as the thing never to do — and `--accent-text` is no
          help here (1.77 dark, 2.82 light): it is darkened *for paper*, and
          --ink in the light theme is the dark colour. Paper on ink is 13.46:1
          dark / 13.95:1 light, and `font-medium` already carries the
          affordance without borrowing a colour it cannot afford. */}
      <div className="text-[13px] font-medium" style={{ color: "var(--paper)" }}>
        Open
      </div>
    </button>
  );
}
