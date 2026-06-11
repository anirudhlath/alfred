import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Markdown from "react-markdown";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { EpisodicEntry, Routine, SemanticFile } from "@/lib/types";

/** Format significance/score — value may be string or number from Redis hash decode. */
function fmtSig(val: unknown): string {
  const n = typeof val === "string" ? parseFloat(val) : Number(val);
  return isNaN(n) ? "—" : n.toFixed(2);
}

function Episodic() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const { data, isFetching } = useQuery<{ entries: EpisodicEntry[] }>({
    queryKey: ["episodic", query],
    queryFn: () =>
      api(`/api/admin/memory/episodic${query ? `?q=${encodeURIComponent(query)}` : ""}`),
  });
  return (
    <div className="space-y-3">
      <Input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && setQuery(q)}
        placeholder="Semantic search (Enter) — empty shows recent"
        className="max-w-md bg-card font-mono text-xs"
      />
      {isFetching && <p className="font-mono text-xs text-muted-foreground">searching…</p>}
      <div className="space-y-2">
        {(data?.entries ?? []).map((e, i) => (
          <div key={i} className="rounded-md border border-border bg-card p-3">
            <div className="flex items-center gap-2 font-mono text-[10px]">
              <Badge
                variant="outline"
                className={e.store === "hot" ? "text-reflex" : "text-home"}
              >
                {e.store}
              </Badge>
              <span className="text-memory">
                sig {fmtSig((e as Record<string, unknown>).significance ?? e.score)}
              </span>
              <span className="text-muted-foreground">
                {String((e as Record<string, unknown>).source ?? "")}
              </span>
            </div>
            <p className="mt-1.5 text-sm text-foreground/90">
              {String(
                (e as Record<string, unknown>).content ??
                  (e as Record<string, unknown>).summary ??
                  "",
              )}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function Semantic() {
  const { data } = useQuery<{ files: SemanticFile[] }>({
    queryKey: ["semantic"],
    queryFn: () => api("/api/admin/memory/semantic"),
  });
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {(data?.files ?? []).map((f) => (
        <div key={`${f.dir}/${f.name}`} className="rounded-md border border-border bg-card p-4">
          <div className="mb-2 font-mono text-[10px] text-muted-foreground">
            {f.dir}/{f.name}
          </div>
          {/* @tailwindcss/typography not installed — using plain styled wrapper */}
          <div className="space-y-2 text-sm leading-relaxed text-foreground/90 [&_h1]:mb-1 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mb-1 [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:font-semibold [&_li]:ml-4 [&_li]:list-disc [&_p]:text-foreground/80 [&_strong]:font-semibold [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-[11px]">
            <Markdown>{f.content.replace(/^---[\s\S]*?---/, "")}</Markdown>
          </div>
        </div>
      ))}
    </div>
  );
}

const ROUTINE_TONE: Record<Routine["state"], string> = {
  active: "text-ok",
  candidate: "text-warn",
  dormant: "text-muted-foreground",
  archived: "text-bad",
};

function Routines() {
  const { data } = useQuery<{ routines: Routine[] }>({
    queryKey: ["routines"],
    queryFn: () => api("/api/admin/memory/routines"),
  });
  return (
    <div className="space-y-2">
      {(data?.routines ?? []).map((r) => (
        <div key={r.name} className="rounded-md border border-border bg-card p-3 font-mono text-xs">
          <div className="flex items-center gap-2">
            <span className="text-sm text-foreground">{r.name}</span>
            <Badge variant="outline" className={ROUTINE_TONE[r.state]}>
              {r.state}
            </Badge>
            <span className="text-memory">conf {r.confidence.toFixed(2)}</span>
            <span className="ml-auto text-muted-foreground">{r.trigger_pattern}</span>
          </div>
          <ol className="mt-2 list-decimal pl-5 text-muted-foreground">
            {r.steps.map((s, i) => (
              <li key={i}>{s.description}</li>
            ))}
          </ol>
        </div>
      ))}
      {data?.routines.length === 0 && (
        <p className="font-mono text-xs text-muted-foreground">No routines learned yet.</p>
      )}
    </div>
  );
}

function Scratchpad() {
  const { data } = useQuery<{ content: string; pending_queue: number }>({
    queryKey: ["scratchpad"],
    queryFn: () => api("/api/admin/memory/scratchpad"),
  });
  return (
    <div>
      <p className="mb-2 font-mono text-[10px] text-muted-foreground">
        {data?.pending_queue ?? 0} observations queued for drain
      </p>
      <pre className="overflow-x-auto rounded-md border border-border bg-card p-4 font-mono text-[11px] leading-relaxed text-foreground/80">
        {data?.content || "Scratchpad is empty."}
      </pre>
    </div>
  );
}

export function MemoryPage() {
  return (
    <div className="h-full overflow-y-auto p-5">
      <Tabs defaultValue="episodic">
        <TabsList className="font-mono text-xs">
          <TabsTrigger value="episodic">EPISODIC</TabsTrigger>
          <TabsTrigger value="semantic">SEMANTIC</TabsTrigger>
          <TabsTrigger value="routines">ROUTINES</TabsTrigger>
          <TabsTrigger value="scratchpad">SCRATCHPAD</TabsTrigger>
        </TabsList>
        <TabsContent value="episodic" className="mt-4">
          <Episodic />
        </TabsContent>
        <TabsContent value="semantic" className="mt-4">
          <Semantic />
        </TabsContent>
        <TabsContent value="routines" className="mt-4">
          <Routines />
        </TabsContent>
        <TabsContent value="scratchpad" className="mt-4">
          <Scratchpad />
        </TabsContent>
      </Tabs>
    </div>
  );
}
