"""Shared configuration loader. Reads from environment variables with .env fallback."""

from __future__ import annotations

import functools
import logging
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from shared.env import is_truthy_flag

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

logger = logging.getLogger(__name__)

# Load .env from project root (walk up from this file to find it).
#
# Skipped under pytest: the suite must not inherit the developer's real config.
# A populated .env (REFLEX_BACKEND=openai + OPENAI_COMPAT_HOST, say) routes mocked
# code paths at live endpoints, so tests that pass in CI — which has no .env —
# fail locally with connection errors. Keeping the load out of test runs makes a
# local `pytest` mean the same thing as a CI one.
_env_path = Path(__file__).resolve().parent.parent / ".env"
if "pytest" not in sys.modules:
    load_dotenv(_env_path)


def data_root() -> Path:
    """Root directory for all runtime-writable state (env ``ALFRED_DATA_DIR``, default ``data``)."""
    return Path(os.getenv("ALFRED_DATA_DIR", "data")).resolve()


def data_path(*parts: str) -> Path:
    """Resolve a child path under the data root, ensuring its parent directory exists."""
    p = data_root().joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def data_mode() -> str:
    """Data lifecycle mode: ``persistent`` | ``ephemeral`` | ``seed`` (env ``ALFRED_DATA_MODE``)."""
    return os.getenv("ALFRED_DATA_MODE", "persistent")


# Cold-migration threshold for the Librarian's decay pass. Migration pressure is
# ``age - 2*significance - 1.5*recency - frequency`` and can never exceed 1.0 (age caps at
# 1.0; every other term only subtracts, and recency is strictly positive), so the old
# default of 1.0 meant nothing ever left hot storage. 0.2 is read off the D4 design's own
# behaviour table: a 30-day, significance-0.1, never-retrieved entry (pressure ~0.78)
# migrates, and a 60-day, significance-0.3 entry retrieved once a month ago (~0.18) is
# the borderline case that stays. Entries with significance >= 0.4 never migrate.
DEFAULT_DECAY_MIGRATION_THRESHOLD = 0.2

# Default embedding model: ungated so a fresh clone works with no HF token or license
# acceptance, and the model the recall floor below is calibrated for. Known models map to
# their output dimension so ``EMBEDDING_DIM`` stays in sync automatically — the vector
# index dimension must match the model, or search breaks. It is ~2.3 GB, and the
# in-process backend loads one copy per service; the ``openai`` backend shares one server.
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"

# The lowest similarity at which involuntary recall pulls a memory into a reply.
# Similarity sits on a per-model scale, so this belongs to DEFAULT_EMBEDDING_MODEL:
# bge-m3 scores ~0.1 above EmbeddingGemma, and at 0.5 it pulled memories into a fifth of
# off-topic questions (EXP-009). Recalibrate it whenever the model changes.
DEFAULT_INVOLUNTARY_RECALL_THRESHOLD = 0.575

# How the embedding model is run, and where. ``sentence_transformers`` keeps a fresh
# clone working with no server; ``openai`` talks to an OpenAI-compatible
# /v1/embeddings server (vLLM --runner pooling) at ``DEFAULT_EMBEDDING_HOST``.
DEFAULT_EMBEDDING_BACKEND = "sentence_transformers"
DEFAULT_EMBEDDING_HOST = "http://localhost:8001"

# Every accepted EMBEDDING_BACKEND. Lives here, not in the factory, so from_env can
# reject a typo before any service starts; core.memory.embedding_backend's registry is
# asserted equal to this tuple by its tests, so the two cannot drift apart.
EMBEDDING_BACKENDS: tuple[str, ...] = (DEFAULT_EMBEDDING_BACKEND, "openai")

# Read/write budget for one embedding request, in seconds. The connect budget is
# separate and much tighter (the provider pins it) because involuntary recall embeds
# inline in the reply path. This one guards the other hang: a server that accepts the
# connection and then stalls — a vLLM mid-model-load does exactly that. 2048 bge-m3
# inputs measured 1.72s, so 30s is a hang detector, not a throughput limit.
DEFAULT_EMBEDDING_TIMEOUT_SECONDS = 30.0
_KNOWN_EMBEDDING_DIMS: dict[str, int] = {
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "sentence-transformers/all-mpnet-base-v2": 768,
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-m3": 1024,
    "google/embeddinggemma-300m": 768,  # gated — requires HF_TOKEN + license acceptance
}


