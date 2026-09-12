export interface WorkshopHandleProps {
  onOpen: () => void;
}

/**
 * The way into the Workshop (handoff, Room): a 44 px button, mono 11
 * `workshop` under a small up-chevron, then a 139×5 radius-3 bar in `fg`.
 * `Composer` renders it under the row and hides it while the keyboard is up.
 *
 * `--fg2`/`t-meta-strong`, not the handoff's `--muted`/`t-meta`: `--muted` is
 * 3.46:1 on `--bg` in light theme (index.css), under AA, and this is the only
 * label the control has.
 */
export function WorkshopHandle({ onOpen }: WorkshopHandleProps) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label="Open the Workshop"
      className="flex min-h-11 w-full flex-col items-center gap-2.5 border-0 bg-transparent pt-0.5 pb-2"
    >
      {/* `.t-meta-strong` sets `--fg2` here, and the chevron's `border-current`
          resolves against it — one source for the colour, not two. */}
      <span className="t-meta-strong flex flex-col items-center gap-0.5">
        <span
          aria-hidden="true"
          data-testid="handle-chevron"
          className="block h-2 w-2 rotate-45 border-t-[1.5px] border-l-[1.5px] border-current"
        />
        workshop
      </span>
      <span
        aria-hidden="true"
        data-testid="handle-bar"
        className="block h-[5px] w-[139px] rounded-[3px]"
        style={{ background: "var(--fg)" }}
      />
    </button>
  );
}
