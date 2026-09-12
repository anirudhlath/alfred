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

  // A house never reached has no reflex to vouch for: `—`, as `cloud —` says
  // when there is no cost record. With an overview in hand, `ok` is the
  // handoff's word for a reflex that reports nothing to measure.
  const lastMs = overview?.reflex?.last_ms;
  const reflex = !overview
    ? "reflex —"
    : online && !firstRun && lastMs != null
      ? `reflex ${Math.round(lastMs)} ms`
      : "reflex ok";

  // Never a clock read during render: `lastTrueAt` is stamped on every socket open
  // and every successful poll, and an unknown clock says so.
  const stamp = lastTrueAt ? hhmm(lastTrueAt) : "--:--";

  // An overview never read is not a silent house: `evs({})` is a bare `0`
  // (format.ts), which would report a quiet stream on every cold open. `—`, as
  // `cloud —` and `reflex —` already say beside it.
  const rate = overview ? `${evs(streams)} ev/s` : "— ev/s";

  // Offline outranks first run: §5.2's "live is not last-known" has no exception
  // for a house where nothing has happened yet.
  const parts = online
    ? [firstRun ? "first run" : stamp, cloud, reflex, rate]
    : [`last true ${stamp}`, cloud, reflex];

  return <div className="t-status">{parts.join(" · ")}</div>;
}
