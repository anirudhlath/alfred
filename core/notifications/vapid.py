"""VAPID — the server key Web Push authenticates with (RFC 8292).

One P-256 key per deployment, generated on first use and kept in the keyring under
``VAPID_SECRET``. Its public half is what a browser subscribes with, so the key must
outlive every subscription: a read that fails propagates rather than generating a new key
over one we merely could not see, which would silently orphan every subscribed browser.

Each push carries ``Authorization: vapid t=<jwt>, k=<public key>``; the JWT names the push
service's origin (``aud``), an expiry (``exp``) and a contact for the service's operator
(``sub``, from ``WEBPUSH_VAPID_SUBJECT``).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlsplit

import jwt  # PyJWT[crypto]
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from loguru import logger

from shared.secrets import aget_secret, aset_secret

VAPID_SUBJECT_ENV: Final = "WEBPUSH_VAPID_SUBJECT"
VAPID_SECRET: Final = ("webpush", "vapid_private_key")  # (integration, field) in the keyring
VAPID_TOKEN_LIFETIME_S: Final = 43_200  # 12 h; RFC 8292 caps it at 24 h

_SUBJECT_SCHEMES: Final = ("mailto:", "https:")


def vapid_subject() -> str | None:
    """The VAPID contact from the environment, or ``None`` when unset or unusable."""
    value = os.environ.get(VAPID_SUBJECT_ENV, "").strip()
    if not value:
        return None
    if not value.startswith(_SUBJECT_SCHEMES):
        # The value is the owner's contact address: name the variable, never echo it.
        logger.warning("{} must start with mailto: or https:; ignoring it", VAPID_SUBJECT_ENV)
        return None
    return value


def generate_private_pem() -> str:
    """A new P-256 private key as PKCS#8 PEM."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@dataclass(frozen=True)
class VapidKeys:
    """The VAPID key pair: the PEM that signs, the public key that browsers subscribe with."""

    private_pem: str = field(repr=False)
    public_key: str  # unpadded base64url of the X9.62 uncompressed point, 87 characters

    def authorization(self, endpoint: str, subject: str, now: float) -> str:
        """The ``Authorization`` header for a push to ``endpoint``."""
        parts = urlsplit(endpoint)
        claims = {
            "aud": f"{parts.scheme}://{parts.hostname}",
            "exp": int(now) + VAPID_TOKEN_LIFETIME_S,
            "sub": subject,
        }
        token = jwt.encode(claims, self.private_pem, algorithm="ES256")
        return f"vapid t={token}, k={self.public_key}"


def load_keys(pem: str) -> VapidKeys:
    """The key pair from a PEM, accepting escaped ``\\n`` as the APNs adapter does."""
    normalized = pem.replace("\\n", "\n")
    key = serialization.load_pem_private_key(normalized.encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("VAPID key must be a P-256 private key")
    point = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public_key = base64.urlsafe_b64encode(point).rstrip(b"=").decode()
    return VapidKeys(private_pem=normalized, public_key=public_key)


async def ensure_vapid_keys() -> VapidKeys:
    """The deployment's VAPID keys, generating and storing them the first time.

    A keyring read that raises propagates and nothing is generated: only a read that
    succeeds and finds nothing means there is no key yet.
    """
    pem = await aget_secret(*VAPID_SECRET)
    if not pem:
        pem = generate_private_pem()
        await aset_secret(*VAPID_SECRET, pem)
        logger.info("Generated a VAPID key")
    return load_keys(pem)
