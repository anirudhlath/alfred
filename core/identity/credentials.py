"""WebAuthn credential store backed by SQLite."""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite


@dataclass(frozen=True)
class StoredCredential:
    """A stored WebAuthn credential."""

    credential_id: str
    public_key: bytes
    sign_count: int
    device_name: str
    transports: list[str]
    created_at: str
    last_used_at: str


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id          TEXT PRIMARY KEY,
    public_key  BLOB NOT NULL,
    sign_count  INTEGER NOT NULL,
    device_name TEXT NOT NULL,
    transports  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    last_used_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webauthn_user (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class CredentialStore:
    """Async SQLite store for WebAuthn credentials."""

    def __init__(self, db_path: pathlib.Path | None = None) -> None:
        if db_path is None:
            data_dir = pathlib.Path(os.getenv("ALFRED_DATA_DIR", "data"))
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "credentials.db"
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open DB connection and ensure schema exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA_SQL)
        await self._db.commit()

    async def close(self) -> None:
        """Close the DB connection."""
        if self._db:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            msg = "CredentialStore not initialized — call initialize() first"
            raise RuntimeError(msg)
        return self._db

    async def save_credential(
        self,
        *,
        credential_id: str,
        public_key: bytes,
        sign_count: int,
        device_name: str,
        transports: list[str],
    ) -> None:
        """Save a new WebAuthn credential."""
        now = datetime.now(UTC).isoformat()
        await self._conn().execute(
            "INSERT INTO webauthn_credentials "
            "(id, public_key, sign_count, device_name, transports, created_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (credential_id, public_key, sign_count, device_name, json.dumps(transports), now, now),
        )
        await self._conn().commit()

    async def get_credential(self, credential_id: str) -> StoredCredential | None:
        """Get a credential by ID, or None if not found."""
        cursor = await self._conn().execute(
            "SELECT * FROM webauthn_credentials WHERE id = ?",
            (credential_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_credential(row)

    async def list_credentials(self) -> list[StoredCredential]:
        """List all stored credentials."""
        cursor = await self._conn().execute(
            "SELECT * FROM webauthn_credentials ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        return [self._row_to_credential(row) for row in rows]

    async def update_sign_count(self, credential_id: str, new_count: int) -> None:
        """Update the sign count and last_used_at timestamp."""
        now = datetime.now(UTC).isoformat()
        await self._conn().execute(
            "UPDATE webauthn_credentials SET sign_count = ?, last_used_at = ? WHERE id = ?",
            (new_count, now, credential_id),
        )
        await self._conn().commit()

    async def delete_credential(self, credential_id: str) -> None:
        """Delete a credential by ID."""
        await self._conn().execute(
            "DELETE FROM webauthn_credentials WHERE id = ?",
            (credential_id,),
        )
        await self._conn().commit()

    async def has_any_credential(self) -> bool:
        """Check if any credential is registered."""
        cursor = await self._conn().execute("SELECT COUNT(*) FROM webauthn_credentials")
        row = await cursor.fetchone()
        return bool(row and row[0] > 0)

    async def get_or_create_user_id(self) -> str:
        """Get or create the single WebAuthn user ID (hex string)."""
        cursor = await self._conn().execute("SELECT value FROM webauthn_user WHERE key = 'user_id'")
        row = await cursor.fetchone()
        if row:
            return str(row[0])
        user_id = os.urandom(32).hex()
        await self._conn().execute(
            "INSERT INTO webauthn_user (key, value) VALUES ('user_id', ?)",
            (user_id,),
        )
        await self._conn().commit()
        return user_id

    @staticmethod
    def _row_to_credential(row: aiosqlite.Row) -> StoredCredential:
        return StoredCredential(
            credential_id=row["id"],
            public_key=bytes(row["public_key"]),
            sign_count=row["sign_count"],
            device_name=row["device_name"],
            transports=json.loads(row["transports"]),
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
        )
