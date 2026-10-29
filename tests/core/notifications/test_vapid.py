"""The VAPID key: generated once, kept in the keyring, signing each push service's token."""

import asyncio
import base64
import time
from collections.abc import Iterator
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from keyring.errors import KeyringError
from loguru import logger

from core.notifications import vapid
from core.notifications.vapid import (
    VAPID_SECRET,
    VAPID_SUBJECT_ENV,
    VAPID_TOKEN_LIFETIME_S,
    ensure_vapid_keys,
    generate_private_pem,
    load_keys,
    vapid_subject,
)
from shared import secrets
from shared.secrets import get_secret

SUBJECT = "mailto:you@example.com"


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _token(header: str, public_key: str) -> str:
    return header.removeprefix("vapid t=").removesuffix(f", k={public_key}")


def _stored_pem() -> str:
    stored = get_secret(*VAPID_SECRET)
    assert stored is not None
    return stored


@pytest.fixture
def logs() -> Iterator[list[str]]:
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="INFO")
    yield messages
    logger.remove(sink_id)


def test_public_key_is_87_char_b64url_point() -> None:
    keys = load_keys(generate_private_pem())
    assert len(keys.public_key) == 87
    raw = _b64url_decode(keys.public_key)
    assert len(raw) == 65
    assert raw[0] == 0x04


def test_private_key_stays_out_of_repr() -> None:
    keys = load_keys(generate_private_pem())
    assert "PRIVATE KEY" not in repr(keys)


def test_authorization_verifies() -> None:
    keys = load_keys(generate_private_pem())
    now = time.time()
    header = keys.authorization("https://web.push.apple.com/QGuQyavXutnMH8", SUBJECT, now)

    assert header.startswith("vapid t=")
    assert header.endswith(f", k={keys.public_key}")
    # Verify against the key the header advertises, as the push service does.
    advertised = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), _b64url_decode(keys.public_key)
    )
    claims = jwt.decode(
        _token(header, keys.public_key),
        advertised,
        algorithms=["ES256"],
        audience="https://web.push.apple.com",
    )
    assert claims["sub"] == SUBJECT
    assert claims["exp"] == int(now) + VAPID_TOKEN_LIFETIME_S


def test_audience_drops_the_path() -> None:
    keys = load_keys(generate_private_pem())
    header = keys.authorization("https://fcm.googleapis.com/fcm/send/abc", SUBJECT, time.time())
    claims = jwt.decode(_token(header, keys.public_key), options={"verify_signature": False})
    assert claims["aud"] == "https://fcm.googleapis.com"


def test_load_keys_accepts_escaped_newlines() -> None:
    pem = generate_private_pem()
    escaped = pem.replace("\n", "\\n")
    assert "\n" not in escaped
    assert load_keys(escaped) == load_keys(pem)


def test_load_keys_refuses_another_curve() -> None:
    pem = (
        ec.generate_private_key(ec.SECP384R1())
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )
    with pytest.raises(ValueError, match="P-256"):
        load_keys(pem)


async def test_ensure_generates_once(logs: list[str]) -> None:
    first = await ensure_vapid_keys()
    assert _stored_pem().startswith("-----BEGIN PRIVATE KEY-----")

    second = await ensure_vapid_keys()
    assert second.public_key == first.public_key
    assert sum("Generated a VAPID key" in m for m in logs) == 1


async def test_concurrent_first_callers_share_one_key(logs: list[str]) -> None:
    results = await asyncio.gather(*(ensure_vapid_keys() for _ in range(8)))
    assert {keys.public_key for keys in results} == {load_keys(_stored_pem()).public_key}
    assert sum("Generated a VAPID key" in m for m in logs) == 1


async def test_ensure_never_generates_over_an_unreadable_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreadable(integration: str, field: str) -> str | None:
        raise KeyringError("locked")

    stored = Mock()
    generate = Mock(side_effect=generate_private_pem)
    monkeypatch.setattr(secrets, "get_secret", unreadable)
    monkeypatch.setattr(secrets, "set_secret", stored)
    monkeypatch.setattr(vapid, "generate_private_pem", generate)

    with pytest.raises(KeyringError, match="locked"):
        await ensure_vapid_keys()
    generate.assert_not_called()
    stored.assert_not_called()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", None),
        ("you@example.com", None),
        ("mailto:", None),
        (" https: ", None),
        ("mailto:you@example.com", "mailto:you@example.com"),
        (" https://alfred.example.com ", "https://alfred.example.com"),
    ],
)
def test_vapid_subject(monkeypatch: pytest.MonkeyPatch, value: str, expected: str | None) -> None:
    monkeypatch.setenv(VAPID_SUBJECT_ENV, value)
    assert vapid_subject() == expected


def test_vapid_subject_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(VAPID_SUBJECT_ENV, raising=False)
    assert vapid_subject() is None


def test_a_bad_subject_warns_by_name_not_value(
    monkeypatch: pytest.MonkeyPatch, logs: list[str]
) -> None:
    monkeypatch.setenv(VAPID_SUBJECT_ENV, "you@example.com")
    assert vapid_subject() is None
    warnings = [m for m in logs if "WARNING" in m]
    assert len(warnings) == 1
    assert VAPID_SUBJECT_ENV in warnings[0]
    assert "you@example.com" not in warnings[0]
