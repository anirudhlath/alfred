"""Tests for MQTT ↔ Redis Streams bridge.

Uses mocked MQTT and Redis to test message forwarding without real services.
"""

import json
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import StateChangedEvent


@pytest.fixture
def sample_state_event() -> StateChangedEvent:
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="light.living_room",
        old_state="on",
        new_state="off",
        attributes={"brightness": 0},
    )


def test_mqtt_topic_to_redis_stream_key() -> None:
    from bus.bridge import mqtt_topic_to_stream_key

    assert mqtt_topic_to_stream_key("home/state_changed") == "alfred:home:state_changed"
    assert mqtt_topic_to_stream_key("media/playback") == "alfred:media:playback"


def test_redis_stream_key_to_mqtt_topic() -> None:
    from bus.bridge import stream_key_to_mqtt_topic

    assert stream_key_to_mqtt_topic("alfred:home:state_changed") == "home/state_changed"
    assert stream_key_to_mqtt_topic("alfred:media:playback") == "media/playback"


@pytest.mark.asyncio
async def test_forward_mqtt_to_redis(sample_state_event: StateChangedEvent) -> None:
    from bus.bridge import forward_mqtt_to_redis

    mock_redis = AsyncMock()
    payload = sample_state_event.model_dump_json().encode()

    await forward_mqtt_to_redis(
        redis=mock_redis,
        mqtt_topic="home/state_changed",
        payload=payload,
    )

    mock_redis.xadd.assert_called_once()
    call_args = mock_redis.xadd.call_args
    assert call_args[0][0] == "alfred:home:state_changed"


@pytest.mark.asyncio
async def test_forward_redis_to_mqtt() -> None:
    from bus.bridge import forward_redis_to_mqtt

    mock_mqtt = AsyncMock()
    event_data = {"event": json.dumps({"event_type": "action_request", "source": "reflex"})}

    await forward_redis_to_mqtt(
        mqtt=mock_mqtt,
        stream_key="alfred:home:command",
        event_data=event_data,
    )

    mock_mqtt.publish.assert_called_once()
    call_args = mock_mqtt.publish.call_args
    assert call_args[0][0] == "home/command"
