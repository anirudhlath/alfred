import { useTheme } from "@/shell/ThemeProvider";

/**
 * Top-right of the Room header. 44x44 hit area, pulled into the header's
 * optical alignment with negative margins, around a 14 px half-filled circle.
 */
export function ThemeToggle() {
  const { toggle } = useTheme();
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Switch theme"
      className="-mt-2 -mr-2.5 flex h-11 w-11 shrink-0 items-center justify-center border-0 bg-transparent text-muted"
    >
      <span
        aria-hidden="true"
        className="block h-3.5 w-3.5 rounded-full border-[1.5px] border-current"
        style={{ background: "linear-gradient(90deg, currentColor 50%, transparent 50%)" }}
      />
    </button>
  );
}