# What an unknown model's width is assumed to be. A guess, and the reason
# ``known_embedding_dim`` exists: only the caller can decide whether a guess is
# reportable as fact.
_ASSUMED_EMBEDDING_DIM = 384


def known_embedding_dim(model: str) -> int | None:
    """The width this build knows ``model`` emits, or ``None`` if it has never seen it.

    ``embedding_dim_for`` answers 384 for an unknown model, which is an assumption, not
    a measurement. A caller that reports a dimension to an operator — ``alfredctl
    doctor`` — has to be able to tell the two apart, or it states a guess as fact.
    """
    return _KNOWN_EMBEDDING_DIMS.get(model)


def embedding_dim_for(model: str) -> int:
    """Output dimension for a known embedding model (assumed 384 for unknown models).

    An explicit ``EMBEDDING_DIM`` env var always wins; this is only the fallback so
    setting ``EMBEDDING_MODEL`` to a known model auto-selects the right index dimension.
    """
    known = known_embedding_dim(model)
    return _ASSUMED_EMBEDDING_DIM if known is None else known


def normalize_embedding_dim(raw: str, model: str) -> int:
    """Resolve ``EMBEDDING_DIM``; blank means "track ``EMBEDDING_MODEL``".

    ``int("")`` raises ``invalid literal for int() with base 10: \'\'`` — no variable
    name, no file — and ``.env.example`` ships the key blank, so that traceback greeted
    every service at import at once. Zero and negatives are rejected too: they would
    create a vector index no embedding can ever be written to.
    """
    text = raw.strip()
    if not text:
        return embedding_dim_for(model)
    try:
        value = int(text)
    except ValueError:
        raise RuntimeError(f"EMBEDDING_DIM must be a whole number, got {text!r}") from None
    if value <= 0:
        raise RuntimeError(f"EMBEDDING_DIM must be greater than 0, got {text!r}")
    return value


def normalize_embedding_model(raw: str) -> str:
    """Normalise ``EMBEDDING_MODEL``; blank means the default model.

    ``.env.example`` ships the key blank ("leave blank to accept the default"), and a
    key present but empty is ``""``, which satisfies ``os.getenv`` and defeats its
    default. Without this guard every service would build a provider named ``""`` —
    ``SentenceTransformer("")`` in-process, or a model the server has never heard of.
    """
    return raw.strip() or DEFAULT_EMBEDDING_MODEL


def normalize_embedding_host(raw: str) -> str:
    """Normalise ``EMBEDDING_HOST`` to a bare origin the client appends ``/v1/...`` to.

    Two ways this bites, both silent at config time and opaque at first embed:

    * **Blank.** ``.env`` carrying ``EMBEDDING_HOST=`` sets the key to ``""``, which
      satisfies ``os.getenv`` and defeats its default. The provider would then build
      a schemeless ``"/v1/embeddings"`` and httpx raises ``UnsupportedProtocol``.
    * **A trailing ``/v1``.** That is exactly how vLLM and the OpenAI docs print base
      URLs, and keeping it yields ``/v1/v1/embeddings`` — a 404 from a healthy server.
    """
    host = raw.strip().rstrip("/")
    if host.endswith("/v1"):
        host = host.removesuffix("/v1").rstrip("/")
    return host or DEFAULT_EMBEDDING_HOST


def positive_seconds(name: str, raw: str, default: float) -> float:
    """Parse a positive number of seconds, naming the variable in every failure.

    Blank means the default. Named for its unit because the messages are ("must be a
    number of seconds"): a different quantity needs its own noun, not this one.

    ``float(...)`` raises ``could not convert string to float: \'abc\'``, which names
    neither the variable nor the file it came from, and it accepts ``0`` and negatives —
    both of which reach ``httpx.Timeout`` as "give up immediately" rather than the "wait
    longer" the operator meant.

    Takes the value rather than reading it, so a caller that already holds one validates
    it by exactly the rule the services apply: ``alfredctl doctor`` reads a merged
    ``.env`` dict where ``os.environ`` does not have the last word.
    """
    text = raw.strip()
    if not text:
        # A key present but empty (``EMBEDDING_TIMEOUT_SECONDS=`` in .env) is "" here,
        # which would defeat os.getenv's own default.
        return default
    try:
        value = float(text)
    except ValueError:
        raise RuntimeError(f"{name} must be a number of seconds, got {text!r}") from None
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than 0, got {text!r}")
    return value


