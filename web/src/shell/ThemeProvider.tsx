import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { applyTheme, resolveInitialTheme, storedTheme, type Theme } from "@/lib/theme";

export interface ThemeValue {
  theme: Theme;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Resolved once, at mount: the time of day must not flip the theme under the
  // user while the app is open.
  const [theme, setTheme] = useState<Theme>(() => resolveInitialTheme(new Date(), storedTheme()));

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const value = useMemo<ThemeValue>(
    () => ({
      theme,
      toggle: () => setTheme((current) => (current === "dark" ? "light" : "dark")),
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
