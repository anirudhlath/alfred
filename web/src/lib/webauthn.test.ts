import { afterEach, describe, expect, it, vi } from "vitest";
import { bufToB64url, b64urlToBuf, loginPasskey, registerPasskey, sessionChannel } from "./webauthn";

const { postMock } = vi.hoisted(() => ({ postMock: vi.fn() }));
vi.mock("./api", () => ({ post: postMock }));

describe("base64url helpers", () => {
  it("round-trips bytes", () => {
    const bytes = new Uint8Array([1, 2, 250, 255, 0]);
    const encoded = bufToB64url(bytes.buffer);
    expect(encoded).not.toMatch(/[+/=]/);
    expect(new Uint8Array(b64urlToBuf(encoded))).toEqual(bytes);
  });
});

/** The browser says the app is on the Home Screen. */
function standalone(matches: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: matches && query === "(display-mode: standalone)",
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

const challenge = bufToB64url(new Uint8Array([7, 7, 7]).buffer);

/** A credential of the shape both ceremonies read, with the byte fields empty. */
const credential = {
  id: "cred",
  rawId: new ArrayBuffer(0),
  type: "public-key",
  response: {
    attestationObject: new ArrayBuffer(0),
    authenticatorData: new ArrayBuffer(0),
    clientDataJSON: new ArrayBuffer(0),
    signature: new ArrayBuffer(0),
    userHandle: null,
  },
};

/** What the last completion body said. */
function completion(): Record<string, unknown> {
  return postMock.mock.calls.at(-1)![1] as Record<string, unknown>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  postMock.mockReset();
});

describe("sessionChannel", () => {
  it("is pwa from the Home Screen and web from a tab", () => {
    standalone(true);
    expect(sessionChannel()).toBe("pwa");
    standalone(false);
    expect(sessionChannel()).toBe("web");
  });

  it("is web where the browser cannot say", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(sessionChannel()).toBe("web");
  });
});

describe("the completion bodies", () => {
  it("registration tells the house which client it is", async () => {
    standalone(true);
    vi.stubGlobal("navigator", { credentials: { create: vi.fn().mockResolvedValue(credential) } });
    postMock.mockResolvedValueOnce({
      challenge,
      user: { id: challenge, name: "sir", displayName: "sir" },
      _challenge_id: "ch-1",
    });

    await registerPasskey("iPhone");

    expect(postMock.mock.calls.at(-1)![0]).toBe("/api/auth/register/complete");
    expect(completion()).toMatchObject({ _challenge_id: "ch-1", _device_name: "iPhone", _channel: "pwa" });
  });

  it("login tells the house which client it is", async () => {
    standalone(false);
    vi.stubGlobal("navigator", { credentials: { get: vi.fn().mockResolvedValue(credential) } });
    postMock.mockResolvedValueOnce({ challenge, _challenge_id: "ch-2" });

    await loginPasskey();

    expect(postMock.mock.calls.at(-1)![0]).toBe("/api/auth/login/complete");
    expect(completion()).toMatchObject({ _challenge_id: "ch-2", _channel: "web" });
  });
});
