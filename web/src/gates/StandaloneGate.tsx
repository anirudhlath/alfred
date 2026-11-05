import { useState } from "react";
import { Gate } from "@/gates/Gate";
import { StepList } from "@/gates/StepList";
import { hhmm } from "@/lib/format";

export interface StandaloneGateProps {
  /** When this session began (its `created_at`); null until the sessions read lands. */
  signedInAt: string | null;
  /** The permission request is in flight or the gate is leaving: the primary holds. */
  busy: boolean;
  onAllow: () => void;
  onDismiss: () => void;
}

/**
 * Reaching you, opened from the home screen: the one place iOS lets the page
 * ask to notify. Steps one to four are behind you and the fifth is this
 * button; the sixth is iOS's own dialog. Copy from `Alfred.dc.html:670`,
 * verbatim; the first two steps' meta is this host and the time you signed in
 * (deviation D5), where the handoff has sample values.
 */
export function StandaloneGate({ signedInAt, busy, onAllow, onDismiss }: StandaloneGateProps) {
  // Read once, as the sign-in gate does: the host the shell was served from.
  const [hostname] = useState(() => location.hostname);
  return (
    <Gate
      kicker="reaching you · 5 of 6 · opened from the home screen"
      title="You came in the front way."
      body="From here I may ask iOS to let me reach you when the app is closed. Only for what cannot wait: an approval with a fuse, the house needing you. Never routine chatter."
      primary={{ label: "Let me reach you", onClick: onAllow, busy }}
      secondary={{ label: "Not yet", onClick: onDismiss }}
      foot="iOS asks once. Decline, and the switch moves to Settings; I shall not raise it again."
    >
      <StepList
        variant="progress"
        steps={[
          { label: "Open in Safari", meta: hostname, state: "done" },
          {
            label: "Sign in with Face ID",
            meta: signedInAt === null ? undefined : hhmm(signedInAt),
            state: "done",
          },
          { label: "Add to Home Screen", state: "done" },
          { label: "Open from the home screen", meta: "now", state: "done" },
          { label: "Let Alfred reach you", state: "current" },
          { label: "Allow in iOS", state: "todo" },
        ]}
      />
    </Gate>
  );
}
