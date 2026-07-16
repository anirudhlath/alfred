import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { api, del } from "@/lib/api";
import type { DeviceInfo, IntegrationInfo, Overview, SessionInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

function Dot({ ok }: { ok: boolean }) {
  return <span className={cn("pulse-dot", ok ? "bg-ok text-ok" : "bg-bad text-bad")} />;
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="bg-card">
      <CardHeader>
        <CardTitle className="font-mono text-xs tracking-widest">{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function IntegrationRow({ integration }: { integration: IntegrationInfo }) {
  const { data, isLoading } = useQuery<{ healthy: boolean }>({
    queryKey: ["integration-status", integration.name],
    queryFn: () => api(`/api/integrations/${integration.name}/status`),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  return (
    <div className="flex items-center gap-2 font-mono text-xs">
      <Dot ok={data?.healthy ?? false} />
      <span>{integration.name}</span>
      <span className="text-muted-foreground">{integration.category}</span>
      {isLoading && <span className="ml-auto text-muted-foreground">checking…</span>}
    </div>
  );
}

export function HealthPage() {
  const qc = useQueryClient();
  const { data: overview, error: overviewError } = useQuery<Overview>({
    queryKey: ["overview"],
    queryFn: () => api("/api/admin/overview"),
  });
  const { data: sessions } = useQuery<{ sessions: SessionInfo[] }>({
    queryKey: ["sessions"],
    queryFn: () => api("/api/admin/sessions"),
  });
  const { data: devices } = useQuery<{ devices: DeviceInfo[] }>({
    queryKey: ["devices"],
    queryFn: () => api("/api/admin/devices"),
  });
  const { data: integrations } = useQuery<IntegrationInfo[]>({
    queryKey: ["integrations"],
    queryFn: () => api("/api/integrations"),
  });
  const endSession = useMutation({
    mutationFn: (id: string) => del(`/api/admin/sessions/${id}`),
    onSuccess: () => {
      toast("Session ended");
      void qc.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const spendRatio = overview?.cost ? overview.cost.spend_usd / overview.cost.cap_usd : 0;

  return (
    <div className="grid h-full grid-cols-1 content-start gap-4 overflow-y-auto p-5 md:grid-cols-2 xl:grid-cols-3">
      <Panel title="CONNECTIVITY">
        {overviewError ? (
          <p className="font-mono text-xs text-bad">
            overview unavailable: {String(overviewError)}
          </p>
        ) : (
          <div className="space-y-2 font-mono text-xs">
            <div className="flex items-center gap-2">
              <Dot ok={overview?.redis.connected ?? false} /> redis
            </div>
            <div className="flex items-center gap-2">
              <Dot ok={overview?.inference.ollama ?? false} /> ollama
            </div>
            <div className="flex items-center gap-2">
              <Dot ok={overview?.inference.lmstudio ?? false} /> lm studio
            </div>
          </div>
        )}
      </Panel>

      <Panel title="COST TODAY">
        {overviewError ? (
          <p className="font-mono text-xs text-bad">overview unavailable</p>
        ) : (
          <div className="font-mono">
            <div className={cn("text-2xl", spendRatio > 0.8 ? "text-warn" : "text-ok")}>
              ${overview?.cost?.spend_usd.toFixed(2) ?? "0.00"}
              <span className="text-xs text-muted-foreground">
                {" "}
                / ${overview?.cost?.cap_usd.toFixed(2) ?? "—"}
              </span>
            </div>
            <Progress value={Math.min(spendRatio * 100, 100)} className="mt-3" />
          </div>
        )}
      </Panel>

      <Panel title="INTEGRATIONS">
        <div className="space-y-2">
          {(integrations ?? []).map((i) => (
            <IntegrationRow key={i.name} integration={i} />
          ))}
        </div>
      </Panel>

      <Panel title="STREAMS">
        {overviewError ? (
          <p className="font-mono text-xs text-bad">overview unavailable</p>
        ) : (
          <table className="w-full font-mono text-xs">
            <tbody>
              {Object.entries(overview?.streams ?? {}).map(([name, s]) => (
                <tr key={name} className="border-b border-border/40">
                  <td className="py-1.5 text-muted-foreground">{name}</td>
                  <td className="text-right">{s.length}</td>
                  <td className="pl-3 text-right text-muted-foreground">
                    {s.last_ts ? new Date(s.last_ts * 1000).toLocaleTimeString("en-GB") : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="SESSIONS">
        <div className="space-y-2">
          {(sessions?.sessions ?? []).map((s) => (
            <div key={s.session_id} className="flex items-center gap-2 font-mono text-xs">
              <Badge variant="outline">{s.channel}</Badge>
              <span className="truncate text-muted-foreground">{s.session_id.slice(0, 8)}</span>
              <span>{s.turns} turns</span>
              <span className="text-muted-foreground">
                {Math.max(0, Math.round(s.ttl_seconds / 60))}m left
              </span>
              <Button
                size="sm"
                variant="outline"
                className="ml-auto font-mono text-[10px]"
                onClick={() => endSession.mutate(s.session_id)}
              >
                END
              </Button>
            </div>
          ))}
          {sessions?.sessions.length === 0 && (
            <p className="font-mono text-xs text-muted-foreground">No active sessions.</p>
          )}
        </div>
      </Panel>

      <Panel title="DEVICES">
        <div className="space-y-2">
          {(devices?.devices ?? []).map((d) => (
            <div key={d.device_token} className="font-mono text-xs">
              <Badge variant="outline">{d.platform ?? "?"}</Badge>{" "}
              <span className="text-muted-foreground">{d.device_token.slice(0, 12)}…</span>
              <span className="ml-2 text-muted-foreground">{d.identity}</span>
            </div>
          ))}
          {devices?.devices.length === 0 && (
            <p className="font-mono text-xs text-muted-foreground">No registered devices.</p>
          )}
        </div>
      </Panel>
    </div>
  );
}
