"""Tests for AlfredClient."""

import pytest


def test_client_stores_config():
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(
        redis_url="redis://testhost:6379",
        mqtt_host="mqtthost",
        service_name="test-service",
    )
    assert client.service_name == "test-service"
    assert client.redis_url == "redis://testhost:6379"


def test_client_collects_tools():
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(service_name="test")

    @client.tool(name="test.hello", description="Say hello")
    def hello(name: str) -> str:
        return f"Hello {name}"

    assert len(client.tools) == 1
    assert client.tools[0]["name"] == "test.hello"


def test_client_collects_publishers():
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(service_name="test")

    @client.publisher(topic="test/events")
    def emit_event(data):
        return data

    assert len(client.publishers) == 1
    assert client.publishers[0]["topic"] == "test/events"


def test_client_collects_subscribers():
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(service_name="test")

    @client.subscriber(topic="test/commands")
    def on_command(payload):
        pass

    assert len(client.subscribers) == 1
    assert client.subscribers[0]["topic"] == "test/commands"


def test_client_generates_tool_manifest():
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(
        service_name="home-service",
        service_endpoint="http://home-service:8000/mcp",
    )

    @client.tool(name="smart_home.dim_lights", description="Dim lights")
    def dim_lights(room: str, level: int):
        return {"ok": True}

    manifest = client.get_registration_manifest()
    assert manifest["service_name"] == "home-service"
    assert manifest["service_endpoint"] == "http://home-service:8000/mcp"
    assert len(manifest["tools"]) == 1
    assert manifest["tools"][0]["name"] == "smart_home.dim_lights"


@pytest.mark.asyncio
async def test_dispatch_calls_registered_async_tool() -> None:
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(service_name="test-service")

    @client.tool(name="test.greet", description="Say hello")
    async def greet(name: str) -> dict[str, str]:
        return {"message": f"Hello, {name}!"}

    result = await client.dispatch("test.greet", {"name": "Alfred"})
    assert result == {"message": "Hello, Alfred!"}


@pytest.mark.asyncio
async def test_dispatch_calls_registered_sync_tool() -> None:
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(service_name="test-service")

    @client.tool(name="test.add", description="Add two numbers")
    def add(a: int, b: int) -> dict[str, int]:
        return {"sum": a + b}

    result = await client.dispatch("test.add", {"a": 2, "b": 3})
    assert result == {"sum": 5}


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_raises() -> None:
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(service_name="test-service")

    with pytest.raises(KeyError, match="Unknown tool"):
        await client.dispatch("nonexistent.tool", {})
