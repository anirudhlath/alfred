import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { api, post } from "@/lib/api";
import { timeOf } from "@/lib/format";
import type { DeferredNotification, Overview, Trigger } from "@/lib/types";

export function TriggersPage() {
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["triggers"] });
    void qc.invalidateQueries({ queryKey: ["overview"] });
    void qc.invalidateQueries({ queryKey: ["deferred"] });
  };

  const { data: triggers } = useQuery<{ triggers: Trigger[] }>({
    queryKey: ["triggers"], queryFn: () => api("/api/admin/triggers"),
  });
  const { data: overview } = useQuery<Overview>({
    queryKey: ["overview"], queryFn: () => api("/api/admin/overview"),
  });
  const { data: deferred } = useQuery<{ notifications: DeferredNotification[] }>({
    queryKey: ["deferred"], queryFn: () => api("/api/admin/notifications/deferred"),
  });
  const { data: history } = useQuery<{ entries: { id: string; event: Record<string, unknown> }[] }>({
    queryKey: ["notification-history"],
    queryFn: () => api("/api/admin/streams/notifications?count=20"),
  });

  const setEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      post(`/api/admin/triggers/${id}/enabled`, { enabled }),
    // The mutation is queued to the triggers process (applied in ms), so the
    // server list won't reflect the change on the next refetch immediately.
    // Optimistically flip the switch and roll back if the request fails.
    onMutate: async ({ id, enabled }) => {
      await qc.cancelQueries({ queryKey: ["triggers"] });
      const previous = qc.getQueryData<{ triggers: Trigger[] }>(["triggers"]);
      qc.setQueryData<{ triggers: Trigger[] }>(["triggers"], (old) =>
        old
          ? { triggers: old.triggers.map((t) => (t.trigger_id === id ? { ...t, enabled } : t)) }
          : old,
      );
      return { previous };
    },
    onError: (e, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(["triggers"], ctx.previous);
      toast.error(String(e));
    },
    onSuccess: () => { toast("Trigger updated — effective within 60s"); },
    // Deliberately NO onSettled invalidate of ["triggers"]: the mutation is only
    // queued to the triggers process (applied within its 60s cache window), so an
    // immediate refetch would return the pre-change value and visibly revert the
    // optimistic switch. The optimistic value stands; a rejected request rolls back
    // in onError, and window-focus/navigation refetches reconcile with server truth.
  });
  const fire = useMutation({
    mutationFn: (id: string) => post(`/api/admin/triggers/${id}/fire`),
    onSuccess: () => { toast("Trigger fired"); invalidate(); },
    onError: (e) => toast.error(String(e)),
  });
  const setDnd = useMutation({
    mutationFn: (active: boolean) => post("/api/admin/dnd", { active }),
    onSuccess: () => { toast("DND updated"); invalidate(); },
    onError: (e) => toast.error(String(e)),
  });
  const drain = useMutation({
    mutationFn: () => post("/api/admin/notifications/drain"),
    onSuccess: () => { toast("Drain queued"); invalidate(); },
    onError: (e) => toast.error(String(e)),
  });

  return (
    <div className="grid h-full gap-4 overflow-y-auto p-5 lg:grid-cols-3">
      <Card className="bg-card lg:col-span-2">
        <CardHeader><CardTitle className="font-mono text-xs tracking-widest">TRIGGERS</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {(triggers?.triggers ?? []).map((t) => (
            <div key={t.trigger_id} className="flex items-center gap-3 rounded-md border border-border p-3 font-mono text-xs">
              <Switch
                checked={t.enabled}
                onCheckedChange={(enabled) => setEnabled.mutate({ id: t.trigger_id, enabled })}
              />
              <div className="min-w-0">
                <div className="text-sm text-foreground">{t.name}</div>
                <div className="text-muted-foreground">
                  {t.trigger_type} · by {t.created_by ?? "?"} · last fired {t.last_fired ?? "never"}
                </div>
              </div>
              <Badge variant="outline" className="ml-auto text-trigger">{t.trigger_type}</Badge>
              {t.one_shot && <Badge variant="outline" className="text-warn">one-shot</Badge>}
              <Button size="sm" variant="outline" className="font-mono text-[10px]"
                onClick={() => fire.mutate(t.trigger_id)}>
                FIRE
              </Button>
            </div>
          ))}
          {triggers?.triggers.length === 0 && (
            <p className="font-mono text-xs text-muted-foreground">No active triggers.</p>
          )}
        </CardContent>
      </Card>

      <div className="space-y-4">
        <Card className="bg-card">
          <CardHeader><CardTitle className="font-mono text-xs tracking-widest">DO NOT DISTURB</CardTitle></CardHeader>
          <CardContent className="flex items-center gap-3 font-mono text-xs">
            <Switch
              checked={overview?.dnd.active ?? false}
              onCheckedChange={(active) => setDnd.mutate(active)}
            />
            <span className={overview?.dnd.active ? "text-warn" : "text-muted-foreground"}>
              {overview?.dnd.active ? `ON${overview.dnd.reason ? ` — ${overview.dnd.reason}` : ""}` : "OFF"}
            </span>
          </CardContent>
        </Card>

        <Card className="bg-card">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="font-mono text-xs tracking-widest">DEFERRED</CardTitle>
            <Button size="sm" variant="outline" className="font-mono text-[10px]"
              disabled={(deferred?.notifications.length ?? 0) === 0}
              onClick={() => drain.mutate()}>
              DRAIN NOW
            </Button>
          </CardHeader>
          <CardContent className="space-y-2">
            {(deferred?.notifications ?? []).map((n) => (
              <div key={n.notification_id} className="rounded-md border border-border p-2.5">
                <div className="font-mono text-[10px] text-trigger">{n.urgency}</div>
                <div className="text-sm">{n.title}</div>
                <div className="text-xs text-muted-foreground">{n.body}</div>
              </div>
            ))}
            {deferred?.notifications.length === 0 && (
              <p className="font-mono text-xs text-muted-foreground">Queue empty.</p>
            )}
          </CardContent>
        </Card>

        <Card className="bg-card">
          <CardHeader><CardTitle className="font-mono text-xs tracking-widest">HISTORY</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {(history?.entries ?? []).map((e) => (
              <div key={e.id} className="rounded-md border border-border p-2.5">
                <div className="font-mono text-[10px] text-muted-foreground">
                  {timeOf(e.id)} · {String(e.event.urgency ?? "")}
                </div>
                <div className="text-sm">{String(e.event.title ?? "")}</div>
                <div className="text-xs text-muted-foreground">{String(e.event.body ?? "")}</div>
              </div>
            ))}
            {history?.entries.length === 0 && (
              <p className="font-mono text-xs text-muted-foreground">No notifications yet.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
