/**
 * The presence field's physics, ported from `Alfred.dc.html`'s `signal()`.
 *
 * Three envelopes over one shared clock:
 *  - `level`  — overall loudness, attack 0.35 / decay 0.06 per tick, snapped to
 *               a hard zero below 0.004 so the field is *perfectly* still at rest.
 *  - `bands`  — eight octave bands, either read from a live AnalyserNode or
 *               synthesized from three sines when there is no microphone.
 *  - `think`  — the thinking ramp, in 0.08 / out 0.035, so a reply settles the
 *               field instead of snapping it flat.
 *
 * No DOM, no rAF, no React: the caller ticks it once per frame with a seconds
 * clock and draws whatever comes back.
 */

const BAND_COUNT = 8;

export interface SignalFrame {
  /** 0–1 overall audio level. */
  level: number;
  /** Eight band levels, 0–1, low to high. The same array every tick — copy it if you keep it. */
  bands: number[];
  /** 0–1 thinking envelope. */
  think: number;
}

export class PresenceSignal {
  private analyser: AnalyserNode | null = null;
  private freq: Uint8Array<ArrayBuffer> | null = null;
  private bands = new Float32Array(BAND_COUNT);
  private out: number[] = new Array<number>(BAND_COUNT).fill(0);
  private level = 0;
  private think = 0;
  private holding = false;
  private thinking = false;
  private offline = false;

  /** Attach the recorder's analyser while holding; pass null on release. */
  attach(analyser: AnalyserNode | null): void {
    this.analyser = analyser;
    this.freq = analyser ? new Uint8Array(analyser.frequencyBinCount) : null;
  }

  setHolding(holding: boolean): void {
    this.holding = holding;
  }

  setThinking(thinking: boolean): void {
    this.thinking = thinking;
  }

  setOffline(offline: boolean): void {
    this.offline = offline;
  }

  /** Advance every envelope by one frame. `now` is a seconds clock, not milliseconds. */
  tick(now: number): SignalFrame {
    const b = this.bands;
    let target = 0;
    let live = false;

    if (this.analyser && this.freq && this.holding) {
      this.analyser.getByteFrequencyData(this.freq);
      let sum = 0;
      for (let k = 0; k < BAND_COUNT; k++) {
        const lo = 1 << k;
        const hi = Math.min(this.freq.length, 2 << k);
        let s = 0;
        for (let i = lo; i < hi; i++) s += this.freq[i] ?? 0;
        // max(1, …): with fftSize 256 the top band is empty (lo 128, hi 128) and
        // the prototype's division by zero turns the entire field into NaN.
        const width = Math.max(1, hi - lo);
        const v = Math.min(1, (s / width / 255) * 1.6);
        b[k] += (v - b[k]) * (v > b[k] ? 0.55 : 0.14);
        sum += v;
      }
      target = Math.min(1, sum / 5);
      live = true;
    }

    if (!live) {
      const n = (f: number, p: number) => 0.5 + 0.5 * Math.sin(now * f + p);
      if (this.holding) {
        // Speech-shaped: two fast sines beating against each other, gated by a
        // slow one so the envelope breathes instead of buzzing.
        const talk = n(5.1, 0) * n(7.7, 1.3) * 0.6 + n(1.9, 2) * 0.5;
        const pause = n(0.45, 0) > 0.28 ? 1 : 0.15;
        target = Math.min(1, 0.15 + talk * pause);
      } else {
        target = 0;
      }
      for (let k = 0; k < BAND_COUNT; k++) {
        const v = target * (0.5 + 0.5 * Math.sin(now * (1.3 + k * 0.7) + k * 1.9)) * (1 - k * 0.08);
        b[k] += (v - b[k]) * 0.2;
      }
    }

    this.level += (target - this.level) * (target > this.level ? 0.35 : 0.06);
    if (this.level < 0.004) this.level = 0;

    const wanted = this.thinking ? 1 : 0;
    this.think += (wanted - this.think) * (wanted > this.think ? 0.08 : 0.035);
    if (this.think < 0.004) this.think = 0;

    // Offline is a display decision, not a physics one. The envelopes keep
    // running — coming back should not snap — but nothing is handed out to draw.
    // The prototype instead skips the tick entirely (`e = offline ? 0 : signal(now)`),
    // which freezes its envelopes; ticking through means a reconnection mid-hold
    // resumes at the level it would have had, rather than snapping up from stale state.
    if (this.offline) {
      this.out.fill(0);
      return { level: 0, bands: this.out, think: 0 };
    }

    for (let k = 0; k < BAND_COUNT; k++) this.out[k] = b[k];
    return { level: this.level, bands: this.out, think: this.think };
  }
}
