"""Tests for WebAuthn credential store."""

from __future__ import annotations

import pytest

from core.identity.credentials import CredentialStore


@pytest.fixture
async def store(tmp_path: object) -> CredentialStore:
    """Create a CredentialStore backed by a temp SQLite DB."""
    import pathlib

    db_path = pathlib.Path(str(tmp_path)) / "credentials.db"
    s = CredentialStore(db_path)
    await s.initialize()
    return s


FAKE_CRED_ID = "dGVzdC1jcmVkLWlk"
FAKE_PUBLIC_KEY = b"\x01\x02\x03\x04"
FAKE_DEVICE_NAME = "MacBook Pro"
FAKE_TRANSPORTS = ["internal", "hybrid"]


class TestCredentialStore:
    @pytest.mark.asyncio
    async def test_has_any_credential_empty(self, store: CredentialStore) -> None:
        assert await store.has_any_credential() is False

    @pytest.mark.asyncio
    async def test_save_and_get(self, store: CredentialStore) -> None:
        await store.save_credential(
            credential_id=FAKE_CRED_ID,
            public_key=FAKE_PUBLIC_KEY,
            sign_count=0,
            device_name=FAKE_DEVICE_NAME,
            transports=FAKE_TRANSPORTS,
        )
        cred = await store.get_credential(FAKE_CRED_ID)
        assert cred is not None
        assert cred.credential_id == FAKE_CRED_ID
        assert cred.public_key == FAKE_PUBLIC_KEY
        assert cred.sign_count == 0
        assert cred.device_name == FAKE_DEVICE_NAME
        assert cred.transports == ["internal", "hybrid"]
        assert cred.created_at is not None
        assert cred.last_used_at is not None

    @pytest.mark.asyncio
    async def test_has_any_credential_after_save(self, store: CredentialStore) -> None:
        await store.save_credential(
            credential_id=FAKE_CRED_ID,
            public_key=FAKE_PUBLIC_KEY,
            sign_count=0,
            device_name=FAKE_DEVICE_NAME,
            transports=FAKE_TRANSPORTS,
        )
        assert await store.has_any_credential() is True

    @pytest.mark.asyncio
    async def test_list_credentials(self, store: CredentialStore) -> None:
        await store.save_credential(
            credential_id="cred-1",
            public_key=b"\x01",
            sign_count=0,
            device_name="Device A",
            transports=["internal"],
        )
        await store.save_credential(
            credential_id="cred-2",
            public_key=b"\x02",
            sign_count=0,
            device_name="Device B",
            transports=["hybrid"],
        )
        creds = await store.list_credentials()
        assert len(creds) == 2
        ids = {c.credential_id for c in creds}
        assert ids == {"cred-1", "cred-2"}

    @pytest.mark.asyncio
    async def test_update_sign_count(self, store: CredentialStore) -> None:
        await store.save_credential(
            credential_id=FAKE_CRED_ID,
            public_key=FAKE_PUBLIC_KEY,
            sign_count=0,
            device_name=FAKE_DEVICE_NAME,
            transports=FAKE_TRANSPORTS,
        )
        await store.update_sign_count(FAKE_CRED_ID, 5)
        cred = await store.get_credential(FAKE_CRED_ID)
        assert cred is not None
        assert cred.sign_count == 5

    @pytest.mark.asyncio
    async def test_delete_credential(self, store: CredentialStore) -> None:
        await store.save_credential(
            credential_id=FAKE_CRED_ID,
            public_key=FAKE_PUBLIC_KEY,
            sign_count=0,
            device_name=FAKE_DEVICE_NAME,
            transports=FAKE_TRANSPORTS,
        )
        await store.delete_credential(FAKE_CRED_ID)
        cred = await store.get_credential(FAKE_CRED_ID)
        assert cred is None
        assert await store.has_any_credential() is False

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store: CredentialStore) -> None:
        cred = await store.get_credential("does-not-exist")
        assert cred is None

    @pytest.mark.asyncio
    async def test_get_or_create_user_id(self, store: CredentialStore) -> None:
        uid1 = await store.get_or_create_user_id()
        uid2 = await store.get_or_create_user_id()
        assert uid1 == uid2
        assert len(uid1) == 64  # 32 bytes as hex
