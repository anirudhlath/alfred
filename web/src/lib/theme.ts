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
 * meta, and the two manifests carry the same pair as `theme_color` and
 * `background_color`. Not exported: `src/test/manifest.test.ts` pins both the
 * files and this record's effect on the meta to `--bg` itself, through
 * `contrast.ts`'s tokens, so the stylesheet stays the one source and a retune
 * fails rather than drifts.
 */
const THEME_COLOR: Record<Theme, string> = { dark: "#25221F", light: "#F6F3EE" };

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
  // of them and the link points at whichever is true now. Read this narrowly: a
  // manifest is consumed at *install* time, so at best this aligns the installed
  // app with the theme in force at Add to Home Screen. It never follows the
  // theme afterwards — whoever installs at 22:00 keeps a dark launch screen at
  // noon for ever — and on iOS it may do nothing at all: WebKit fetches at most
  // one manifest per document load, and this runs after parsing (main.tsx is a
  // deferred module), with no observer to re-read the href. Chromium does
  // observe it, which is where the value is, and where the cost is too: its
  // WebAPK update check fingerprints these two colours, so crossing 07:00 or
  // 19:00 can schedule a silent reinstall. Unverified on a device either way —
  // docs/superpowers/qa/ holds the checklist that would settle it.
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
