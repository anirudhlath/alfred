export type Theme = "dark" | "light";

export const THEME_KEY = "alfred.theme";

/** Light between 07:00 and 18:59, dark otherwise — unless a choice was stored. */
export function resolveInitialTheme(now: Date, stored: string | null): Theme {
  if (stored === "dark" || stored === "light") return stored;
  const hour = now.getHours();
  return hour >= 7 && hour < 19 ? "light" : "dark";
}

/** `--bg` per theme, as index.css declares it. Safari's chrome reads it from the meta. */
const THEME_COLOR: Record<Theme, string> = { dark: "#25221F", light: "#F6F3EE" };

/** The one place the theme becomes visible: an attribute write plus persistence. */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  // The theme is ours, not the OS's — a phone in system light mode runs dark here
  // at 21:00 — so the meta cannot carry a `prefers-color-scheme` pair; it follows.
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[theme]);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Private mode or a storage-quota refusal. The theme still applies for this
    // session; only the memory of it is lost.
  }
}

export function storedTheme(): string | null {
  try {
    return localStorage.getItem(THEME_KEY);
  } catch {
    return null;
  }
}
