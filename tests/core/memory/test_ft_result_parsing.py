"""FT.SEARCH result parsing across RESP2 and RESP3.

Regression: `_parse_ft_results` accepted only RESP2's flat array. redis-py 8
negotiates RESP3, where FT.SEARCH returns a mapping — so the isinstance guard
rejected every response and semantic recall silently returned nothing. Redis was
finding and scoring the matches correctly the whole time; the parser dropped them.
Nothing raised, nothing logged, and no test covered this function.

The RESP3 fixture below is a verbatim capture from the production index.
"""

from __future__ import annotations

from core.memory.redis_vector_store import _parse_ft_results

_CONTENT = b"[reflex:state_change] home.light_turn_on(target=Living Room) \xe2\x86\x92 success"

_RESP3 = {
    b"attributes": [],
    b"format": b"STRING",
    b"total_results": 2,
    b"warning": [],
    b"results": [
        {
            b"id": b"ctx:68e8049c-c32f-4cce-9931-a4e518f5d170",
            b"values": [],
            b"extra_attributes": {
                b"__score": b"0.492147922516",
                b"content": _CONTENT,
                b"semantic_key": b"Reflex state_change action: home.light_turn_on",
                b"type": b"episodic",
                b"source": b"reflex",
                b"entities": b"media_player.living_room_living_room_apple_tv",
                b"timestamp": b"1786592925.6",
                b"significance": b"0.147",
                b"retrieval_count": b"0",
                b"last_retrieved": b"0",
                b"compressed": b"",
            },
        },
        {
            b"id": b"ctx:aca52a5d-ceb8-47ea-9857-aedc9daab4c7",
            b"values": [],
            b"extra_attributes": {
                b"__score": b"0.800000000000",
                b"content": b"something barely related",
                b"semantic_key": b"",
                b"type": b"episodic",
                b"source": b"reflex",
                b"entities": b"",
                b"timestamp": b"1787962072.29",
                b"significance": b"0.123",
                b"retrieval_count": b"3",
                b"last_retrieved": b"1787962099.0",
                b"compressed": b"",
            },
        },
    ],
}

_RESP2 = [
    2,
    b"ctx:68e8049c-c32f-4cce-9931-a4e518f5d170",
    [
        b"__score",
        b"0.492147922516",
        b"content",
        _CONTENT,
        b"type",
        b"episodic",
        b"source",
        b"reflex",
        b"timestamp",
        b"1786592925.6",
        b"significance",
        b"0.147",
        b"retrieval_count",
        b"0",
        b"last_retrieved",
        b"0",
        b"compressed",
        b"",
    ],
]


def test_parses_the_resp3_mapping() -> None:
    results = _parse_ft_results(_RESP3, 0.0)

    assert len(results) == 2
    first = results[0]
    assert first.id == "68e8049c-c32f-4cce-9931-a4e518f5d170"  # ctx: prefix stripped
    assert first.content == _CONTENT.decode()
    assert first.score == 1.0 - 0.492147922516  # cosine distance → similarity
    assert first.metadata.type == "episodic"
    assert first.metadata.source == "reflex"
    assert first.metadata.entities == "media_player.living_room_living_room_apple_tv"
    assert first.metadata.significance == 0.147
    assert results[1].metadata.retrieval_count == 3


def test_still_parses_the_resp2_flat_array() -> None:
    """The server protocol is negotiated, not pinned — both shapes must work."""
    results = _parse_ft_results(_RESP2, 0.0)

    assert len(results) == 1
    assert results[0].id == "68e8049c-c32f-4cce-9931-a4e518f5d170"
    assert results[0].content == _CONTENT.decode()
    assert results[0].metadata.source == "reflex"


def test_min_similarity_filters_resp3_results() -> None:
    """Distances 0.492 and 0.800 → similarities 0.508 and 0.200."""
    results = _parse_ft_results(_RESP3, 0.5)

    assert len(results) == 1
    assert results[0].id == "68e8049c-c32f-4cce-9931-a4e518f5d170"


def test_unusable_payloads_yield_no_results() -> None:
    assert _parse_ft_results(None, 0.0) == []
    assert _parse_ft_results({}, 0.0) == []
    assert _parse_ft_results({b"results": []}, 0.0) == []
    assert _parse_ft_results([0], 0.0) == []


def test_parses_ft_info_from_both_protocols() -> None:
    """Same RESP3 blind spot: FT.INFO is a mapping too, so count() read 0 of 18 docs."""
    from core.memory.redis_vector_store import _parse_ft_info

    assert _parse_ft_info({b"num_docs": 18, b"index_name": b"idx:context"})["num_docs"] == 18
    assert _parse_ft_info([b"num_docs", 18, b"index_name", b"idx:context"])["num_docs"] == 18
    assert _parse_ft_info(None) == {}
