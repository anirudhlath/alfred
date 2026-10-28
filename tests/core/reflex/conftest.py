"""Fixtures shared by the Reflex tests."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest import mock

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def restored_environ() -> Iterator[None]:
    """``os.environ`` put back as it was before the test, whatever the test exported.

    Building a Reflex agent calls ``apply_model_env``, which exports ``VLLM_BASE_URL`` and,
    when a key is configured, ``OPENROUTER_API_KEY``; left set, both reach every later test.
    """
    with mock.patch.dict(os.environ):
        yield
