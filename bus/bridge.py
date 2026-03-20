"""MQTT ↔ Redis Streams bridge.

Thin forwarder — no business logic. Converts between MQTT topics and
Redis Stream keys using a simple naming convention:
  MQTT:  {domain}/{event_type}
  Redis: alfred:{domain}:{event_type}
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis
from aiomqtt import Client as MqttClient

if TYPE_CHECKING:
    from shared.types import AioRedis

logger = logging.getLogger(__name__)

STREAM_PREFIX = "alfred"


def mqtt_topic_to_stream_key(topic: str) -> str:
    """Convert MQTT topic 'home/state_changed' → Redis stream 'alfred:home:state_changed'."""
    parts = topic.replace("/", ":")
    return f"{STREAM_PREFIX}:{parts}"


def stream_key_to_mqtt_topic(stream_key: str) -> str:
    """Convert Redis stream 'alfred:home:state_changed' → MQTT topic 'home/state_changed'."""
    without_prefix = stream_key.removeprefix(f"{STREAM_PREFIX}:")
    return without_prefix.replace(":", "/")


async def forward_mqtt_to_redis(
    redis: AioRedis,
    mqtt_topic: str,
    payload: bytes,
) -> None:
    """Forward an MQTT message to the corresponding Redis Stream."""
    stream_key = mqtt_topic_to_stream_key(mqtt_topic)
    await redis.xadd(stream_key, {"event": payload.decode()})
    logger.debug("MQTT → Redis: %s → %s", mqtt_topic, stream_key)


async def forward_redis_to_mqtt(
    mqtt: MqttClient,
    stream_key: str,
    event_data: dict[str, Any],
) -> None:
    """Forward a Redis Stream entry to the corresponding MQTT topic."""
    topic = stream_key_to_mqtt_topic(stream_key)
    payload = event_data.get("event", "{}")
    await mqtt.publish(topic, payload.encode())
    logger.debug("Redis → MQTT: %s → %s", stream_key, topic)


async def run_bridge(
    redis_url: str = "redis://localhost:6379",
    mqtt_host: str = "localhost",
    mqtt_port: int = 1883,
    mqtt_topics: list[str] | None = None,
    redis_streams: list[str] | None = None,
) -> None:
    """Main bridge loop."""
    if mqtt_topics is None:
        mqtt_topics = ["home/#", "media/#"]
    if redis_streams is None:
        redis_streams = ["alfred:reflex:actions"]

    redis: AioRedis = aioredis.from_url(redis_url)
    async with MqttClient(mqtt_host, mqtt_port) as mqtt:
        for topic in mqtt_topics:
            await mqtt.subscribe(topic)
            logger.info("Subscribed to MQTT topic: %s", topic)

        await asyncio.gather(
            _mqtt_to_redis_loop(mqtt, redis),
            _redis_to_mqtt_loop(redis, mqtt, redis_streams),
        )


async def _mqtt_to_redis_loop(mqtt: MqttClient, redis: AioRedis) -> None:
    """Listen for MQTT messages and forward to Redis."""
    async for message in mqtt.messages:
        raw = message.payload
        payload = raw if isinstance(raw, bytes) else str(raw).encode()
        await forward_mqtt_to_redis(
            redis=redis,
            mqtt_topic=str(message.topic),
            payload=payload,
        )


async def _redis_to_mqtt_loop(
    redis: AioRedis,
    mqtt: MqttClient,
    streams: list[str],
) -> None:
    """Listen for Redis Stream entries and forward to MQTT."""
    # Redis xread IDs keyed by stream name → last-seen entry ID ("0" = from beginning)
    last_ids: dict[bytes | str | memoryview[int], int | bytes | str | memoryview[int]] = {
        s: "0" for s in streams
    }
    while True:
        results: list[
            tuple[bytes | str, list[tuple[bytes | str, dict[bytes | str, bytes | str]]]]
        ] = await redis.xread(last_ids, block=1000)
        for stream_key_raw, entries in results:
            stream_key = (
                stream_key_raw.decode() if isinstance(stream_key_raw, bytes) else stream_key_raw
            )
            for entry_id, data in entries:
                decoded: dict[str, str] = {
                    (k.decode() if isinstance(k, bytes) else k): (
                        v.decode() if isinstance(v, bytes) else v
                    )
                    for k, v in data.items()
                }
                await forward_redis_to_mqtt(mqtt, stream_key, decoded)
                last_ids[stream_key] = entry_id
