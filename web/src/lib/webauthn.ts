import { post } from "./api";

export function bufToB64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let str = "";
  for (const b of bytes) str += String.fromCharCode(b);
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlToBuf(value: string): ArrayBuffer {
  const b64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob(padded);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  return bytes.buffer;
}

export async function registerPasskey(deviceName: string): Promise<void> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const options = await post<Record<string, any>>("/api/auth/register/begin", {
    device_name: deviceName,
  });

  // Extract and remove server-only metadata before passing options to browser API.
  // auth.js deletes these keys so browsers don't see unknown fields in PublicKeyCredentialCreationOptions.
  const challengeId = options._challenge_id as string;
  const savedDeviceName = (options._device_name as string | undefined) ?? deviceName;
  delete options._challenge_id;
  delete options._device_name;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const userOpts = options.user as any;
  // Cast required: options is Record<string,any> from py-webauthn options_to_json; rp and
  // pubKeyCredParams are always present at runtime but TypeScript can't verify the spread.
  const publicKey = {
    ...options,
    challenge: b64urlToBuf(options.challenge as string),
    user: {
      id: b64urlToBuf(userOpts.id as string),
      name: userOpts.name as string,
      displayName: userOpts.displayName as string,
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    excludeCredentials: ((options.excludeCredentials as any[] | undefined) ?? []).map((c) => ({
      ...c,
      id: b64urlToBuf(c.id as string),
    })),
  } as PublicKeyCredentialCreationOptions;

  const cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential;
  if (!cred) throw new Error("Credential creation cancelled");
  const response = cred.response as AuthenticatorAttestationResponse;

  // Field layout matches auth.js: _challenge_id and _device_name are top-level, not nested.
  await post("/api/auth/register/complete", {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      attestationObject: bufToB64url(response.attestationObject),
      clientDataJSON: bufToB64url(response.clientDataJSON),
      transports: response.getTransports ? response.getTransports() : [],
    },
    _challenge_id: challengeId,
    _device_name: savedDeviceName,
  });
}

export async function loginPasskey(conditional = false, signal?: AbortSignal): Promise<void> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const options = await post<Record<string, any>>("/api/auth/login/begin");

  // Extract and remove server-only metadata before passing to browser API.
  const challengeId = options._challenge_id as string;
  delete options._challenge_id;

  // Cast required: same reason as registerPasskey — Record<string,any> spread.
  const publicKey = {
    ...options,
    challenge: b64urlToBuf(options.challenge as string),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    allowCredentials: ((options.allowCredentials as any[] | undefined) ?? []).map((c) => ({
      ...c,
      id: b64urlToBuf(c.id as string),
    })),
  } as PublicKeyCredentialRequestOptions;

  const cred = (await navigator.credentials.get({
    publicKey,
    ...(conditional ? { mediation: "conditional" as CredentialMediationRequirement } : {}),
    ...(signal ? { signal } : {}),
  })) as PublicKeyCredential;
  if (!cred) throw new Error("Login cancelled");
  const response = cred.response as AuthenticatorAssertionResponse;

  // Field layout matches auth.js: _challenge_id is top-level alongside id/rawId/type/response.
  await post("/api/auth/login/complete", {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      authenticatorData: bufToB64url(response.authenticatorData),
      clientDataJSON: bufToB64url(response.clientDataJSON),
      signature: bufToB64url(response.signature),
      userHandle: response.userHandle ? bufToB64url(response.userHandle) : null,
    },
    _challenge_id: challengeId,
  });
}

export async function logout(): Promise<void> {
  await post("/api/auth/logout");
}
