import { useSyncExternalStore } from "react";

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
 * Call `fn` whenever the geometry may have changed. `scroll` matters as much as
 * `resize`: iOS scrolls the visual viewport under a focused field rather than
 * resizing it again, and offsetTop is part of the inset. Returns the unsubscriber.
 */
function subscribe(fn: () => void): () => void {
  const viewport = window.visualViewport;
  viewport?.addEventListener("resize", fn);
  viewport?.addEventListener("scroll", fn);
  window.addEventListener("resize", fn);
  return () => {
    viewport?.removeEventListener("resize", fn);
    viewport?.removeEventListener("scroll", fn);
    window.removeEventListener("resize", fn);
  };
}

/**
 * Mirror the window into `--app-height` and the keyboard into `--keyboard-inset`
 * on the document element. Returns the uninstaller; `main.tsx` calls this once
 * and never uninstalls, tests always do.
 */
export function installViewportVars(): () => void {
  const root = document.documentElement;

  const apply = () => {
    // innerHeight, not visualViewport.height: the column must not shrink for the
    // keyboard, because .pb-keyboard already pays for it and the composer would
    // rise twice. innerHeight follows Safari's toolbars (100vh does not), and on
    // browsers that honour `interactive-widget` it follows the keyboard too — in
    // which case the inset below is 0, and nothing is paid twice either.
    root.style.setProperty("--app-height", `${Math.round(window.innerHeight)}px`);
    root.style.setProperty("--keyboard-inset", `${Math.round(keyboardInset())}px`);
  };

  apply();
  return subscribe(apply);
}

const isKeyboardOpen = () => keyboardInset() > KEYBOARD_OPEN_PX;

/** True while the software keyboard is up. Drives the composer's padding. */
export function useKeyboardOpen(): boolean {
  return useSyncExternalStore(subscribe, isKeyboardOpen);
}
