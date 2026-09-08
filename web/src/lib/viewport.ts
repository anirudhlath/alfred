import { useEffect, useState } from "react";

/** Below this, the inset is Safari's toolbar or a rotation artefact, not a keyboard. */
export const KEYBOARD_OPEN_PX = 80;

/**
 * How much of the window is hidden below the visual viewport — the software
 * keyboard, in practice. 0 where `visualViewport` is unavailable, because a
 * guess here would move the composer for no reason.
 */
export function keyboardInset(): number {
  const viewport = window.visualViewport;
  if (!viewport) return 0;
  return Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
}

/**
 * Mirror the visual viewport into `--app-height` and `--keyboard-inset` on the
 * document element. Returns the uninstaller; `main.tsx` calls this once and never
 * uninstalls, tests always do.
 */
export function installViewportVars(): () => void {
  const root = document.documentElement;

  const apply = () => {
    const viewport = window.visualViewport;
    const height = viewport?.height ?? window.innerHeight;
    root.style.setProperty("--app-height", `${Math.round(height)}px`);
    root.style.setProperty("--keyboard-inset", `${Math.round(keyboardInset())}px`);
  };

  apply();

  const viewport = window.visualViewport;
  // `scroll` matters as much as `resize`: iOS scrolls the visual viewport under a
  // focused field rather than resizing it again, and offsetTop is part of the inset.
  viewport?.addEventListener("resize", apply);
  viewport?.addEventListener("scroll", apply);
  window.addEventListener("resize", apply);

  return () => {
    viewport?.removeEventListener("resize", apply);
    viewport?.removeEventListener("scroll", apply);
    window.removeEventListener("resize", apply);
  };
}

/** True while the software keyboard is up. Drives the composer's padding. */
export function useKeyboardOpen(): boolean {
  const [open, setOpen] = useState(() => keyboardInset() > KEYBOARD_OPEN_PX);

  useEffect(() => {
    const check = () => setOpen(keyboardInset() > KEYBOARD_OPEN_PX);
    check();
    const viewport = window.visualViewport;
    viewport?.addEventListener("resize", check);
    viewport?.addEventListener("scroll", check);
    return () => {
      viewport?.removeEventListener("resize", check);
      viewport?.removeEventListener("scroll", check);
    };
  }, []);

  return open;
}
