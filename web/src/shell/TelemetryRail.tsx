import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { api } from "@/lib/api";
import { CATEGORY_CLASS, categorize, summarize, timeOf } from "@/lib/format";
import type { Overview } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useAlfred } from "./AlfredProvider";

/** Categories whose arrival can change cost, DND state, or session counts. */
const VITAL_CATEGORIES = new Set(["conscious", "trigger", "user"]);

function Vital({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-md border border-border px-2 py-1.5">
      <div className="text-[8px] tracking-wider text-muted-foreground">{label}</div>
      <div className={cn("font-mono text-sm", tone ?? "text-foreground")}>{value}</div>
    </div>
  );
}

export function TelemetryRail() {
  const { feed, telemetryStatus } = useAlfred();
  const [collapsed, setCollapsed] = useState(false);
  const { data: overview, refetch } = useQuery<Overview>({
    queryKey: ["overview"],
    queryFn: () => api("/api/admin/overview"),
  });

  // Throttle state: track the last time we issued a refetch (performance.now() — effect-scope only).
  // Initialised to -Infinity so the first qualifying entry always passes the 5 s gate.
  const lastRefetchAt = useRef<number>(-Infinity);

  // Event-driven refresh: only refetch when the newest feed entry belongs to a
  // category that can change cost, DND, or session counts (conscious / trigger / user).
  // Home and reflex churn must NOT trigger refetches — they have no effect on vitals.
  // Throttled to at most once per 5 s to avoid request storms during smart-home bursts.
  const feedHead = feed[0];
  useEffect(() => {
    if (!feedHead) return;
    const category = categorize(feedHead.stream, feedHead.event);
    if (!VITAL_CATEGORIES.has(category)) return;
    const now = performance.now();
    if (now - lastRefetchAt.current < 5000) return;
    lastRefetchAt.current = now;
    void refetch();
  }, [feedHead, refetch]);

  // Count entries that arrived within 60 s of the most-recent feed entry.
  // Pure computation — avoids Date.now() in render.
  const newestMs = feed.length > 0 ? Number(feed[0].id.split("-")[0]) : 0;
  const perMinute = feed.filter(
    (e) => newestMs - Number(e.id.split("-")[0]) < 60_000,
  ).length;

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        title="Show telemetry"
        className="border-l border-border bg-panel px-1.5 text-muted-foreground hover:text-foreground"
      >
        <PanelRightOpen className="size-4" />
      </button>
    );
  }

  return (
    <aside className="flex w-60 flex-col border-l border-border bg-panel">
      <div className="flex h-11 items-center justify-between border-b border-border px-3">
        <span className="font-mono text-[9px] tracking-[0.2em] text-muted-foreground">SYSTEM</span>
        <button onClick={() => setCollapsed(true)} title="Hide telemetry" className="text-muted-foreground hover:text-foreground">
          <PanelRightClose className="size-3.5" />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-1.5 p-3">
        <Vital
          label="COST TODAY"
          value={overview?.cost ? `$${overview.cost.spend_usd.toFixed(2)}` : "—"}
          tone={overview?.cost && overview.cost.spend_usd / overview.cost.cap_usd > 0.8 ? "text-warn" : "text-ok"}
        />
        <Vital label="SESSIONS" value={String(overview?.counts.sessions ?? "—")} />
        <Vital label="EVENTS/MIN" value={String(perMinute)} tone="text-reflex" />
        <Vital
          label="DND"
          value={overview?.dnd.active ? "ON" : "OFF"}
          tone={overview?.dnd.active ? "text-warn" : "text-muted-foreground"}
        />
      </div>
      <div className="flex items-center justify-between px-3 pb-1">
        <span className="font-mono text-[9px] tracking-[0.2em] text-muted-foreground">LIVE</span>
        <span className={cn("pulse-dot", telemetryStatus === "online" ? "bg-reflex text-reflex" : "bg-bad text-bad")} />
      </div>
      <div className="flex-1 space-y-2 overflow-hidden px-3 pb-3">
        {feed.slice(0, 14).map((entry, i) => {
          const category = categorize(entry.stream, entry.event);
          return (
            <Link
              key={entry.id + entry.stream}
              to={`/activity#${entry.id}`}
              className="block font-mono text-[9px] leading-snug text-foreground/70 hover:text-foreground"
              style={{ opacity: Math.max(0.25, 1 - i * 0.06) }}
            >
              <span className="text-muted-foreground/60">{timeOf(entry.id)}</span>{" "}
              <span className={CATEGORY_CLASS[category]}>{category}</span>
              <br />
              {summarize(entry.stream, entry.event)}
            </Link>
          );
        })}
        {feed.length === 0 && (
          <p className="font-mono text-[9px] text-muted-foreground">Waiting for activity…</p>
        )}
      </div>
    </aside>
  );
}
