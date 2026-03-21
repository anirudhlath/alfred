"""Tests for ChannelAdapter ABC and ChannelRegistry."""

from __future__ import annotations

from typing import ClassVar

import pytest

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency


class FakeAdapter(ChannelAdapter):
    name: ClassVar[str] = "fake"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.INFORMATIONAL, Urgency.IMPORTANT}

    def __init__(self) -> None:
        self.delivered: list[Notification] = []

    async def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification)


class UrgentOnlyAdapter(ChannelAdapter):
    name: ClassVar[str] = "urgent_only"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.URGENT}

    async def deliver(self, notification: Notification) -> None:
        pass


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    """Reset registry between tests."""
    ChannelRegistry._registry.clear()
    ChannelRegistry._instances.clear()


class TestChannelAdapter:
    def test_supports_urgency_true(self) -> None:
        adapter = FakeAdapter()
        assert adapter.supports_urgency(Urgency.INFORMATIONAL) is True

    def test_supports_urgency_false(self) -> None:
        adapter = FakeAdapter()
        assert adapter.supports_urgency(Urgency.URGENT) is False


class TestChannelRegistry:
    def test_register_decorator(self) -> None:
        @ChannelRegistry.register()
        class TestAdapter(ChannelAdapter):
            name: ClassVar[str] = "test"
            supported_urgencies: ClassVar[set[Urgency]] = {Urgency.INFORMATIONAL}

            async def deliver(self, notification: Notification) -> None:
                pass

        assert "test" in ChannelRegistry._registry

    def test_get_adapters_for_urgency(self) -> None:
        ChannelRegistry._registry["fake"] = FakeAdapter
        ChannelRegistry._registry["urgent_only"] = UrgentOnlyAdapter
        ChannelRegistry.set_instance("fake", FakeAdapter())
        ChannelRegistry.set_instance("urgent_only", UrgentOnlyAdapter())

        adapters = ChannelRegistry.get_adapters_for_urgency(Urgency.INFORMATIONAL)
        names = [type(a).name for a in adapters]
        assert "fake" in names
        assert "urgent_only" not in names

    def test_get_adapters_caches_instances(self) -> None:
        ChannelRegistry._registry["fake"] = FakeAdapter
        ChannelRegistry.set_instance("fake", FakeAdapter())
        adapters1 = ChannelRegistry.get_adapters_for_urgency(Urgency.INFORMATIONAL)
        adapters2 = ChannelRegistry.get_adapters_for_urgency(Urgency.INFORMATIONAL)
        assert adapters1[0] is adapters2[0]

    def test_uninitialized_adapter_skipped(self) -> None:
        """Registered but not initialized adapters are not returned."""
        ChannelRegistry._registry["fake"] = FakeAdapter
        # Don't call set_instance
        adapters = ChannelRegistry.get_adapters_for_urgency(Urgency.INFORMATIONAL)
        assert len(adapters) == 0

    def test_available_returns_names(self) -> None:
        ChannelRegistry._registry["fake"] = FakeAdapter
        assert "fake" in ChannelRegistry.available()
