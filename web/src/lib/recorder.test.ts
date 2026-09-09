import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAudioContext } from "./audio";
import { blobToDataUrl, pickMimeType, Recorder } from "./recorder";

class FakeMediaRecorder {
  static supported: string[] = ["audio/mp4"];
  /** What the browser settles on when it is given no hint. */
  static chosen = "audio/webm";
  static instances: FakeMediaRecorder[] = [];

  static isTypeSupported(type: string): boolean {
    return FakeMediaRecorder.supported.includes(type);
  }

  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  options: { mimeType?: string } | undefined;
  mimeType: string;
  started = false;

  constructor(_stream: MediaStream, options?: { mimeType?: string }) {
    this.options = options;
    this.mimeType = options?.mimeType ?? "";
    FakeMediaRecorder.instances.push(this);
  }

  // Like the real one: the type it actually records is known once it starts.
  start(): void {
    this.started = true;
    this.mimeType ||= FakeMediaRecorder.chosen;
  }

  stop(): void {
    this.ondataavailable?.({ data: new Blob(["audio-bytes"], { type: "audio/mp4" }) });
    this.onstop?.();
  }
}

const analyser = { fftSize: 0, smoothingTimeConstant: 0, frequencyBinCount: 128 };
const source = { connect: vi.fn(), disconnect: vi.fn() };

class FakeAudioContext {
  state = "running";
  destination = {};
  createAnalyser() {
    return analyser;
  }
  createMediaStreamSource() {
    return source;
  }
  resume = vi.fn(() => {
    this.state = "running";
    return Promise.resolve();
  });
}

let tracks: { stop: ReturnType<typeof vi.fn> }[] = [];
let getUserMedia: ReturnType<typeof vi.fn>;

