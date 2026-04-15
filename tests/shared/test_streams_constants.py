def test_device_tokens_key_exists() -> None:
    from shared.streams import DEVICE_TOKENS_KEY

    assert DEVICE_TOKENS_KEY == "alfred:push:devices"
