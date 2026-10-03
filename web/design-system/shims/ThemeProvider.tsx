// Stands in for web/src/shell/ThemeProvider.tsx inside the design-system bundle.
//
// The app's provider resolves the theme from the clock and localStorage and writes
// it to <html>, because the app is the whole document. On a design canvas several
// pages sit side by side in different themes, so the theme belongs to the nearest
// <Page>, which provides it through this context. Same `useTheme()` shape, so
// ThemeToggle and PresenceField are the app's own code, unmodified.
import { createContext, useContext, type ReactNode } from "react";

export type Theme = "dark" | "light";

export interface ThemeValue {
  theme: Theme;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

export function ThemeProvider({ value, children }: { value: ThemeValue; children: ReactNode }) {
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

const FALLBACK: ThemeValue = { theme: "dark", toggle: () => {} };

// Beside its provider, as web/src/shell/ThemeProvider.tsx keeps it.
// eslint-disable-next-line react-refresh/only-export-components
export function useTheme(): ThemeValue {
  return useContext(ThemeContext) ?? FALLBACK;
}
