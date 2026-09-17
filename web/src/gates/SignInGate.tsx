import { useState } from "react";
import { Gate } from "@/gates/Gate";
import { deviceFootLine, failureText, rememberedDevice } from "@/lib/auth";
import { loginPasskey } from "@/lib/webauthn";

export interface SignInGateProps {
  onSignedIn: () => void;
}

export function SignInGate({ onSignedIn }: SignInGateProps) {
  const [hostname] = useState(() => location.hostname);
  const [device] = useState(() => rememberedDevice());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signIn(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await loginPasskey();
      onSignedIn();
    } catch (caught) {
      // Login is deliberately *not* network-gated (spec §3.1): enrol at home, sign
      // in from anywhere. So a failure here is a cancelled Face ID or a real 4xx,
      // and the server's own words are the most useful thing to show.
      setError(failureText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Gate
      kicker={`${hostname} · signed out`}
      title="Welcome back, sir."
      body="Face ID unlocks the passkey on this phone. Nothing is sent but the signed challenge."
      primary={{ label: "Sign in with Face ID", onClick: () => void signIn(), busy }}
      foot={error ?? deviceFootLine(device)}
    />
  );
}
