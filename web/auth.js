/**
 * WebAuthn authentication for Alfred PWA.
 * Handles registration, login (with Conditional UI), and auth status.
 */

const Auth = {
  /**
   * Check current auth status from server.
   * @returns {Promise<{registered: boolean, authenticated: boolean}>}
   */
  async getStatus() {
    const resp = await fetch('/api/auth/status');
    return resp.json();
  },

  /**
   * Start and complete WebAuthn registration.
   * @param {string} deviceName
   * @returns {Promise<boolean>} true if registration succeeded
   */
  async register(deviceName) {
    // Begin
    const beginResp = await fetch('/api/auth/register/begin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_name: deviceName }),
    });
    if (!beginResp.ok) {
      const err = await beginResp.json();
      throw new Error(err.detail || 'Registration failed');
    }
    const options = await beginResp.json();
    const challengeId = options._challenge_id;
    const savedDeviceName = options._device_name;

    // Convert base64url fields for browser API
    options.challenge = base64urlToBuffer(options.challenge);
    options.user.id = base64urlToBuffer(options.user.id);
    if (options.excludeCredentials) {
      options.excludeCredentials = options.excludeCredentials.map(c => ({
        ...c,
        id: base64urlToBuffer(c.id),
      }));
    }
    delete options._challenge_id;
    delete options._device_name;

    // Browser credential creation
    const credential = await navigator.credentials.create({ publicKey: options });
    if (!credential) throw new Error('Credential creation cancelled');

    // Complete
    const attestation = {
      id: credential.id,
      rawId: bufferToBase64url(credential.rawId),
      type: credential.type,
      response: {
        attestationObject: bufferToBase64url(credential.response.attestationObject),
        clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
        transports: credential.response.getTransports ? credential.response.getTransports() : [],
      },
      _challenge_id: challengeId,
      _device_name: savedDeviceName,
    };

    const completeResp = await fetch('/api/auth/register/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(attestation),
    });
    if (!completeResp.ok) {
      const err = await completeResp.json();
      throw new Error(err.detail || 'Registration verification failed');
    }
    return true;
  },

  /**
   * Start and complete WebAuthn login.
   * @param {AbortSignal} [signal] - Optional abort signal for Conditional UI
   * @returns {Promise<boolean>} true if login succeeded
   */
  async login(signal) {
    const beginResp = await fetch('/api/auth/login/begin', { method: 'POST' });
    if (!beginResp.ok) {
      const err = await beginResp.json();
      throw new Error(err.detail || 'Login failed');
    }
    const options = await beginResp.json();
    const challengeId = options._challenge_id;

    options.challenge = base64urlToBuffer(options.challenge);
    if (options.allowCredentials) {
      options.allowCredentials = options.allowCredentials.map(c => ({
        ...c,
        id: base64urlToBuffer(c.id),
      }));
    }
    delete options._challenge_id;

    const getOptions = { publicKey: options };
    if (signal) getOptions.signal = signal;

    const assertion = await navigator.credentials.get(getOptions);
    if (!assertion) throw new Error('Login cancelled');

    const body = {
      id: assertion.id,
      rawId: bufferToBase64url(assertion.rawId),
      type: assertion.type,
      response: {
        authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
        clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
        signature: bufferToBase64url(assertion.response.signature),
        userHandle: assertion.response.userHandle
          ? bufferToBase64url(assertion.response.userHandle)
          : null,
      },
      _challenge_id: challengeId,
    };

    const completeResp = await fetch('/api/auth/login/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!completeResp.ok) {
      const err = await completeResp.json();
      throw new Error(err.detail || 'Login verification failed');
    }
    return true;
  },

  /**
   * Try Conditional UI (passkey autofill). Non-blocking -- sets up the
   * mediation: "conditional" request that resolves when user taps the
   * passkey suggestion.
   * @param {AbortController} abortController
   * @returns {Promise<boolean>}
   */
  async loginConditional(abortController) {
    const beginResp = await fetch('/api/auth/login/begin', { method: 'POST' });
    if (!beginResp.ok) return false;
    const options = await beginResp.json();
    const challengeId = options._challenge_id;

    options.challenge = base64urlToBuffer(options.challenge);
    if (options.allowCredentials) {
      options.allowCredentials = options.allowCredentials.map(c => ({
        ...c,
        id: base64urlToBuffer(c.id),
      }));
    }
    delete options._challenge_id;

    try {
      const assertion = await navigator.credentials.get({
        publicKey: options,
        mediation: 'conditional',
        signal: abortController.signal,
      });
      if (!assertion) return false;

      const body = {
        id: assertion.id,
        rawId: bufferToBase64url(assertion.rawId),
        type: assertion.type,
        response: {
          authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
          clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
          signature: bufferToBase64url(assertion.response.signature),
          userHandle: assertion.response.userHandle
            ? bufferToBase64url(assertion.response.userHandle)
            : null,
        },
        _challenge_id: challengeId,
      };

      const completeResp = await fetch('/api/auth/login/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      return completeResp.ok;
    } catch (e) {
      if (e.name === 'AbortError') return false;
      throw e;
    }
  },

  /** Logout -- clear server session and cookie. */
  async logout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.reload();
  },
};

// --- Base64url helpers ---

function base64urlToBuffer(base64url) {
  const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
  const pad = base64.length % 4 === 0 ? '' : '='.repeat(4 - (base64.length % 4));
  const binary = atob(base64 + pad);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
