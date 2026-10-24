"""Tests for classifying who caused a state change and phrasing it."""

from __future__ import annotations

from bus.schemas.events import ChangeContext
from core.attribution.cause import ATTRIBUTED, cause_of, cause_words


def test_no_context_has_no_cause() -> None:
    assert cause_of(None) is None
    assert cause_words(None) is None
    assert cause_words(None, "Alex") is None


def test_by_service_wins_over_user_and_parent() -> None:
    context = ChangeContext(user_id="u", parent_id="p", by_service=True)

    assert cause_of(context) == "alfred"
    assert cause_words(context) == "by Alfred"
    assert cause_words(context, "Alex") == "by Alfred"


def test_user_is_a_person() -> None:
    assert cause_of(ChangeContext(user_id="u")) == "person"
    assert cause_words(ChangeContext(user_id="u")) == "by a person"
    assert cause_words(ChangeContext(user_id="u", person="person.alex")) == "by person.alex"
    assert cause_words(ChangeContext(user_id="u", person="person.alex"), "Alex") == "by Alex"
    assert cause_words(ChangeContext(user_id="u"), "Alex") == "by Alex"


def test_user_wins_over_parent() -> None:
    context = ChangeContext(user_id="u", parent_id="p")

    assert cause_of(context) == "person"
    assert cause_words(context) == "by a person"


def test_parent_without_user_is_an_automation() -> None:
    context = ChangeContext(parent_id="p")

    assert cause_of(context) == "automation"
    assert cause_words(context) == "by an automation"
    assert cause_words(context, "Alex") == "by an automation"


def test_neither_is_outside() -> None:
    context = ChangeContext(id="c")

    assert cause_of(context) == "outside"
    assert cause_words(context) == "by a switch, app or device"
    assert cause_words(context, "Alex") == "by a switch, app or device"


def test_an_empty_context_is_outside_not_unknown() -> None:
    assert cause_of(ChangeContext()) == "outside"


def test_attributed_is_the_three_causes_that_name_an_actor() -> None:
    assert frozenset({"person", "alfred", "automation"}) == ATTRIBUTED
