"""Shared configuration loader. Reads from environment variables with .env fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (walk up from this file to find it)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


@dataclass(frozen=True)
class AlfredConfig:
    redis_host: str = "localhost"
    redis_port: int = 6379
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3:8b"
    lmstudio_host: str = "http://localhost:1234"
    ha_host: str = "http://homeassistant.local:8123"
    ha_token: str = ""
    research_vault_path: str = "./research"
    signoz_enabled: bool = True
    otel_endpoint: str = "http://localhost:4317"

    # Phase 3: Conscious Engine
    claude_api_key: str = ""
    claude_model: str = "claude-opus-4-6"
    session_timeout_minutes: int = 30
    proactivity_level: str = "opinionated"  # opinionated | moderate | conservative

    # Phase 3: Cost
    daily_cost_cap_usd: float = 5.0

    # Phase 3: Memory
    episodic_hot_days: int = 7
    episodic_compress_days: int = 90

    # Phase 3: Voice
    voice_confidence_threshold: float = 0.85

    # Phase 3: Signal
    signal_phone_number: str = ""

    # Phase 3: Logging
    log_level: str = "INFO"
    log_json: bool = False

    @classmethod
    def from_env(cls) -> AlfredConfig:
        return cls(
            redis_host=os.getenv("REDIS_HOST", "localhost"),
            redis_port=int(os.getenv("REDIS_PORT", "6379")),
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3:8b"),
            lmstudio_host=os.getenv("LMSTUDIO_HOST", "http://localhost:1234"),
            ha_host=os.getenv("HA_HOST", "http://homeassistant.local:8123"),
            ha_token=os.getenv("HA_TOKEN", ""),
            research_vault_path=os.getenv("RESEARCH_VAULT_PATH", "./research"),
            signoz_enabled=os.getenv("SIGNOZ_ENABLED", "true").lower() == "true",
            otel_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
            # Phase 3: Conscious Engine
            claude_api_key=os.getenv("CLAUDE_API_KEY", ""),
            claude_model=os.getenv("CLAUDE_MODEL", "claude-opus-4-6"),
            session_timeout_minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")),
            proactivity_level=os.getenv("PROACTIVITY_LEVEL", "opinionated"),
            # Phase 3: Cost
            daily_cost_cap_usd=float(os.getenv("DAILY_COST_CAP_USD", "5.0")),
            # Phase 3: Memory
            episodic_hot_days=int(os.getenv("EPISODIC_HOT_DAYS", "7")),
            episodic_compress_days=int(os.getenv("EPISODIC_COMPRESS_DAYS", "90")),
            # Phase 3: Voice
            voice_confidence_threshold=float(os.getenv("VOICE_CONFIDENCE_THRESHOLD", "0.85")),
            # Phase 3: Signal
            signal_phone_number=os.getenv("SIGNAL_PHONE_NUMBER", ""),
            # Phase 3: Logging
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_json=os.getenv("LOG_JSON", "false").lower() == "true",
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"
