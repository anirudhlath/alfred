import { describe, expect, it } from "vitest";
import { bufToB64url, b64urlToBuf } from "./webauthn";

describe("base64url helpers", () => {
  it("round-trips bytes", () => {
    const bytes = new Uint8Array([1, 2, 250, 255, 0]);
    const encoded = bufToB64url(bytes.buffer);
    expect(encoded).not.toMatch(/[+/=]/);
    expect(new Uint8Array(b64urlToBuf(encoded))).toEqual(bytes);
  });
});
