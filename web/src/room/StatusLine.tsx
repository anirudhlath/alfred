import { evs, hhmm, usd } from "@/lib/format";
import type { Overview } from "@/lib/types";
import { isFirstRun } from "@/room/useOverview";

export interface StatusLineProps {
  overview: Overview | undefined;
  online: boolean;
  lastTrueAt: Date | null;
}

export function StatusLine({ overview, online, lastTrueAt }: StatusLineProps) {
  const streams = overview?.streams ?? {};
  const firstRun = isFirstRun(overview);

  const cost = overview?.cost;
  const cloud = cost ? `cloud ${usd(cost.spend_usd)} / ${usd(cost.cap_usd)}` : "cloud —";

  const lastMs = overview?.reflex?.last_ms;
  const reflex =
    online && !firstRun && lastMs != null ? `reflex ${Math.round(lastMs)} ms` : "reflex ok";

  // Never a clock read during render: `lastTrueAt` is stamped on every socket open
  // and every successful poll, and an unknown clock says so.
  const stamp = lastTrueAt ? hhmm(lastTrueAt) : "--:--";

  // Offline outranks first run: §5.2's "live is not last-known" has no exception
  // for a house where nothing has happened yet.
  const parts = online
    ? [firstRun ? "first run" : stamp, cloud, reflex, `${evs(streams)} ev/s`]
    : [`last true ${stamp}`, cloud, reflex];

  return <div className="t-status">{parts.join(" · ")}</div>;
}
