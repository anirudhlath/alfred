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
 */

/**
 * Call `fn` whenever the geometry may have changed. `scroll` matters as much as
 * `resize`: iOS pans the visual viewport under a focused field rather than
 * resizing it again, and the pan moves `offsetTop`. `window`'s own resize
 * covers rotation and the browsers that have no `visualViewport` at all.
 * Returns the unsubscriber.
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
 * Mirror the visible band onto the document element as `--app-height` and
 * `--viewport-top`. Returns the uninstaller; `main.tsx` calls this once and
 * never uninstalls, tests always do.
 */
export function installViewportVars(): () => void {
  const root = document.documentElement;

  const apply = () => {
    const viewport = window.visualViewport;
    // No visualViewport (jsdom, and browsers before Safari 13): innerHeight is
    // the only reading there is, and on those the two viewports are the same.
    const height = viewport ? viewport.height : window.innerHeight;
    root.style.setProperty("--app-height", `${Math.round(height)}px`);
    root.style.setProperty("--viewport-top", `${Math.round(viewport?.offsetTop ?? 0)}px`);
  };

  apply();
  return subscribe(apply);
}
