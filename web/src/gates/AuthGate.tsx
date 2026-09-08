import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DeniedGate } from "@/gates/DeniedGate";
import { ExpiredGate } from "@/gates/ExpiredGate";
import { GateField } from "@/gates/GateField";
import { SetupGate } from "@/gates/SetupGate";
import { SignInGate } from "@/gates/SignInGate";
import { fetchAuthStatus } from "@/lib/auth";
import { authEvents } from "@/lib/auth-events";
import { Layer } from "@/shell/Layer";

export function AuthGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { data, isPending, isError } = useQuery({
    queryKey: ["auth-status"],
    queryFn: fetchAuthStatus,
  });

  const [setupActive, setSetupActive] = useState(false);
  const [expired, setExpired] = useState(false);
  const [denied, setDenied] = useState(false);

  useEffect(() => authEvents.on("expired", () => setExpired(true)), []);
  useEffect(() => authEvents.on("denied", () => setDenied(true)), []);

  const needsSetup = data !== undefined && !data.registered;

  // Registration authenticates the session, so the next refetch of auth-status
  // says "registered, authenticated" while the user is still on step 1. Latching
  // keeps the gate mounted until it says it is finished. Set during render, the
  // way react.dev documents for state that remembers a previous render.
  if (needsSetup && !setupActive) setSetupActive(true);

  const refetchEverything = useCallback(() => {
    void queryClient.invalidateQueries();
  }, [queryClient]);

  if (isPending) {
    // Nothing true to say yet. The field, and no copy.
    return (
      <div
        className="relative flex flex-1 flex-col"
        style={{ background: "var(--bg)", color: "var(--fg)" }}
      >
        <GateField />
      </div>
    );
  }

  if (needsSetup || setupActive) {
    return (
      <SetupGate
        onDone={() => {
          setSetupActive(false);
          refetchEverything();
        }}
      />
    );
  }

  // Fail closed: a 5xx or a network blip leaves `data` undefined, and mounting the
  // room with unknown auth state would show an empty house as if it were the truth.
  if (isError || !data || !data.authenticated) {
    return <SignInGate onSignedIn={refetchEverything} />;
  }

  return (
    <>
      {children}
      <Layer open={expired} label="Session lapsed" level="gate" durationMs={400}>
        <ExpiredGate
          onSignedIn={() => {
            setExpired(false);
            refetchEverything();
          }}
        />
      </Layer>
      <Layer open={denied} label="Not from here" level="gate" durationMs={400}>
        <DeniedGate onDismiss={() => setDenied(false)} />
      </Layer>
    </>
  );
}
