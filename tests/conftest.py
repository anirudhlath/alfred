import keyring
import keyring.backend
import pytest


class InMemoryKeyring(keyring.backend.KeyringBackend):
    """In-memory keyring backend for testing. Shared across test modules."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get(service, {}).get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store.setdefault(service, {})[username] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self._store[service][username]
        except KeyError:
            from keyring.errors import PasswordDeleteError

            raise PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def _mock_keyring() -> None:
    """Install fresh in-memory keyring backend before each test."""
    keyring.set_keyring(InMemoryKeyring())
