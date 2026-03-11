"""Tests for TriggerRegistry — decorator-based type registration."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from core.triggers.models import BaseTrigger, TriggerContext
from core.triggers.registry import TriggerRegistry


class _DummyTrigger(BaseTrigger):
    """Test trigger type for registry tests."""

    trigger_type: str = "dummy"

    class Conditions(BaseModel):
        foo: str = "bar"

    conditions: Conditions = Conditions()

    def evaluate(self, context: TriggerContext) -> bool:
        return True


def test_register_and_get(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    TriggerRegistry.register_type("dummy")(_DummyTrigger)
    assert TriggerRegistry.get("dummy") is _DummyTrigger


def test_get_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    with pytest.raises(KeyError, match="nope"):
        TriggerRegistry.get("nope")


def test_available_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    TriggerRegistry.register_type("alpha")(_DummyTrigger)
    TriggerRegistry.register_type("beta")(_DummyTrigger)
    assert sorted(TriggerRegistry.available_types()) == ["alpha", "beta"]


def test_build_conditions_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    TriggerRegistry.register_type("dummy")(_DummyTrigger)
    docs = TriggerRegistry.build_conditions_docs()
    assert "dummy" in docs
    assert "foo" in docs
