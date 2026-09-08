import { evs, hhmm, usd } from "@/lib/format";
import type { Overview } from "@/lib/types";

export interface StatusLineProps {
  overview: Overview | undefined;
  online: boolean;
  lastTrueAt: Date | null;
}

export function StatusLine({ overview, online, lastTrueAt }: StatusLineProps) {
  const streams = overview?.streams ?? {};
  // Every catalogued stream empty means nothing has ever happened here. An *absent*
  // streams map means Redis is down, which is a different thing entirely.
  const firstRun =
    Object.keys(streams).length > 0 && Object.values(streams).every((s) => s.length === 0);

  const cost = overview?.cost;
  const cloud = cost ? `cloud ${usd(cost.spend_usd)} / ${usd(cost.cap_usd)}` : "cloud —";

  const lastMs = overview?.reflex?.last_ms;
  const reflex =
    online && !firstRun && lastMs != null ? `reflex ${Math.round(lastMs)} ms` : "reflex ok";

  // Never a clock read during render: `lastTrueAt` is stamped on every socket open
  // and every successful poll, and an unknown clock says so.
  const stamp = lastTrueAt ? hhmm(lastTrueAt) : "--:--";

  const parts = firstRun
    ? ["first run", cloud, reflex, `${evs(streams)} ev/s`]
    : online
      ? [stamp, cloud, reflex, `${evs(streams)} ev/s`]
      : [`last true ${stamp}`, cloud, reflex];

  return <div className="t-status">{parts.join(" · ")}</div>;
}
