import { Gate } from "@/gates/Gate";
import { StepList } from "@/gates/StepList";

export interface InstallGateProps {
  onDismiss: () => void;
}

/**
 * Reaching you, in a Safari tab: iOS lets a page ask to notify only once it has
 * been added to the home screen and opened from there, and Safari keeps that
 * gesture to itself — so there is no primary, only the steps and a pointer at
 * the Share button below. Copy from `Alfred.dc.html:669`, verbatim.
 */
export function InstallGate({ onDismiss }: InstallGateProps) {
  return (
    <Gate
      kicker="reaching you · 3 of 6 · in Safari"
      title="Add me to the home screen."
      body="Safari will not let me ask to notify you until I have been added there, and there is no button I can show for it. It is one gesture, at the bottom of the screen."
      secondary={{ label: "Not now", onClick: onDismiss }}
      foot="This tab can be closed once you have. Nothing is lost; I pick up where we left off."
      pointer="Share"
    >
      <StepList
        variant="progress"
        steps={[
          { label: "Tap Share, below", state: "current" },
          { label: 'Scroll to "Add to Home Screen"', state: "todo" },
          { label: "Tap Add, then open Alfred from there", state: "todo" },
        ]}
      />
    </Gate>
  );
}
