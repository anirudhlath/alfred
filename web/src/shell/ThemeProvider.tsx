import { createContext, useContext, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { applyTheme, rememberTheme, resolveInitialTheme, storedTheme, type Theme } from "@/lib/theme";

export interface ThemeValue {
  theme: Theme;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Resolved once, at mount: the time of day must not flip the theme under the
  // user while the app is open.
  const [theme, setTheme] = useState<Theme>(() => resolveInitialTheme(new Date(), storedTheme()));

  // A layout effect, so the attribute lands before the first paint: :root is dark
  // until it is written, and a stored light theme must not flash dark on launch.
  useLayoutEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const value = useMemo<ThemeValue>(
    () => ({
      theme,
      toggle: () => {
        // Persisted here and not in the effect: only a choice is remembered, never
        // the time-of-day fallback, or the first launch would fix the theme for good.
        const next = theme === "dark" ? "light" : "dark";
        rememberTheme(next);
        setTheme(next);
      },
    }),
    [theme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme(): ThemeValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme outside ThemeProvider");
  return ctx;
}
