/**
 * Pin the app to the *visual* viewport — the band of screen that is actually
 * visible — rather than to the layout viewport.
 *
 * iOS gives a standalone app no single honest answer to "how tall is the part
 * of the screen I may draw in". `window.innerHeight` is the layout viewport,
 * which a software keyboard may or may not shrink, depending on iOS version and
 * on whether the app was launched from the home screen. `visualViewport.height`
 * is the part on screen, and `visualViewport.offsetTop` is how far iOS has
 * panned it to keep a focused field above the keys.
 *
 * Deriving a keyboard inset from the difference of the two was the phase-2 bug.
 * The two readings settle at different moments, and a single frame in which the
 * layout viewport had already shrunk while the visual one had not left
 * `--keyboard-inset` holding most of a keyboard as padding on a column that was
 * already keyboard-free. The composer paid for the keyboard twice, overflowed a
 * shell shorter than itself, and was clipped off the top of the screen — which
 * is what the phone reported, with the Workshop handle stranded under it.
 *
 * So: no inset and no arithmetic between the two viewports. `--app-height` is
 * the visible band's height, `--viewport-top` is where that band starts, and
 * every full-screen surface is a fixed box at exactly those coordinates
 * (index.css, `#root` and `.viewport-fill`). Whatever iOS does — shrink, pan,
 * or both — the shell *is* the visible part of the screen, the composer sits at
 * the bottom of it, and nothing in the tree has to know a keyboard exists.
 *
 * The visual viewport is only believed while a field that raises a keyboard has
 * focus, because that is the only thing that truly shrinks it. Measured on an
 * iPhone (iOS 27, installed) with no keyboard and nothing focused, it dropped
 * to 547 against a layout viewport of 894 as the app went to the background,
 * came back reading 547 with no event, and corrected only at the next touch: a
 * dark band over the bottom of every surface, clipping whatever sat there.
 * Re-reading on resume cannot help, since the reading itself is what is wrong.
 * With no such field the shell is the layout viewport, from its top: an offset
 * belongs to the band it was read with, and a shell that kept the keyboard's
 * offset at the full height would hang off the bottom of the screen.
 */

/** The input types iOS raises no keyboard for. */
const NO_KEYBOARD = new Set([
  "button",
  "checkbox",
  "color",
  "file",
  "hidden",
  "image",
  "radio",
  "range",
  "reset",
  "submit",
]);

function fieldHasFocus(): boolean {
  const focused = document.activeElement;
  if (focused instanceof HTMLTextAreaElement) return true;
  return focused instanceof HTMLInputElement && !NO_KEYBOARD.has(focused.type);
}

/**
 * Call `fn` whenever the geometry may have changed. `scroll` matters as much as
 * `resize`: iOS pans the visual viewport under a focused field rather than
 * resizing it again, and the pan moves `offsetTop`. `window`'s own resize
 * covers rotation and the browsers that have no `visualViewport` at all. Focus
 * moving decides which reading counts, so it is geometry too.
 * Returns the unsubscriber.
 */
function subscribe(fn: () => void): () => void {
  const viewport = window.visualViewport;
  viewport?.addEventListener("resize", fn);
  viewport?.addEventListener("scroll", fn);
  window.addEventListener("resize", fn);
  document.addEventListener("focusin", fn);
  document.addEventListener("focusout", fn);
  return () => {
    viewport?.removeEventListener("resize", fn);
    viewport?.removeEventListener("scroll", fn);
    window.removeEventListener("resize", fn);
    document.removeEventListener("focusin", fn);
    document.removeEventListener("focusout", fn);
  };
}

/**
 * Mirror the visible band onto the document element as `--app-height` and
 * `--viewport-top`. Returns the uninstaller; `main.tsx` calls this once and
 * never uninstalls, tests always do.
 */
export function installViewportVars(): () => void {
  const root = document.documentElement;

  const apply = () => {
    // No visualViewport (jsdom, and browsers before Safari 13): innerHeight is
    // the only reading there is, and on those the two viewports are the same.
    const band = fieldHasFocus() ? window.visualViewport : null;
    const height = band ? band.height : window.innerHeight;
    root.style.setProperty("--app-height", `${Math.round(height)}px`);
    root.style.setProperty("--viewport-top", `${Math.round(band?.offsetTop ?? 0)}px`);
  };

  apply();
  return subscribe(apply);
}
