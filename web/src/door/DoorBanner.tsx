import { fuseRemaining, type TrackedAction } from "@/lib/actions";
import { humaniseTool, mmss } from "@/lib/format";
import { FuseRing } from "@/door/FuseRing";

/** Under this the ring changes colour — the handoff's only "hurry" signal. */
const DANGER_SECONDS = 30;

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
  const ttl = tracked.action.ttl_seconds;
  // `remaining` is never negative; the top clamp is for a device clock behind
  // the server's, where `expires_at` is further off than the TTL.
  const percent = ttl > 0 ? Math.min(100, (remaining / ttl) * 100) : 0;

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

      <div className="text-[13px] font-medium" style={{ color: "var(--accent)" }}>
        Open
      </div>
    </button>
  );
}
