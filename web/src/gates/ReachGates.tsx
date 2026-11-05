import { useEffect, useRef } from "react";
import { InstallGate } from "@/gates/InstallGate";
import { StandaloneGate } from "@/gates/StandaloneGate";
import { markGateAnswered } from "@/lib/reach-local";
import { Layer } from "@/shell/Layer";
import { usePush } from "@/shell/usePush";

/** The two gates on the way to notifications. */
export type ReachGate = "install" | "standalone";

export interface ReachGatesProps {
  /** Which gate is up, if either. The host holds it. */
  gate: ReachGate | null;
  /** When this session began (its `created_at`); null where the host has not read it. */
  signedInAt: string | null;
  onClose: () => void;
}

/**
 * The gate pair that System › Reach and the Room each host, mounted at
 * `level="gate"`, the way `AuthGate` mounts its own, so they rise over either.
 * The standalone gate's primary is `enable()`, held while `asking` and through the
 * gate's leave, and the gate closes once the asking ends: whatever iOS answered,
 * Reach's row and the Room's Act row are now what say it.
 *
 * A tap on either of the standalone gate's buttons ends its opening by itself on
 * this device (`useReachPrompts`), even if iOS's dialog is then dismissed. Should
 * the store refuse that, only the Room's own state holds the tap, and only as long
 * as that Room is mounted. The install gate's way out only closes it.
 */
export function ReachGates({ gate, signedInAt, onClose }: ReachGatesProps) {
  // An observer only: mounting here is not asking again (`PushOptions`).
  const { asking, enable } = usePush({ syncOnMount: false });

  // The asking ending closes the standalone gate. An effect, where a component
  // would adjust its own state during render: `onClose` sets the host's, which
  // React refuses from this one's render.
  const wasAsking = useRef(asking);
  useEffect(() => {
    if (wasAsking.current && !asking && gate === "standalone") onClose();
    wasAsking.current = asking;
  }, [asking, gate, onClose]);

  return (
    <>
      <Layer
        open={gate === "install"}
        label="Add me to the home screen"
        level="gate"
        durationMs={400}
      >
        <InstallGate onDismiss={onClose} />
      </Layer>
      <Layer
        open={gate === "standalone"}
        label="You came in the front way"
        level="gate"
        durationMs={400}
      >
        <StandaloneGate
          signedInAt={signedInAt}
          // `Layer` keeps a closed gate mounted for its leave. A second `enable()`
          // there would answer at once, and write a second row (`usePush`).
          busy={asking || gate !== "standalone"}
          onAllow={() => {
            // `enable()` first: anything ahead of it can end the gesture (`usePush`).
            enable();
            markGateAnswered();
          }}
          onDismiss={() => {
            markGateAnswered();
            onClose();
          }}
        />
      </Layer>
    </>
  );
}
