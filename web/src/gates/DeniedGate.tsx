import { Gate } from "@/gates/Gate";
import { hhmm } from "@/lib/format";

export interface DeniedGateProps {
  /** When the house was last reachable; omitted before any connection has been made. */
  lastTrue?: Date | null;
  onDismiss: () => void;
}

/**
 * 403. Registration and credential writes are home-network-only (spec §3.1), so
 * this is what "you are not at home" looks like. There is nothing to retry and
 * nothing to sign in to — only a way back to what is already on screen.
 */
export function DeniedGate({ lastTrue, onDismiss }: DeniedGateProps) {
  const foot = lastTrue
    ? `Last true ${hhmm(lastTrue)} · everything shown behind this is last-known`
    : "Everything shown behind this is last-known";

  return (
    <Gate
      kicker="403 · off-network"
      title="Not from here."
      body="New passkeys, credentials and devices may only be added from inside the house network. This connection is not, so the house declined it. Nothing was changed."
      secondary={{ label: "Back to the room", onClick: onDismiss }}
      foot={foot}
    />
  );
}
