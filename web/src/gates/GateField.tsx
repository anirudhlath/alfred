/**
 * The gates' presence: a 220 px dot grid whose only movement is `drift`, a
 * pixel out and back over six seconds. Not the Room's canvas field (plan 1b) —
 * a gate is not a place where Alfred is listening.
 */
export function GateField() {
  return (
    <div
      aria-hidden="true"
      className="gate-field pointer-events-none absolute inset-x-0 top-0 h-[220px]"
    />
  );
}
