"""Tests for ChannelRegistry.get_instance()."""

from unittest.mock import MagicMock

from core.notifications.channels import ChannelRegistry


def test_get_instance_returns_registered_adapter() -> None:
    adapter = MagicMock()
    ChannelRegistry.set_instance("test_adapter", adapter)
    try:
        assert ChannelRegistry.get_instance("test_adapter") is adapter
    finally:
        ChannelRegistry._instances.pop("test_adapter", None)


def test_get_instance_returns_none_for_missing() -> None:
    assert ChannelRegistry.get_instance("nonexistent") is None
