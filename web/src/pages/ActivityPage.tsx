import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { CATEGORY_CLASS, categorize, summarize, timeOf, type SourceCategory } from "@/lib/format";
import type { StreamPage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useAlfredFeed } from "@/shell/AlfredProvider";
import type { FeedEntry } from "@/shell/AlfredProvider";
import { EventInspector } from "./EventInspector";

function compareIdsDesc(a: string, b: string): number {
  const [amsStr, aseqStr] = a.split("-");
  const [bmsStr, bseqStr] = b.split("-");
  const ams = parseInt(amsStr, 10);
  const bms = parseInt(bmsStr, 10);
  if (ams !== bms) return bms - ams;
  return parseInt(bseqStr, 10) - parseInt(aseqStr, 10);
}

const STREAMS = [
  "events", "actions", "user_requests", "user_responses",
  "reflex_observations", "notifications", "home_state", "home_action_results",
];

export function ActivityPage() {
  const feed = useAlfredFeed();
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState<FeedEntry[]>([]);
  const [stream, setStream] = useState<string | null>(null);
  const [selected, setSelected] = useState<FeedEntry | null>(null);

  const { data: history } = useQuery<StreamPage>({
    queryKey: ["stream-history", stream],
    queryFn: () => api(`/api/admin/streams/${stream}?count=100`),
    enabled: stream !== null,
  });

  const live = paused ? frozen : feed;
  const entries = useMemo(() => {
    if (stream === null) return live;
    const backfill = (history?.entries ?? []).map((e) => ({ stream: stream, ...e }));
    const liveFiltered = live.filter((e) => e.stream === stream);
    const seen = new Set(liveFiltered.map((e) => e.id));
    const merged = [...liveFiltered, ...backfill.filter((e) => !seen.has(e.id))];
    return merged.sort((a, b) => compareIdsDesc(a.id, b.id));
  }, [live, stream, history]);

  const togglePause = () => {
    if (!paused) setFrozen(feed);
    setPaused(!paused);
  };

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-1.5 border-b border-border p-3">
          <Button variant="outline" size="sm" onClick={togglePause} className="font-mono text-xs">
            {paused ? <Play className="size-3" /> : <Pause className="size-3" />}
            {paused ? "RESUME" : "PAUSE"}
          </Button>
          <Badge
            variant={stream === null ? "default" : "outline"}
            className="cursor-pointer font-mono text-[10px]"
            onClick={() => setStream(null)}
          >
            all
          </Badge>
          {STREAMS.map((s) => (
            <Badge
              key={s}
              variant={stream === s ? "default" : "outline"}
              className="cursor-pointer font-mono text-[10px]"
              onClick={() => setStream(s)}
            >
              {s}
            </Badge>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto font-mono text-xs">
          {entries.map((entry) => {
            const category: SourceCategory = categorize(entry.stream, entry.event);
            return (
              <button
                key={`${entry.stream}-${entry.id}`}
                onClick={() => setSelected(entry)}
                className="flex w-full items-baseline gap-3 border-b border-border/40 px-4 py-2 text-left hover:bg-card"
              >
                <span className="text-muted-foreground/60">{timeOf(entry.id)}</span>
                <span className={cn("w-20 shrink-0", CATEGORY_CLASS[category])}>{category}</span>
                <span className="w-36 shrink-0 truncate text-muted-foreground">{entry.stream}</span>
                <span className="truncate text-foreground/80">{summarize(entry.stream, entry.event)}</span>
              </button>
            );
          })}
          {entries.length === 0 && (
            <p className="p-6 text-muted-foreground">No activity yet — the system is quiet.</p>
          )}
        </div>
      </div>
      <EventInspector entry={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
