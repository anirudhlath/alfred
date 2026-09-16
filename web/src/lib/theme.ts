export type Theme = "dark" | "light";

export const THEME_KEY = "alfred.theme";

/** Light between 07:00 and 18:59, dark otherwise — unless a choice was stored. */
export function resolveInitialTheme(now: Date, stored: string | null): Theme {
  if (stored === "dark" || stored === "light") return stored;
  const hour = now.getHours();
  return hour >= 7 && hour < 19 ? "light" : "dark";
}

/**
 * `--bg` per theme, as index.css declares it. Safari's chrome reads it from the
 * meta; the two manifests carry it as `theme_color` and `background_color`, and
 * `src/test/manifest.test.ts` holds those files to this record.
 */
export const THEME_COLOR: Record<Theme, string> = { dark: "#25221F", light: "#F6F3EE" };

/** The manifest per theme. See `applyTheme` for why there are two. */
const MANIFEST: Record<Theme, string> = {
  dark: "/manifest.json",
  light: "/manifest-light.json",
};

/** The one place the theme becomes visible: the attribute the palette hangs off. */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  // The theme is ours, not the OS's — a phone in system light mode runs dark here
  // at 21:00 — so the meta cannot carry a `prefers-color-scheme` pair; it follows.
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[theme]);
  // A manifest is a static file and the theme is the hour's, so there are two
  // of them and the link points at whichever is true now. iOS reads the
  // manifest at Add-to-Home-Screen time — after this has run — so the launch
  // screen matches the theme the app was installed under, which is the defect
  // phase 1 deferred to phase 4 (web/README.md). On iOS it is
  // `background_color` that this really fixes: the status bar is
  // `black-translucent`, so `theme_color` never gets a look in there.
  document.querySelector('link[rel="manifest"]')?.setAttribute("href", MANIFEST[theme]);
}

/**
 * Persist a choice. Only a choice: the time-of-day fallback is applied but never
 * remembered, or the first launch would freeze the theme for every launch after.
 */
export function rememberTheme(theme: Theme): void {
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
