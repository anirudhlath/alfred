import { useEffect, useRef } from "react";
import type { PresenceSignal } from "@/lib/presence-signal";
import { useReducedMotion } from "@/shell/presence";
import { useTheme } from "@/shell/ThemeProvider";

const W = 393;
const H = 190;
/** 12 pt dot grid — the handoff's "Presence field" paragraph. */
const STEP = 12;
/** The prototype's `dotResponse` prop. Fixed at its default; there is no control for it. */
const GAIN = 1;
/** What a frozen frame is drawn from: a signal at rest, without asking the signal. */
const ZERO_BANDS: number[] = [0, 0, 0, 0, 0, 0, 0, 0];

export interface PresenceFieldProps {
  signal: PresenceSignal;
  offline: boolean;
}

/**
 * Alfred, as a surface. A dot grid displaced by a height field: still at rest,
 * tidal while you speak, pulsing while he thinks. Ported from `presence()` in
 * `Alfred.dc.html`.
 */
export function PresenceField({ signal, offline }: PresenceFieldProps) {
  const { theme } = useTheme();
  const reduced = useReducedMotion();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const phaseRef = useRef(0);
  const thinkPhaseRef = useRef(0);
  const lastRef = useRef<number | null>(null);
  /** Whether the frame on the canvas is the resting one, which never changes. */
  const stillRef = useRef(false);

  useEffect(() => {
    signal.setOffline(offline);
  }, [signal, offline]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const dark = theme === "dark";
    // New colours, or a new signal: whatever is on the canvas is stale.
    stillRef.current = false;

    // One frame. `frozen` paints the grid at rest without ticking the signal —
    // the reduce-motion and hidden-tab frame — so the envelopes keep running
    // for whoever ticks next, and no mid-swell frame is ever left standing.
    const draw = (now: number, frozen = false) => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (canvas.width !== W * dpr) {
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        // Resizing wipes the bitmap, resting frame included.
        stillRef.current = false;
      }

      // jsdom has no 2D context without the optional `canvas` package, and a
      // canvas detached mid-frame returns null. Neither is worth a crash.
      let g: CanvasRenderingContext2D | null;
      try {
        g = canvas.getContext("2d");
      } catch {
        g = null;
      }
      if (!g) return;

      const frame = frozen ? null : signal.tick(now);
      const e = frame?.level ?? 0;
      const b = frame?.bands ?? ZERO_BANDS;
      const th = frame?.think ?? 0;

      // At rest every frame is the same frame. Paint it once and then leave the
      // canvas alone; the loop keeps running so it notices when that changes.
      const still = e === 0 && th === 0;
      if (still && stillRef.current) return;
      stillRef.current = still;

      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, H);

      // The wave phase only advances while something is flowing, so the field is
      // genuinely motionless at rest rather than slowly creeping.
      if (!frozen) {
        if (lastRef.current === null) lastRef.current = now;
        const dt = now - lastRef.current;
        phaseRef.current += dt * (0.4 + e * 2.2);
        thinkPhaseRef.current += dt * th;
        lastRef.current = now;
      }
      const ph = phaseRef.current;
      const tph = thinkPhaseRef.current;

      const rgb = offline
        ? dark
          ? "154,145,134"
          : "138,129,119"
        : dark
          ? "232,178,132"
          : "205,132,80";
      const base = offline ? 0.42 : dark ? 0.55 : 0.7;

      const cols = Math.ceil(W / STEP) + 1;
      const rows = Math.ceil(H / STEP) + 1;

      // Tides: low bands are long swells travelling across, high bands are short
      // chop riding on the crests.
      const low = (b[0] + b[1] + b[2]) / 3;
      const mid = (b[3] + b[4]) / 2;
      const high = (b[5] + b[6] + b[7]) / 3;

      for (let j = 0; j < rows; j++) {
        const y = j * STEP;
        for (let i = 0; i < cols; i++) {
          const x = i * STEP;
          let z = 0;

          if (e > 0) {
            const swell =
              Math.sin(x * 0.022 + y * 0.012 - ph * 1.1) * 0.65 +
              Math.sin(x * 0.013 - y * 0.02 + ph * 0.7 + 1.7) * 0.5;
            const crest = Math.pow(Math.max(0, swell), 1.6) * (0.4 + low * 1.4);
            const trough = Math.min(0, swell) * 0.35 * (0.3 + low);
            const chop =
              Math.sin(x * 0.09 + ph * 3.1 + y * 0.04) *
              Math.sin(y * 0.07 - ph * 2.3) *
              high *
              0.5 *
              Math.max(0, swell + 0.3);
            const roll = Math.sin(x * 0.045 - ph * 1.8 + y * 0.03) * mid * 0.35;
            z = (crest + trough + chop + roll) * e * GAIN * 1.5;
            z = Math.max(-0.5, Math.min(1.9, z));
          }

          if (th > 0) {
            // A pulse sweeping down the field every ~2.4 s, with a fainter slow
            // counter-sweep going back up.
            const p1 = (y / H + tph * 0.42) % 1;
            const p2 = (y / H - tph * 0.17 + 0.5 + 1e4) % 1;
            const g1 = Math.abs(p1 - 0.5);
            const g2 = Math.abs(p2 - 0.5);
            const band = Math.exp(-Math.pow(g1 / 0.11, 2));
            const back = Math.exp(-Math.pow(g2 / 0.18, 2)) * 0.35;
            const lean = 1 - Math.abs(x - W / 2) / (W * 0.9);
            z += (band * 1.15 + back) * lean * th * GAIN;
          }

          const px = x + z * Math.sin(x * 0.022 - ph) * 2.5;
          const py = y - z * 12;
          const r = Math.max(0.6, 1 + 0.7 * z);
          const a = Math.max(0.05, Math.min(1, base * (0.65 + 0.45 * z)));

          if (z > 0.3) {
            g.fillStyle = `rgba(0,0,0,${(dark ? 0.24 : 0.11) * Math.min(1, z - 0.3)})`;
            g.beginPath();
            g.ellipse(px, y + 2, r * 1.3, r * 0.5, 0, 0, Math.PI * 2);
            g.fill();
          }

          g.fillStyle = `rgba(${rgb},${a})`;
          g.beginPath();
          g.arc(px, py, r, 0, Math.PI * 2);
          g.fill();
        }
      }
    };

    let raf = 0;
    const loop = () => {
      draw(performance.now() / 1000);
      raf = requestAnimationFrame(loop);
    };

    // Reduce-motion: one frame at rest, and never again. Constraint from the
    // handoff's motion section, and the JS half of the CSS `prefers-reduced-motion`
    // block in index.css.
    if (reduced) {
      draw(0, true);
      return;
    }

    const start = () => {
      if (raf) return;
      // Drop the accumulated clock so a backgrounded hour does not arrive as one
      // enormous dt and fling the phase forward.
      lastRef.current = null;
      loop();
    };
    const stop = () => {
      if (!raf) return;
      cancelAnimationFrame(raf);
      raf = 0;
    };

    const onVisibility = () => (document.hidden ? stop() : start());
    document.addEventListener("visibilitychange", onVisibility);

    if (document.hidden) draw(0, true);
    else start();

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      cancelAnimationFrame(raf);
    };
  }, [signal, offline, theme, reduced]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className="absolute top-0 left-1/2 -translate-x-1/2"
      style={{
        width: `${W}px`,
        height: `${H}px`,
        pointerEvents: "none",
        WebkitMaskImage: "linear-gradient(to bottom, rgba(0,0,0,.95) 30%, transparent 100%)",
        maskImage: "linear-gradient(to bottom, rgba(0,0,0,.95) 30%, transparent 100%)",
      }}
    />
  );
}
