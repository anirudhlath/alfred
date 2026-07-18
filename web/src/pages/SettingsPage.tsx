import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { logout } from "@/lib/webauthn";
import type { IntegrationInfo } from "@/lib/types";
import { IntegrationCard } from "./IntegrationCard";
import { VoiceEnrollmentCard } from "./VoiceEnrollmentCard";

export function SettingsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: integrations } = useQuery<IntegrationInfo[]>({
    queryKey: ["integrations"],
    queryFn: () => api("/api/integrations"),
  });

  const onSaved = () => {
    void qc.invalidateQueries({ queryKey: ["integrations"] });
    void qc.invalidateQueries({ queryKey: ["integration-status"] });
  };

  const signOut = async () => {
    await logout();
    await qc.invalidateQueries({ queryKey: ["auth-status"] });
    navigate("/login", { replace: true });
  };

  return (
    <div className="h-full space-y-4 overflow-y-auto p-5">
      <Card className="bg-card">
        <CardHeader className="flex-row items-center justify-between gap-2">
          <CardTitle className="font-mono text-xs tracking-widest">SESSION</CardTitle>
          <Button
            size="sm"
            variant="outline"
            className="font-mono text-[10px] text-bad"
            onClick={() => void signOut()}
          >
            SIGN OUT
          </Button>
        </CardHeader>
        <CardContent className="font-mono text-xs text-muted-foreground">
          Signed in via passkey. Sign out to clear this session.
        </CardContent>
      </Card>

      <VoiceEnrollmentCard />

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
        {(integrations ?? []).map((integration) => (
          <IntegrationCard key={integration.name} integration={integration} onSaved={onSaved} />
        ))}
      </div>
      {integrations?.length === 0 && (
        <p className="font-mono text-xs text-muted-foreground">No integrations available.</p>
      )}
    </div>
  );
}
