"""Shared configuration loader. Reads from environment variables with .env fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AlfredConfig:
    redis_host: str = "localhost"
    redis_port: int = 6379
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3:8b"
    ha_host: str = "http://homeassistant.local:8123"
    ha_token: str = ""
    research_vault_path: str = "./research"
    signoz_enabled: bool = True
    otel_endpoint: str = "http://localhost:4317"

    @classmethod
    def from_env(cls) -> AlfredConfig:
        return cls(
            redis_host=os.getenv("REDIS_HOST", "localhost"),
            redis_port=int(os.getenv("REDIS_PORT", "6379")),
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3:8b"),
            ha_host=os.getenv("HA_HOST", "http://homeassistant.local:8123"),
            ha_token=os.getenv("HA_TOKEN", ""),
            research_vault_path=os.getenv("RESEARCH_VAULT_PATH", "./research"),
            signoz_enabled=os.getenv("SIGNOZ_ENABLED", "true").lower() == "true",
            otel_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"
