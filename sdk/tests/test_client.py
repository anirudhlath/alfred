"""Tests for AlfredClient."""


def test_client_stores_config():
    from sdk.alfred_sdk.client import AlfredClient

    client = AlfredClient(
        redis_url="redis://testhost:6379",
        mqtt_host="mqtthost",
        service_name="test-service",
    )
    assert client.service_name == "test-service"
    assert client.redis_url == "redis://testhost:6379"
