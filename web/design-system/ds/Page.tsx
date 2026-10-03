import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { ThemeProvider, type Theme } from "../shims/ThemeProvider";

export interface PageProps {
  /** Dark is the default after sunset and the first paint; light is daytime. */
  theme?: Theme;
  /**
   * `phone` is the 393 × 852 shell the client is drawn at: a flex column that
   * clips, exactly like the app's #root. `fill` is a plain block for laying
   * out parts.
   */
  frame?: "phone" | "fill";
  /** Inner padding for `fill`. The Room's gutter is 24, the Workshop's 16. */
  padding?: number;
  children: ReactNode;
}

/**
 * The ground everything in Alfred stands on: the theme's tokens, `--bg`, `--fg`
 * and DM Sans. Every component reads its colours from the nearest Page, and
 * ThemeToggle inside one flips that Page only.
 */
export function Page({ theme = "dark", frame = "fill", padding, children }: PageProps) {
  const [chosen, setChosen] = useState<Theme>(theme);
  const [seen, setSeen] = useState<Theme>(theme);
  if (theme !== seen) {
    setSeen(theme);
    setChosen(theme);
  }
  const value = useMemo(
    () => ({ theme: chosen, toggle: () => setChosen((t) => (t === "dark" ? "light" : "dark")) }),
    [chosen],
  );

  const base: CSSProperties = {
    background: "var(--bg)",
    color: "var(--fg)",
    fontFamily: "var(--font-sans)",
    WebkitFontSmoothing: "antialiased",
    colorScheme: chosen,
  };
  const style: CSSProperties =
    frame === "phone"
      ? {
          ...base,
          width: 393,
          height: 852,
          display: "flex",
          flexDirection: "column",
          position: "relative",
          overflow: "hidden",
          // Contains `position: fixed` descendants, as the real viewport does.
          transform: "translateZ(0)",
        }
      : { ...base, padding: padding ?? 24 };

  return (
    <ThemeProvider value={value}>
      <div data-theme={chosen} style={style}>
        {children}
      </div>
    </ThemeProvider>
  );
}