def cost_cap_usd(raw: str, default: float) -> float:
    """Parse ``DAILY_COST_CAP_USD``, naming it in every failure.

    Blank means the default. Zero or less is allowed and is the kill switch: every paid
    model request is refused (``shared/spend.py``). NaN and infinity are refused here, at
    startup, rather than by the budget inside each service, where the supervisor would
    restart it until it gave up on the whole runner.
    """
    text = raw.strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        raise RuntimeError(
            f"DAILY_COST_CAP_USD must be an amount of dollars, got {text!r}"
        ) from None
    if not math.isfinite(value):
        raise RuntimeError(
            f"DAILY_COST_CAP_USD must be a finite amount of dollars, got {text!r} "
            "(0 or less refuses every paid request)"
        )
    return value


def positive_seconds_env(name: str, default: float) -> float:
    """Read a positive number of seconds from the environment."""
    return positive_seconds(name, os.getenv(name, ""), default)


# Where prompt text is read from (shared/prompts/store.py): the Langfuse label, and how
# long a fetched text is served before it is refreshed in the background.
DEFAULT_PROMPT_LABEL = "production"
DEFAULT_PROMPT_CACHE_TTL_S = 60.0
DEFAULT_OPENAI_COMPAT_HOST = "http://localhost:1234"
DEFAULT_SYSTEM2_MODEL = "openrouter:anthropic/claude-sonnet-4"
DEFAULT_SYSTEM2_MAX_TOKENS = 2048
DEFAULT_SYSTEM2_TIMEOUT_S = 60.0
# The served-name prefixes VLLMProvider picks a model profile from. A custom
# --served-model-name defeats that, and `alfredctl doctor` warns.
VLLM_KNOWN_FAMILIES: tuple[str, ...] = (
    "llama",
    "gemma",
    "qwen",
    "qwq",
    "deepseek",
    "mistral",
    "command",
    "c4ai-command",
    "gpt-oss",
    "glm",
)
_PROVIDER_PREFIX = re.compile(r"^[A-Za-z][\w-]*:")


def translate_legacy_model(raw: str, *, openai_base_url: str = "") -> str:
    """A LiteLLM model string (the old ``CLAUDE_MODEL``) as a Pydantic AI model string.

    - ``openrouter/<vendor>/<model>`` → ``openrouter:<vendor>/<model>``;
    - ``openai/<model>`` → ``vllm:<model>`` when ``OPENAI_BASE_URL`` names a server,
      else ``openai:<model>``, which ``alfredctl doctor`` fails;
    - a value that already names a provider (``vllm:…``) passes through;
    - anything else is returned unchanged, for ``resolve_model`` to refuse at startup.
    """
    value = raw.strip()
    if _PROVIDER_PREFIX.match(value):
        return value
    vendor, sep, rest = value.partition("/")
    if sep and rest and vendor == "openrouter":
        return f"openrouter:{rest}"
    if sep and rest and vendor == "openai":
        return f"vllm:{rest}" if openai_base_url.strip() else f"openai:{rest}"
    return value


@functools.cache
def _warn_legacy_model(raw: str, translated: str) -> None:
    logger.warning(
        "CLAUDE_MODEL=%r is a LiteLLM model string; reading it as SYSTEM2_MODEL=%r. "
        "Set SYSTEM2_MODEL instead to silence this.",
        raw,
        translated,
    )


def normalize_vllm_base_url(raw: str) -> str:
    """An OpenAI-compatible server's base URL, ending in ``/v1``. Blank stays blank."""
    url = raw.strip().rstrip("/")
    if not url:
        return ""
    return url if url.endswith("/v1") else f"{url}/v1"


def vllm_family_known(served_name: str) -> bool:
    """Whether *served_name* (or its last ``/`` segment) starts with a known family."""
    name = served_name.strip().lower()
    return any(c.startswith(VLLM_KNOWN_FAMILIES) for c in (name, name.rsplit("/", 1)[-1]))


def _positive_int(name: str, raw: str, default: int) -> int:
    """Parse a positive whole number, naming the variable in every failure."""
    text = raw.strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        raise RuntimeError(f"{name} must be a whole number, got {text!r}") from None
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than 0, got {text!r}")
    return value


@dataclass(frozen=True)
class LlmSettings:
    """The LLM roles' settings, read once from the environment."""

    system2_model: str
    librarian_model: str
    vllm_base_url: str
    openrouter_api_key: str
    system2_max_tokens: int
    system2_timeout_s: float