beforeEach(() => {
  FakeMediaRecorder.supported = ["audio/mp4"];
  FakeMediaRecorder.instances = [];
  analyser.fftSize = 0;
  analyser.smoothingTimeConstant = 0;
  source.connect.mockClear();
  source.disconnect.mockClear();
  tracks = [{ stop: vi.fn() }, { stop: vi.fn() }];

  getUserMedia = vi.fn(async () => ({ getTracks: () => tracks }) as unknown as MediaStream);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  vi.stubGlobal("AudioContext", FakeAudioContext);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("pickMimeType", () => {
  it("prefers mp4, which is what Safari has", () => {
    expect(pickMimeType((type) => ["audio/mp4", "audio/aac"].includes(type))).toBe("audio/mp4");
  });

  it("falls back to aac", () => {
    expect(pickMimeType((type) => type === "audio/aac")).toBe("audio/aac");
  });

  it("lets the browser choose when it supports neither", () => {
    expect(pickMimeType(() => false)).toBe("");
  });

  it("never asks for webm, which Safari cannot record", () => {
    const asked: string[] = [];
    pickMimeType((type) => {
      asked.push(type);
      return false;
    });
    expect(asked).toEqual(["audio/mp4", "audio/aac"]);
  });

  it("uses MediaRecorder.isTypeSupported by default", () => {
    expect(pickMimeType()).toBe("audio/mp4");
  });

  it("answers empty, not a throw, where there is no MediaRecorder at all", () => {
    vi.stubGlobal("MediaRecorder", undefined);
    expect(pickMimeType()).toBe("");
  });
});

describe("Recorder", () => {
  it("opens the microphone and wires an analyser at the design's settings", async () => {
    const recorder = new Recorder();
    await recorder.start();

    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(FakeMediaRecorder.instances[0].options).toEqual({ mimeType: "audio/mp4" });
    expect(FakeMediaRecorder.instances[0].started).toBe(true);
    expect(recorder.analyser).toBe(analyser);
    expect(analyser.fftSize).toBe(256);
    expect(analyser.smoothingTimeConstant).toBe(0.6);
    expect(source.connect).toHaveBeenCalledWith(analyser);
  });

  it("constructs with no options when nothing is supported", async () => {
    FakeMediaRecorder.supported = [];
    const recorder = new Recorder();
    await recorder.start();
    expect(FakeMediaRecorder.instances[0].options).toBeUndefined();
  });

  it("labels the recording with the type the browser chose, not mp4", async () => {
    FakeMediaRecorder.supported = [];
    const recorder = new Recorder();
    await recorder.start();

    const recording = await recorder.stop();

    // Firefox records webm here; calling it mp4 would send ffmpeg a lie.
    expect(recording!.mimeType).toBe("audio/webm");
    expect(recording!.blob.type).toBe("audio/webm");
  });

  it("resumes a context iOS suspended while the app was in the background", async () => {
    const ctx = getAudioContext() as unknown as FakeAudioContext;
    ctx.state = "suspended";
    await new Recorder().start();

    expect(ctx.resume).toHaveBeenCalled();
    expect(ctx.state).toBe("running");
  });

  it("resumes a context WebKit marked interrupted by a call mid-conversation", async () => {
    const ctx = getAudioContext() as unknown as FakeAudioContext;
    // The context outlives the test above, and so does its mock's history.
    ctx.resume.mockClear();
    ctx.state = "interrupted";
    await new Recorder().start();

    expect(ctx.resume).toHaveBeenCalledTimes(1);
    expect(ctx.state).toBe("running");
  });

  it("closes the microphone again when there is no MediaRecorder to open", async () => {
    vi.stubGlobal("MediaRecorder", undefined);
    const recorder = new Recorder();

    await expect(recorder.start()).rejects.toThrow();

    expect(tracks[0].stop).toHaveBeenCalled();
    expect(tracks[1].stop).toHaveBeenCalled();
    expect(await recorder.stop()).toBeNull();
  });

  it("ignores a second start while already recording", async () => {
    const recorder = new Recorder();
    await recorder.start();
    await recorder.start();
    expect(FakeMediaRecorder.instances).toHaveLength(1);
  });

  it("returns the blob, the type and how long it ran", async () => {
    vi.useFakeTimers();
    const recorder = new Recorder();
    await recorder.start();

    vi.advanceTimersByTime(2400);
    const recording = await recorder.stop();

    expect(recording).not.toBeNull();
    expect(recording!.mimeType).toBe("audio/mp4");
    expect(recording!.durationMs).toBe(2400);
    expect(recording!.blob.size).toBeGreaterThan(0);
  });

  it("releases the microphone and the analyser", async () => {
    const recorder = new Recorder();
    await recorder.start();

    await recorder.stop();

    expect(tracks[0].stop).toHaveBeenCalled();
    expect(tracks[1].stop).toHaveBeenCalled();
    expect(source.disconnect).toHaveBeenCalled();
    expect(recorder.analyser).toBeNull();
  });

  it("answers null when it was never started", async () => {
    expect(await new Recorder().stop()).toBeNull();
  });

  it("still releases the microphone when the recorder refuses to stop", async () => {
    const recorder = new Recorder();
    await recorder.start();
    FakeMediaRecorder.instances[0].stop = () => {
      throw new Error("InvalidStateError");
    };

    const recording = await recorder.stop();

    expect(recording).not.toBeNull();
    expect(tracks[0].stop).toHaveBeenCalled();
  });

  it("records without an analyser where there is no Web Audio", async () => {
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("webkitAudioContext", undefined);
    // getAudioContext() memoises the context the tests above built, so a fresh
    // copy of the module is the only recorder that has never seen one.
    vi.resetModules();
    const { Recorder: FreshRecorder } = await import("./recorder");
    const recorder = new FreshRecorder();
    await recorder.start();
    expect(FakeMediaRecorder.instances[0].started).toBe(true);
    expect(recorder.analyser).toBeNull();
  });
});

describe("blobToDataUrl", () => {
  it("reads a blob as a data URL the socket can carry", async () => {
    const url = await blobToDataUrl(new Blob(["hello"], { type: "audio/mp4" }));
    expect(url.startsWith("data:audio/mp4;base64,")).toBe(true);
  });
});
