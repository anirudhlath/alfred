import { getAudioContext } from "./audio";

/**
 * In preference order. Constraint §4.8: Safari's MediaRecorder has no WebM at
 * all and *throws* from the constructor when asked for it, which is how the
 * outgoing VoiceButton died on every iPhone. The backend's allowed formats take
 * mp4 as of this task.
 */
export const RECORDER_MIME_TYPES = ["audio/mp4", "audio/aac"] as const;

function supportedByBrowser(type: string): boolean {
  return (
    typeof MediaRecorder !== "undefined" &&
    typeof MediaRecorder.isTypeSupported === "function" &&
    MediaRecorder.isTypeSupported(type)
  );
}

/** The first type this browser can record, or `""` to let it choose for itself. */
export function pickMimeType(isSupported: (type: string) => boolean = supportedByBrowser): string {
  for (const type of RECORDER_MIME_TYPES) {
    if (isSupported(type)) return type;
  }
  return "";
}

export interface Recording {
  blob: Blob;
  mimeType: string;
  durationMs: number;
}

/**
 * One hold-to-talk recording: the microphone stream, the MediaRecorder, and the
 * AnalyserNode the presence field is driven from — all opened together and, more
 * importantly, all released together. A leaked stream leaves the iOS microphone
 * indicator on after the button is let go.
 */
export class Recorder {
  analyser: AnalyserNode | null = null;

  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private chunks: Blob[] = [];
  private mimeType = "";
  private startedAt = 0;

  async start(): Promise<void> {
    if (this.recorder) return;

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.stream = stream;
    this.chunks = [];

    let recorder: MediaRecorder;
    try {
      this.mimeType = pickMimeType();
      recorder = this.mimeType
        ? new MediaRecorder(stream, { mimeType: this.mimeType })
        : new MediaRecorder(stream);
      recorder.ondataavailable = (event: BlobEvent) => {
        if (event.data && event.data.size > 0) this.chunks.push(event.data);
      };
      recorder.start();
    } catch (error) {
      // No MediaRecorder at all (iOS before 14.3), or one that refuses the
      // stream. The microphone is already open by now, and an iOS mic indicator
      // left on is worse than the failed recording.
      for (const track of stream.getTracks()) track.stop();
      this.stream = null;
      throw error;
    }
    // With no hint the browser chose for itself — webm or ogg, never mp4 — and
    // the blob, and through it the data URL's container hint, must say so.
    this.mimeType = recorder.mimeType || this.mimeType;
    this.recorder = recorder;
    this.startedAt = Date.now();

    // The analyser is optional: no Web Audio means no live presence field, but
    // the recording itself must still work.
    const ctx = getAudioContext();
    if (!ctx) return;
    // `installAudioUnlock` only fires once; iOS re-suspends the context every
    // time the app is backgrounded (and WebKit marks it "interrupted" after a
    // call or Siri), and an analyser on a context that is not running reads
    // silence for the whole utterance. `start()` runs inside the pointerdown,
    // so the resume is honoured.
    if (ctx.state !== "running") void ctx.resume().catch(() => {});
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.6;
    const source = ctx.createMediaStreamSource(stream);
    source.connect(analyser);
    this.analyser = analyser;
    this.source = source;
  }

  async stop(): Promise<Recording | null> {
    const recorder = this.recorder;
    const stream = this.stream;
    const mimeType = this.mimeType;
    const durationMs = Date.now() - this.startedAt;

    this.recorder = null;
    this.stream = null;
    this.startedAt = 0;

    const release = () => {
      this.source?.disconnect();
      this.source = null;
      this.analyser = null;
      for (const track of stream?.getTracks() ?? []) track.stop();
    };

    if (!recorder) {
      release();
      return null;
    }

    const blob = await new Promise<Blob>((resolve) => {
      const finish = () => resolve(new Blob(this.chunks, { type: mimeType }));
      recorder.onstop = finish;
      try {
        recorder.stop();
      } catch {
        // Already inactive. Whatever was collected is still the recording.
        finish();
      }
    });

    release();
    return { blob, mimeType, durationMs };
  }
}

/** Blob → `data:audio/mp4;base64,…`, which is what `/ws` expects as `content`. */
export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the recording"));
    reader.readAsDataURL(blob);
  });
}
