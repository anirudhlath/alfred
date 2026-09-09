import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DeniedGate } from "@/gates/DeniedGate";
import { ExpiredGate } from "@/gates/ExpiredGate";
import { GateField } from "@/gates/GateField";
import { SetupGate } from "@/gates/SetupGate";
import { SignInGate } from "@/gates/SignInGate";
import { fetchAuthStatus } from "@/lib/auth";
import { authEvents } from "@/lib/auth-events";
import type { AuthStatus } from "@/lib/types";
import { useConnection } from "@/shell/ConnectionProvider";
import { Layer } from "@/shell/Layer";

export function AuthGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { lastTrueAt, reconnect } = useConnection();
  const { data, isPending } = useQuery({
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

  // A gate raised over the room must not outlive the room: if the status comes
  // back signed-out while the expired gate is up, the sign-in gate takes over,
  // and a latch left set here would cover the room again the moment it returned.
  const authenticated = data?.authenticated === true;
  if (expired && !authenticated) setExpired(false);

  // A sign-in is the one moment the sockets need a push: the server closed them
  // with 4001 while there was no session, and a 4001 is never retried.
  const refetchEverything = useCallback(() => {
    reconnect();
    void queryClient.invalidateQueries();
  }, [queryClient, reconnect]);

  let body: ReactNode;
  if (isPending) {
    // Nothing true to say yet. The field, and no copy.
    body = (
      <div
        className="relative flex flex-1 flex-col"
        style={{ background: "var(--bg)", color: "var(--fg)" }}
      >
        <GateField />
      </div>
    );
  } else if (needsSetup || setupActive) {
    body = (
      <SetupGate
        onDone={() => {
          setSetupActive(false);
          // Registration authenticated this session. Say so now: the cached
          // status still reads `registered: false`, and the latch above would
          // re-arm on it before the refetch landed — setup for ever.
          queryClient.setQueryData<AuthStatus>(["auth-status"], {
            registered: true,
            authenticated: true,
          });
          refetchEverything();
        }}
      />
    );
  } else if (!authenticated) {
    // Fail closed: a first read that errors leaves `data` undefined, and mounting
    // the room with unknown auth state would show an empty house as if it were
    // the truth. A failed *refetch* keeps the last good data, and the room with
    // it — a session that has really lapsed arrives as the `expired` event.
    body = <SignInGate onSignedIn={refetchEverything} />;
  } else {
    body = children;
  }

  // The two event gates sit outside the routing: `denied` means "not from this
  // network", which is as true on the setup and sign-in gates as in the room —
  // and setup swallows its 403s on the promise that this gate has already said it.
  return (
    <>
      {body}
      <Layer open={expired} label="Session lapsed" level="gate" durationMs={400}>
        <ExpiredGate
          onSignedIn={() => {
            setExpired(false);
            refetchEverything();
          }}
        />
      </Layer>
      <Layer open={denied} label="Not from here" level="gate" durationMs={400}>
        <DeniedGate lastTrue={lastTrueAt} onDismiss={() => setDenied(false)} />
      </Layer>
    </>
  );
}