def resolve_llm_settings(env: Mapping[str, str]) -> LlmSettings:
    """Read the LLM settings from *env*; a blank value means its default.

    Takes a mapping rather than reading ``os.environ``, so ``alfredctl doctor`` checks
    its merged ``.env`` by the same rule the services apply.

    Raises:
        RuntimeError: a token cap or timeout is not a positive number; the message names
            the variable.
    """

    def get(name: str) -> str:
        return env.get(name, "").strip()

    openai_base_url = get("OPENAI_BASE_URL")
    legacy = get("CLAUDE_MODEL")
    system2 = get("SYSTEM2_MODEL")
    from_legacy_openai = False
    if not system2 and legacy:
        system2 = translate_legacy_model(legacy, openai_base_url=openai_base_url)
        if system2 != legacy:
            _warn_legacy_model(legacy, system2)
        from_legacy_openai = legacy.startswith("openai/") and bool(openai_base_url)
    system2 = system2 or DEFAULT_SYSTEM2_MODEL
    base_url = (
        get("VLLM_BASE_URL")
        or (openai_base_url if from_legacy_openai else "")
        or get("OPENAI_COMPAT_HOST")
        or get("LMSTUDIO_HOST")
        or DEFAULT_OPENAI_COMPAT_HOST
    )
    tokens_name = "SYSTEM2_MAX_TOKENS" if get("SYSTEM2_MAX_TOKENS") else "CLAUDE_MAX_TOKENS"
    return LlmSettings(
        system2_model=system2,
        librarian_model=get("LIBRARIAN_MODEL") or system2,
        vllm_base_url=normalize_vllm_base_url(base_url),
        openrouter_api_key=get("OPENROUTER_API_KEY") or get("CLAUDE_API_KEY"),
        system2_max_tokens=_positive_int(tokens_name, get(tokens_name), DEFAULT_SYSTEM2_MAX_TOKENS),
        system2_timeout_s=positive_seconds(
            "SYSTEM2_TIMEOUT_S", get("SYSTEM2_TIMEOUT_S"), DEFAULT_SYSTEM2_TIMEOUT_S
        ),
    )


def normalize_embedding_backend(raw: str) -> str:
    """Normalise and validate ``EMBEDDING_BACKEND``; blank means the default.

    Validated at config load rather than at provider construction because one typo
    used to produce three different outcomes: the conscious engine and the librarian
    caught it and ran on with memory silently disabled, the admin API cached it as a
    permanent failure, and the ingestor died with a traceback. Failing here makes a
    typo fail once, loudly and identically, in every process, before any of them can
    diverge. The factory keeps its own check for configs built by hand.
    """
    backend = raw.strip().lower() or DEFAULT_EMBEDDING_BACKEND
    if backend not in EMBEDDING_BACKENDS:
        raise RuntimeError(
            f"Unknown EMBEDDING_BACKEND {raw!r} (read as {backend!r}; expected one of: "
            f"{', '.join(EMBEDDING_BACKENDS)})"
        )
    return backend


