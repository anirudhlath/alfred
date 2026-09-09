/**
 * One AudioContext for the whole app, unlocked by the first gesture.
 *
 * Constraint §4.7: iOS keeps a context suspended and refuses `Audio.play()`
 * until the user has touched the page, and it will not retroactively allow a
 * sound that was requested before then. The outgoing client made a fresh
 * `new Audio()` per reply, so the first spoken reply after every launch was
 * dropped in silence. Everything now plays through a context that a real tap
 * has already resumed.
 */

type AudioContextCtor = new () => AudioContext;

interface AudioWindow {
  AudioContext?: AudioContextCtor;
  webkitAudioContext?: AudioContextCtor;
}

let context: AudioContext | null = null;
let installed = false;

function audioContextCtor(): AudioContextCtor | null {
  const w = window as unknown as AudioWindow;
  return w.AudioContext ?? w.webkitAudioContext ?? null;
}

/** The shared context, built on first use. Null where Web Audio does not exist. */
export function getAudioContext(): AudioContext | null {
  if (context) return context;
  const Ctor = audioContextCtor();
  if (!Ctor) return null;
  try {
    context = new Ctor();
  } catch {
    // Some embedded browsers refuse construction outright. Silence beats a crash.
    context = null;
  }
  return context;
}

/**
 * Resume and prime the shared context on the first `pointerdown` or `keydown`.
 * Returns an uninstaller; `main.tsx` calls this once and never uninstalls.
 *
 * The zero-length buffer is not ceremony: on iOS, resuming is not enough — a
 * source has to actually start from inside the gesture for the context to count
 * as unlocked.
 */
export function installAudioUnlock(): () => void {
  if (installed) return () => {};
  installed = true;

  const remove = () => {
    document.removeEventListener("pointerdown", unlock);
    document.removeEventListener("keydown", unlock);
    installed = false;
  };

  function unlock(): void {
    const ctx = getAudioContext();
    if (ctx) {
      if (ctx.state !== "running") void ctx.resume().catch(() => {});
      try {
        const source = ctx.createBufferSource();
        source.buffer = ctx.createBuffer(1, 1, 22050);
        source.connect(ctx.destination);
        source.start(0);
      } catch {
        // Nothing to prime — the context will still work for later playback.
      }
    }
    remove();
  }

  document.addEventListener("pointerdown", unlock);
  document.addEventListener("keydown", unlock);
  return remove;
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** Play a base64 WAV — Alfred's spoken reply, or an urgent notification. */
export function playWavBase64(base64: string): void {
  const ctx = getAudioContext();
  if (!ctx) return;

  let bytes: Uint8Array;
  try {
    bytes = base64ToBytes(base64);
  } catch {
    // A truncated or non-base64 payload. Nothing to play, nothing to report.
    return;
  }

  // iOS suspends the context whenever the app is backgrounded; a reply arriving
  // on the way back would otherwise decode fine and play into nothing. Not
  // `=== "suspended"`: WebKit also has "interrupted" — a phone call or Siri
  // took the audio session — which the AudioContextState union does not name.
  if (ctx.state !== "running") void ctx.resume().catch(() => {});

  void ctx
    .decodeAudioData(bytes.buffer as ArrayBuffer)
    .then((buffer) => {
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      source.start(0);
    })
    .catch(() => {
      // Undecodable audio is not worth interrupting the conversation over — the
      // text of the reply is already on screen.
    });
}
