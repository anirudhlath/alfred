import { useState } from "react";
import { evs, hhmm } from "@/lib/format";
import type { StreamRef } from "@/lib/streams";
import { useOverview } from "@/room/useOverview";
import { useConnection } from "@/shell/ConnectionProvider";
import { Layer } from "@/shell/Layer";
import { ActivityBench } from "./ActivityBench";
import { BenchSwitcher, type Bench } from "./BenchSwitcher";
import { useActivity } from "./useActivity";

export interface WorkshopProps {
  open: boolean;
  onClose: () => void;
  /** A row asked `Why · causal thread`. The Room opens the sheet. */
  onWhy: (ref: StreamRef) => void;
}

/**
 * The Workshop (spec §4.10, §8 phase 2): the second of the two surfaces, under
 * the Room's handle. A `Layer` at the workshop level, so the Why sheet and the
 * Door both paint over it. The panel inside carries every hook, so the feed
 * is subscribed and read only while the layer is mounted — including the
 * 400 ms it takes to leave.
 */
export function Workshop({ open, onClose, onWhy }: WorkshopProps) {
  return (
    <Layer open={open} label="Workshop" durationMs={400} level="workshop">
      <WorkshopPanel onClose={onClose} onWhy={onWhy} />
    </Layer>
  );
}

interface WorkshopPanelProps {
  onClose: () => void;
  onWhy: (ref: StreamRef) => void;
}

const UNBUILT = "not built yet · phase 3";

/**
 * A fragment, not a wrapper: `Layer`'s panel is already the `flex flex-col`
 * column `ActivityBench` documents it needs, so the header, the bench's banner
 * and footer take their own height and its `flex-1` list takes what is left.
 */
function WorkshopPanel({ onClose, onWhy }: WorkshopPanelProps) {
  const [bench, setBench] = useState<Bench>("activity");
  const activity = useActivity();
  const { lastTrueAt } = useConnection();
  const overview = useOverview();

  // §5.2: live is not last-known. Not-live outranks paused; paused outranks the rate.
  const status = !activity.live
    ? `last true ${lastTrueAt ? hhmm(lastTrueAt) : "--:--"} · not live`
    : activity.paused
      ? `paused · ${activity.heldCount} new`
      : `live · ${evs(overview.data?.streams ?? {})} ev/s`;

  return (
    <>
      <header className="flex flex-col gap-2.5 px-4 pt-[62px]">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={onClose}
            className="-ml-2 flex h-11 items-center gap-1.5 px-2 text-[15px] font-medium"
            style={{ color: "var(--accent)" }}
          >
            <span
              aria-hidden="true"
              className="h-[9px] w-[9px] rotate-45"
              style={{ borderLeft: "1.5px solid currentColor", borderBottom: "1.5px solid currentColor" }}
            />
            Room
          </button>
          <span className="t-meta" data-testid="workshop-status">
            {status}
          </span>
        </div>
        <BenchSwitcher bench={bench} onChange={setBench} />
      </header>
      {bench === "activity" ? (
        <ActivityBench activity={activity} onWhy={onWhy} />
      ) : (
        <p className="t-meta flex flex-1 items-center justify-center">{UNBUILT}</p>
      )}
    </>
  );
}
