# WebAuthn Registration Off-LAN with a Pairing Code

**Feature:** `X-Pairing-Code` alternative to `require_trusted_network` on `POST /api/auth/register/{begin,complete}` (`_registration_gate` in `core/identity/auth_routes.py`)
**Priority:** high
**Type:** integration

## Prerequisites
- Alfred core reachable from both a trusted network path and an untrusted one (e.g. the public hostname through the reverse proxy, or a phone on cellular)
- Device A: already has a passkey registered and a live `alfred_auth` session (this is what mints the code)
- Device B: a real second device with a browser supporting WebAuthn, on a network path the trusted-network gate rejects — confirm it is genuinely untrusted before starting (step 1)
- `FORWARDED_ALLOW_IPS` correctly set if a reverse proxy is in the path, or the gate will judge the proxy and device B will look trusted
- Redis reachable (the code, its TTL and the failure counter all live there)

## Test Steps
1. From device B, `POST /api/auth/register/begin` with no `X-Pairing-Code` header. Confirm **403** `Access restricted to trusted networks: <ip> is not trusted.` — this establishes that B really is off-LAN, so the rest of the case proves the code and not a mis-set trusted range.
2. On device A, `POST /api/auth/pairing`. Confirm **200** with `{"code", "expires_at", "ttl_seconds": 300}`, a 6-digit code, and `expires_at` five minutes out.
3. On device B, register through the onboarding wizard (or by hand) sending `X-Pairing-Code: <code>` on **both** `register/begin` and `register/complete`. Confirm the ceremony completes, the response is 200, and `alfred_auth` is set on B.
4. Confirm the code is consumed: repeat step 3 with the same code from a third path (or B again after clearing its cookie) and confirm **403** `Invalid or expired pairing code`. Check `alfred:webauthn:pairing` is gone in Redis.
5. Confirm B's new passkey is real: `GET /api/auth/credentials` from either device lists it, and B can log in again with `login/begin` + `login/complete` (login was never network-gated).
6. **Negative — wrong guesses burn the code.** Mint a fresh code on A. From B, send nine wrong six-digit codes on `register/begin`, confirming **403** `Invalid or expired pairing code` each time, `alfred:webauthn:pairing:fails` climbing to 9, and `alfred:webauthn:pairing` still present. Send a tenth wrong code and confirm the key is now gone — then confirm the *correct* code also returns 403, because it has been burned rather than merely mistyped.
7. **Negative — malformed and empty headers.** From B send `X-Pairing-Code: abcdef` and `X-Pairing-Code: 12345` (403 `Invalid or expired pairing code`, and confirm `alfred:webauthn:pairing:fails` did **not** increment — malformed codes are refused uncounted). Then send an **empty-valued** header and confirm the answer is the trusted-network **403**, not the pairing one — an empty header must read as no header at all. In curl the empty value is `-H 'X-Pairing-Code;'` with a **semicolon**: `-H 'X-Pairing-Code:'` with a colon and nothing after it *removes* the header instead of sending it empty, which would test nothing. The whitespace-only case cannot be reached with curl at all: curl strips the value and drops the header entirely, exactly as the colon form does (verified on the wire with curl 8.21.0; a tab behaves the same), so curl would silently send *no* header and test nothing. Use a client that transmits the value verbatim — the stdlib's `http.client` does: `python -c 'import http.client, json; c = http.client.HTTPSConnection("alfred.example.com"); c.request("POST", "/api/auth/register/begin", body=json.dumps({"device_name": "probe"}), headers={"Content-Type": "application/json", "X-Pairing-Code": "   "}); print(c.getresponse().status)'`. Do **not** substitute httpx: h11 refuses the value before anything is written (`httpx.LocalProtocolError: Illegal header value b'   '`, measured on httpx 0.28.1), so the request never leaves the machine. Expect the trusted-network **403** and no increment on `alfred:webauthn:pairing:fails` — the gate strips before it looks, so an all-whitespace header reads as absent too.
8. **Negative — expiry.** Mint a code on A, wait out the five minutes without using it, then try it from B. Confirm **403** and that the guess was not counted (no live code to guess at).

## Expected Result
- A device on an untrusted network can register a passkey with a valid pairing code and cannot without one.
- The code is single-use, dies at five minutes, and is burned by the tenth wrong guess.
- A malformed or empty header never consumes budget: malformed is a 403 without counting, empty falls through to the network gate.
- The registration that spends a code is indistinguishable from a LAN registration afterwards — same credential row, same session, same login path.

## Notes
- Minting is **session-gated only**, deliberately: requiring the LAN to mint would defeat the point, since the signed-in device is often the one that is away. The five-minute/ten-guess budget stands in for the network half.
- Minting again overwrites any active code and resets the failure counter — that is the recovery path if someone burns a code with ten off-LAN guesses (an accepted denial-of-pairing trade).
- This case complements [`webauthn-registration-trusted-network.md`](webauthn-registration-trusted-network.md) (the LAN path) and [`web-passkey-flows.md`](web-passkey-flows.md) (register/login/logout/WS on localhost).
- Worth running once behind the real reverse proxy: if `FORWARDED_ALLOW_IPS` is wrong, device B may be judged by the proxy's RFC1918 address and pass the network gate outright, which would silently make this whole case pass for the wrong reason. Step 1 is what catches that.
