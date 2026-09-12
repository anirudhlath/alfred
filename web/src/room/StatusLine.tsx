import { hhmm, usd } from "@/lib/format";
import type { Overview } from "@/lib/types";
import { isFirstRun, rateText } from "@/room/useOverview";

export interface StatusLineProps {
  overview: Overview | undefined;
  online: boolean;
  lastTrueAt: Date | null;
}

export function StatusLine({ overview, online, lastTrueAt }: StatusLineProps) {
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

  // `—` for a rate it has not read, as `cloud —` and `reflex —` say beside it;
  // `rateText` owns the rule, and the Workshop's header asks the same function.
  const rate = rateText(overview);

  // Offline outranks first run: §5.2's "live is not last-known" has no exception
  // for a house where nothing has happened yet.
  const parts = online
    ? [firstRun ? "first run" : stamp, cloud, reflex, rate]
    : [`last true ${stamp}`, cloud, reflex];

  return <div className="t-status">{parts.join(" · ")}</div>;
}
