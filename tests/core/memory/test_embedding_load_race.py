"""Concurrent _load() calls must construct the embedding model exactly once.

A background warmup task racing the first real embed() request would otherwise
load two copies of the ~300M-param model (both via asyncio.to_thread).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from core.memory.embedding_provider import SentenceTransformerProvider

if TYPE_CHECKING:
    import pytest

_constructions = 0


class _FakeModel:
    def __init__(self, model_name: str) -> None:
        global _constructions
        _constructions += 1
        time.sleep(0.05)  # simulate slow model load

    def get_sentence_embedding_dimension(self) -> int:
        return 8

    def encode(self, text: Any, normalize_embeddings: bool = True) -> Any:
        raise NotImplementedError


def test_concurrent_load_constructs_model_once(monkeypatch: pytest.MonkeyPatch) -> None:
    global _constructions
    _constructions = 0
    monkeypatch.setattr("sentence_transformers.SentenceTransformer", _FakeModel)

    provider = SentenceTransformerProvider("fake-model")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(provider._load) for _ in range(2)]
        models = [f.result(timeout=5) for f in futures]

    assert _constructions == 1
    assert models[0] is models[1]
