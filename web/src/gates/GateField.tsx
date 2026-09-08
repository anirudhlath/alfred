/**
 * The gates' presence: a 220 px dot grid that drifts a pixel and back over six
 * seconds. Static by design — the canvas field (plan 1b) belongs to the Room,
 * and a gate is not a place where Alfred is listening.
 */
export function GateField() {
  return (
    <div
      aria-hidden="true"
      className="gate-field pointer-events-none absolute inset-x-0 top-0 h-[220px]"
    />
  );
}
