import { useState } from "react";
import { Gate } from "@/gates/Gate";
import { failureText } from "@/lib/auth";
import { loginPasskey } from "@/lib/webauthn";

export interface ExpiredGateProps {
  onSignedIn: () => void;
}

/**
 * 401. Raised over whatever was on screen, never instead of it — the room behind
 * this gate is still the last thing that was true.
 *
 * The body deviates from the handoff on purpose: the prototype says "30 days",
 * and phase 0 cut the session TTL to eight hours (`_AUTH_SESSION_TTL`).
 */
export function ExpiredGate({ onSignedIn }: ExpiredGateProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signIn(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await loginPasskey();
      onSignedIn();
    } catch (caught) {
      setError(failureText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Gate
      kicker="401 · session lapsed"
      title="Your session lapsed."
      body="The passkey session on this phone ran out after eight hours. Anything below is last-known until you sign in again."
      primary={{ label: "Sign in with Face ID", onClick: () => void signIn(), busy }}
      foot={error ?? "Conversations and settings are on the server; nothing is lost."}
    />
  );
}
