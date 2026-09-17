import type { ReactNode } from "react";

/**
 * One section of the System bench: a caps label, optionally something on its
 * right, and a card of rows under it.
 *
 * Its own module because it is the frame rather than any one section's content:
 * task 9's Sessions, Connected services, Devices & identity and Reflex import
 * it from here, and a file named for the frame is one they can read without
 * reading Health, Quiet and Maintenance first.
 *
 * The 22 px between sections is the label's own top margin rather than a gap on
 * the column, so the first label sits 16 px under the header (handoff §8) and
 * every one after it 22 px under the card above.
 */
export function SystemSection({
  title,
  aside,
  children,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mt-[22px] first:mt-4">
      <div className="mb-2 flex items-end justify-between gap-3">
        {/* `.t-meta-strong`, not `.t-label` — which is this exact 11 px caps at
            500, and carries `--muted`: 3.46:1 on `--bg` in light, under AA at
            this size. The handoff's label, in the token that can be read. */}
        <h3 className="t-meta-strong m-0 uppercase" style={{ letterSpacing: "0.08em" }}>
          {title}
        </h3>
        {aside}
      </div>
      <div
        className="overflow-hidden rounded-xl border border-line"
        style={{ background: "var(--surface)" }}
      >
        {children}
      </div>
    </section>
  );
}

/**
 * One 56 px row inside a section's card, divided from the row above it. 56 is
 * the handoff's, and it is also what keeps a row whose only control is the row
 * itself over the 44 px minimum.
 *
 * The divider is the row's own top border rather than the card's `divide-y`, so
 * a row that only appears in one state — Quiet's expiry chips — takes its line
 * with it when it goes.
 */
export function SystemRow({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-14 items-center justify-between gap-3 border-t border-line px-3 first:border-t-0">
      {children}
    </div>
  );
}
