import { useState } from "react";
import { Gate } from "@/gates/Gate";

export interface UnansweredGateProps {
  /** A read of the status is in flight: the button holds until it lands. */
  asking: boolean;
  onTryAgain: () => void;
}

/**
 * The phone is online and the house did not answer "who is this?": no route
 * to it, no answer before the deadline, a proxy's 5xx or 4xx (the route sends
 * no 4xx of its own; docs/web-frontend.md, "The gates", maps every status), a
 * captive portal's page, or the house failing to read its own store. None of
 * those says whether you are signed in, so this gate does not either — the
 * sign-in gate's `signed out` would be a claim nobody made.
 *
 * Invented, and shaped on `OfflineGate` and `DeniedGate`, as the offline gate
 * was; the plan's deviation 12 carries the copy for the owner. The kicker's
 * status is `unknown since --:--` from the closed vocabulary
 * (docs/web-frontend.md), as the System bench writes a value never known: a
 * stamp would claim the session was known at that moment, and it never was.
 *
 * Unlike the offline gate, a button: a retry here can succeed the moment the
 * house answers. `AuthGate` also asks by itself on each return to the
 * foreground and once the phone has a signal again.
 */
export function UnansweredGate({ asking, onTryAgain }: UnansweredGateProps) {
  // Read once, as the sign-in gate does: the host the shell was served from.
  const [hostname] = useState(() => location.hostname);
  return (
    <Gate
      kicker={`${hostname} · unknown since --:--`}
      strongKicker
      title="The house is not answering."
      body="I asked it who you are and had no answer, so I cannot say whether you are signed in. Nothing is lost; I ask again when you try, and each time you come back."
      secondary={{ label: "Try again", onClick: onTryAgain, waiting: asking }}
    />
  );
}
