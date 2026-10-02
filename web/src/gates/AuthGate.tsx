import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DeniedGate } from "@/gates/DeniedGate";
import { ExpiredGate } from "@/gates/ExpiredGate";
import { GateField } from "@/gates/GateField";
import { OfflineGate } from "@/gates/OfflineGate";
import { SetupGate } from "@/gates/SetupGate";
import { SignInGate } from "@/gates/SignInGate";
import { fetchAuthStatus } from "@/lib/auth";
import { authEvents } from "@/lib/auth-events";
import type { AuthStatus } from "@/lib/types";
import { useConnection } from "@/shell/ConnectionProvider";
import { Layer } from "@/shell/Layer";
import { useOnline } from "@/shell/useOnline";

export function AuthGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { lastTrueAt, reconnect } = useConnection();
  const { data, isPending, errorUpdateCount, refetch } = useQuery({
    queryKey: ["auth-status"],
    queryFn: fetchAuthStatus,
  });
  const online = useOnline();

  const [setupActive, setSetupActive] = useState(false);
  const [expired, setExpired] = useState(false);
  const [denied, setDenied] = useState(false);
  const [failedOffline, setFailedOffline] = useState<number | null>(null);

  useEffect(() => authEvents.on("expired", () => setExpired(true)), []);
  useEffect(() => authEvents.on("denied", () => setDenied(true)), []);

  // Nothing has landed — not the session, not even whether there is a house.
  const unread = data === undefined;
  // The offline gate's condition. See the branch below.
  const showOffline = unread && !online;

  // How many times the read had failed while the offline gate was up: failures
  // it has already answered for. Set during render, like the setup latch below.
  if (showOffline && failedOffline !== errorUpdateCount) setFailedOffline(errorUpdateCount);

  // The signal is back and no read since has failed. Until one has, the read's
  // last error is the offline one, and the fail-closed branch would turn it into
  // "signed out" the moment the phone reconnects — so the field shows, and the
  // read is asked again. react-query asks by itself only if its onlineManager
  // heard the phone go offline, and on a launch that began offline it never
  // did: it starts online and listens for events, and none had fired. Keyed on
  // the count rather than on a fetch being under way, because React can render
  // the `online` event before react-query has started anything — and on that
  // launch react-query starts nothing at all. With nothing read, `refetch()`
  // joins a read already under way rather than cancelling it, so when
  // react-query does ask as well, the two are one request.
  const reasking = unread && online && failedOffline === errorUpdateCount;
  useEffect(() => {
    if (reasking) void refetch();
  }, [reasking, refetch]);

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
  if (showOffline) {
    // The phone says it has no network and nothing has landed: say so, whatever
    // stage the read is at — in flight, between its two tries, failed, or (once
    // react-query has heard an `offline` event) paused. On a launch that began
    // offline it is not paused (see `useOnline`): it runs and fails, and without
    // this the fail-closed branch below would say "signed out" about a session
    // nobody could ask after. Keyed on `data`, not on the request, which still
    // goes out; if the phone is wrong about being offline, the read lands and
    // this gives way.
    body = <OfflineGate />;
  } else if (isPending || reasking) {
    // Nothing true to say yet. The field, and no copy. With a network, a read
    // that has not landed is in flight or about to be, and a word about the
    // connection would be a guess.
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
