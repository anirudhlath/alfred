import { Gate } from "@/gates/Gate";

/**
 * A launch with no network: the shell booted from the service worker's cache,
 * and nothing has been read — not even whether there is a session.
 *
 * Phase 3's rule, one layer lower: never say the house is empty before the
 * server has answered, and say which of the three silences this one is. The
 * other two — the read was refused, the store really is empty — are the benches'.
 *
 * The handoff has no copy for this; its offline scenes are all a *running* app
 * that lost its socket. Invented, and shaped on `DeniedGate`, whose form the
 * handoff did fix. The kicker is `.t-meta-strong`, not the other gates'
 * `.t-meta`: it is the line that says which silence this is.
 *
 * No button (deviation 5 in the plan). A `Try again` here would send a request —
 * react-query does not pause a read on a launch that began offline — but while
 * the phone has no connection that request can only fail the way the read
 * already did, and leave this gate exactly where it is. The moment there is a
 * signal, `AuthGate` asks again by itself.
 */
export function OfflineGate() {
  return (
    <Gate
      kicker="offline · nothing read yet"
      strongKicker
      title="No way to the house."
      body="This phone has no connection, so I cannot ask the house who you are. Nothing is lost; I pick up where we left off as soon as there is a signal."
    />
  );
}
