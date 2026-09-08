import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

interface Started {
  starts: number;
  decoded: number;
  resumed: number;
}

let started: Started;
let decodeFails = false;

class FakeAudioContext {
  state: AudioContextState = "suspended";
  destination = {} as AudioDestinationNode;

  resume(): Promise<void> {
    started.resumed += 1;
    this.state = "running";
    return Promise.resolve();
  }

  createBuffer(): AudioBuffer {
    return {} as AudioBuffer;
  }

  createBufferSource(): AudioBufferSourceNode {
    return {
      buffer: null,
      connect: () => {},
      start: () => void (started.starts += 1),
    } as unknown as AudioBufferSourceNode;
  }

  decodeAudioData(): Promise<AudioBuffer> {
    started.decoded += 1;
    return decodeFails
      ? Promise.reject(new Error("EncodingError"))
      : Promise.resolve({} as AudioBuffer);
  }
}

/** A fresh module per test, so the cached context never leaks between them. */
async function loadAudio() {
  vi.resetModules();
  return await import("./audio");
}

beforeEach(() => {
  started = { starts: 0, decoded: 0, resumed: 0 };
  decodeFails = false;
  vi.stubGlobal("AudioContext", FakeAudioContext);
});

afterEach(() => vi.unstubAllGlobals());

describe("getAudioContext", () => {
  it("builds one context and hands out the same one for ever", async () => {
    const { getAudioContext } = await loadAudio();
    const first = getAudioContext();
    expect(first).toBeInstanceOf(FakeAudioContext);
    expect(getAudioContext()).toBe(first);
  });

  it("answers null where the browser has no Web Audio at all", async () => {
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("webkitAudioContext", undefined);
    const { getAudioContext } = await loadAudio();
    expect(getAudioContext()).toBeNull();
  });

  it("accepts the webkit-prefixed constructor", async () => {
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("webkitAudioContext", FakeAudioContext);
    const { getAudioContext } = await loadAudio();
    expect(getAudioContext()).toBeInstanceOf(FakeAudioContext);
  });

  it("answers null rather than throwing when construction fails", async () => {
    vi.stubGlobal(
      "AudioContext",
      class {
        constructor() {
          throw new Error("not allowed");
        }
      },
    );
    const { getAudioContext } = await loadAudio();
    expect(getAudioContext()).toBeNull();
  });
});

describe("installAudioUnlock", () => {
  it("resumes and primes the context on the first tap, then stops listening", async () => {
    const { installAudioUnlock, getAudioContext } = await loadAudio();
    installAudioUnlock();

    document.dispatchEvent(new Event("pointerdown"));

    expect(started.resumed).toBe(1);
    expect(started.starts).toBe(1);
    expect(getAudioContext()!.state).toBe("running");

    document.dispatchEvent(new Event("pointerdown"));
    expect(started.resumed).toBe(1);
  });

  it("takes a keypress as the gesture too", async () => {
    const { installAudioUnlock } = await loadAudio();
    installAudioUnlock();

    document.dispatchEvent(new Event("keydown"));

    expect(started.resumed).toBe(1);
  });

  it("can be uninstalled before any gesture arrives", async () => {
    const { installAudioUnlock } = await loadAudio();
    const uninstall = installAudioUnlock();
    uninstall();

    document.dispatchEvent(new Event("pointerdown"));

    expect(started.resumed).toBe(0);
  });

  it("installs once however many times it is called", async () => {
    const { installAudioUnlock } = await loadAudio();
    installAudioUnlock();
    installAudioUnlock();

    document.dispatchEvent(new Event("pointerdown"));

    expect(started.starts).toBe(1);
  });
});

describe("playWavBase64", () => {
  it("decodes the reply and plays it through the shared context", async () => {
    const { playWavBase64 } = await loadAudio();
    playWavBase64(btoa("RIFF....WAVE"));
    await Promise.resolve();
    await Promise.resolve();

    expect(started.decoded).toBe(1);
    expect(started.starts).toBe(1);
  });

  it("resumes a context iOS suspended while the app was in the background", async () => {
    const { playWavBase64 } = await loadAudio();
    playWavBase64(btoa("RIFF"));
    expect(started.resumed).toBe(1);
  });

  it("stays silent rather than throwing when there is no Web Audio", async () => {
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("webkitAudioContext", undefined);
    const { playWavBase64 } = await loadAudio();
    expect(() => playWavBase64(btoa("RIFF"))).not.toThrow();
  });

  it("swallows a decode failure", async () => {
    decodeFails = true;
    const { playWavBase64 } = await loadAudio();
    playWavBase64(btoa("RIFF"));
    await Promise.resolve();
    await Promise.resolve();
    expect(started.starts).toBe(0);
  });

  it("swallows a payload that is not base64 at all", async () => {
    const { playWavBase64 } = await loadAudio();
    expect(() => playWavBase64("not base64 !!!")).not.toThrow();
    expect(started.decoded).toBe(0);
  });
});