def models_root() -> Path:
    """Root for downloaded model caches (env ``ALFRED_MODELS_DIR``, default ``<data>/models``).

    Caches, not state: safe to share across worktrees/containers and to delete.
    """
    override = os.getenv("ALFRED_MODELS_DIR", "").strip()
    root = Path(override).resolve() if override else data_root() / "models"
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass(frozen=True)
class AlfredConfig:
    redis_host: str = "localhost"
    redis_port: int = 6379
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3:8b"
    lmstudio_host: str = "http://localhost:1234"

    # Reflex (System 1) inference backend: ollama (native /api/chat) | openai
    # (any OpenAI-compatible /v1/chat/completions server — vLLM, LM Studio)
    reflex_backend: str = "ollama"
    openai_compat_host: str = DEFAULT_OPENAI_COMPAT_HOST
    openai_compat_model: str = ""
    ha_host: str = "http://homeassistant.local:8123"
    ha_token: str = ""
    research_vault_path: str = "./research"
    signoz_enabled: bool = True
    otel_endpoint: str = "http://localhost:4317"

    # Phase 3: Conscious Engine (models and keys: see the LLM settings below)
    system2_model: str = DEFAULT_SYSTEM2_MODEL
    librarian_model: str = DEFAULT_SYSTEM2_MODEL
    vllm_base_url: str = ""
    openrouter_api_key: str = ""
    system2_max_tokens: int = DEFAULT_SYSTEM2_MAX_TOKENS
    system2_timeout_s: float = DEFAULT_SYSTEM2_TIMEOUT_S
    session_timeout_minutes: int = 30
    proactivity_level: str = "opinionated"  # opinionated | moderate | conservative

    # Prompts (shared/prompts): served from Langfuse when LANGFUSE_HOST is set, else
    # the repo defaults.
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    prompt_label: str = DEFAULT_PROMPT_LABEL
    prompt_cache_ttl_s: float = DEFAULT_PROMPT_CACHE_TTL_S

    # Phase 3: Cost
    daily_cost_cap_usd: float = 5.0

    # Memory: Embedding. ``embedding_backend`` picks how the model is run, not
    # which model: ``sentence_transformers`` loads it in-process (default — a
    # fresh clone needs no server), ``openai`` calls an OpenAI-compatible
    # /v1/embeddings server (vLLM --runner pooling) at ``embedding_host``.
    # ``embedding_model`` names the model either way, so ``embedding_dim``
    # keeps tracking it through ``embedding_dim_for()``.
    # ``embedding_api_key`` is the bearer token for that server — needed by a vLLM
    # started with ``--api-key`` (or real OpenAI), and ignored by the in-process
    # backend. Empty means "send no Authorization header at all".
    embedding_backend: str = DEFAULT_EMBEDDING_BACKEND
    embedding_host: str = DEFAULT_EMBEDDING_HOST
    embedding_timeout_seconds: float = DEFAULT_EMBEDDING_TIMEOUT_SECONDS
    embedding_api_key: str = ""
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dim: int = 384

    # Memory: Significance weights
    significance_weight_safety: float = 0.35
    significance_weight_novelty: float = 0.25
    significance_weight_personal: float = 0.25
    significance_weight_emotional: float = 0.15

    # Memory: Decay
    decay_migration_threshold: float = DEFAULT_DECAY_MIGRATION_THRESHOLD

    # Memory: Involuntary recall
    involuntary_recall_limit: int = 10
    involuntary_recall_threshold: float = DEFAULT_INVOLUNTARY_RECALL_THRESHOLD

    # Memory: Pattern detection
    pattern_min_occurrences: int = 3
    pattern_min_days: int = 7
    pattern_confidence_threshold: float = 0.6
    routine_decay_per_cycle: float = 0.05
    routine_archive_threshold: float = 0.3
    routine_suggestion_cooldown_hours: int = 24

    # Memory: Semantic conflict resolution
    conflict_min_observations: int = 5
    conflict_min_days: int = 14

    # Phase 3: Voice
    voice_confidence_threshold: float = 0.85
    # Phase 3: Voice — TTS backend (see docs/voice.md)
    tts_backend: str = "kokoro"  # kokoro | piper
    kokoro_voice: str = "am_michael"
    kokoro_speed: float = 1.0
    kokoro_onnx_provider: str = "auto"  # auto | cpu | cuda | coreml

    # Phase 3: Signal
    signal_phone_number: str = ""

    # Phase 3: Logging
    log_level: str = "INFO"
    log_json: bool = False

    # Librarian interval: LIBRARIAN_INTERVAL_SECONDS env var (default: 3600s = 1hr)
    # Not in AlfredConfig since it's only used by the conscious process scheduler.

    @classmethod
    def from_env(cls) -> AlfredConfig:
        llm = resolve_llm_settings(os.environ)
        # EMBEDDING_DIM defaults to the known dimension for EMBEDDING_MODEL so the two
        # never silently drift out of sync (a mismatch breaks vector search).
        embedding_model = normalize_embedding_model(os.getenv("EMBEDDING_MODEL", ""))
        embedding_dim = normalize_embedding_dim(os.getenv("EMBEDDING_DIM", ""), embedding_model)
        # Both read through a blank-means-default guard rather than os.getenv's
        # default: a key present but empty (``EMBEDDING_HOST=`` in .env) is "" here.
        embedding_host = normalize_embedding_host(os.getenv("EMBEDDING_HOST", ""))
        embedding_backend = normalize_embedding_backend(os.getenv("EMBEDDING_BACKEND", ""))
        embedding_timeout_seconds = positive_seconds_env(
            "EMBEDDING_TIMEOUT_SECONDS", DEFAULT_EMBEDDING_TIMEOUT_SECONDS
        )
        # Same blank-means-default rule on the reflex side: `.env.example` ships
        # OPENAI_COMPAT_HOST empty, and "" would satisfy os.getenv, defeat the
        # LMSTUDIO_HOST fallback this chain documents, and leave the reflex client
        # building a schemeless "" + "/v1/chat/completions" (httpx UnsupportedProtocol).
        lmstudio_host = os.getenv("LMSTUDIO_HOST", "").strip() or DEFAULT_OPENAI_COMPAT_HOST
        openai_compat_host = os.getenv("OPENAI_COMPAT_HOST", "").strip() or lmstudio_host
        return cls(
            redis_host=os.getenv("REDIS_HOST", "localhost"),
            redis_port=int(os.getenv("REDIS_PORT", "6379")),
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3:8b"),
            lmstudio_host=lmstudio_host,
            reflex_backend=os.getenv("REFLEX_BACKEND", "ollama"),
            # Falls back to LMSTUDIO_HOST — LM Studio is the same protocol
            openai_compat_host=openai_compat_host,
            openai_compat_model=os.getenv("OPENAI_COMPAT_MODEL", ""),
            ha_host=os.getenv("HA_HOST", "http://homeassistant.local:8123"),
            ha_token=os.getenv("HA_TOKEN", ""),
            research_vault_path=os.getenv("RESEARCH_VAULT_PATH", str(data_root() / "research")),
            signoz_enabled=is_truthy_flag(os.getenv("SIGNOZ_ENABLED", "true")),
            otel_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
            # Phase 3: Conscious Engine
            system2_model=llm.system2_model,
            librarian_model=llm.librarian_model,
            vllm_base_url=llm.vllm_base_url,
            openrouter_api_key=llm.openrouter_api_key,
            system2_max_tokens=llm.system2_max_tokens,
            system2_timeout_s=llm.system2_timeout_s,
            session_timeout_minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "30")),
            proactivity_level=os.getenv("PROACTIVITY_LEVEL", "opinionated"),
            langfuse_host=os.getenv("LANGFUSE_HOST", "").strip(),
            langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
            langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
            prompt_label=os.getenv("ALFRED_PROMPT_LABEL", "").strip() or DEFAULT_PROMPT_LABEL,
            prompt_cache_ttl_s=positive_seconds_env(
                "PROMPT_CACHE_TTL_S", DEFAULT_PROMPT_CACHE_TTL_S
            ),
            # Phase 3: Cost
            daily_cost_cap_usd=cost_cap_usd(os.getenv("DAILY_COST_CAP_USD", ""), 5.0),
            # Memory: Embedding (env-configurable; see above for the dim default).
            embedding_backend=embedding_backend,
            embedding_host=embedding_host,
            embedding_timeout_seconds=embedding_timeout_seconds,
            embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
            embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            # Memory: Involuntary recall (env-configurable)
            involuntary_recall_limit=int(os.getenv("INVOLUNTARY_RECALL_LIMIT", "10")),
            involuntary_recall_threshold=float(
                os.getenv("INVOLUNTARY_RECALL_THRESHOLD", str(DEFAULT_INVOLUNTARY_RECALL_THRESHOLD))
            ),
            # Phase 3: Voice
            voice_confidence_threshold=float(os.getenv("VOICE_CONFIDENCE_THRESHOLD", "0.85")),
            tts_backend=os.getenv("ALFRED_TTS_BACKEND", "kokoro"),
            kokoro_voice=os.getenv("KOKORO_VOICE", "am_michael"),
            kokoro_speed=float(os.getenv("KOKORO_SPEED", "1.0")),
            kokoro_onnx_provider=os.getenv("KOKORO_ONNX_PROVIDER", "auto"),
            # Phase 3: Signal
            signal_phone_number=os.getenv("SIGNAL_PHONE_NUMBER", ""),
            # Phase 3: Logging
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_json=is_truthy_flag(os.getenv("LOG_JSON", "false")),
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"


def apply_model_env(config: AlfredConfig, environ: MutableMapping[str, str] = os.environ) -> None:
    """Export the resolved base URL and key where Pydantic AI's providers read them.

    ``vllm:`` reads ``VLLM_BASE_URL`` and ``openrouter:`` reads ``OPENROUTER_API_KEY``
    when a model string is resolved. The resolved values include fallbacks the
    providers know nothing of (``OPENAI_COMPAT_HOST``, ``CLAUDE_API_KEY``), so call this
    before ``shared.agents.resolve_model``.
    """
    if config.vllm_base_url:
        environ["VLLM_BASE_URL"] = config.vllm_base_url
    if config.openrouter_api_key:
        environ["OPENROUTER_API_KEY"] = config.openrouter_api_key
