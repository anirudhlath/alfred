# Alfred — single fat OCI image: redis + mosquitto + 6 core services + home-service,
# supervised by tini → python -m runner (ALFRED_MANAGE_INFRA=1).
#
# Build context = a STAGED workspace dir containing alfred/ and home-service/,
# produced from `git ls-files` so gitignored files (.env, secrets, personal
# preferences) can never enter the image. Build via:  uv run alfredctl build
#
# Base pins: python:3.13-slim-bookworm and redis:8-bookworm are both Debian 12,
# so the copied redis binaries + modules link against matching glibc/openssl.

FROM node:22-slim AS webbuild
WORKDIR /web
COPY alfred/web/package.json alfred/web/package-lock.json ./
RUN npm ci
COPY alfred/web/ ./
RUN npm run build

FROM redis:8-bookworm AS redis

FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# tini: PID 1 (zombie reaping + signal forwarding for the multi-process container)
# mosquitto(+clients): MQTT edge broker; libgomp1: RediSearch OpenMP runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini mosquitto mosquitto-clients libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Redis 8 (official image) bundles redisearch/rejson modules — copy binaries + modules
COPY --from=redis /usr/local/bin/redis-server /usr/local/bin/redis-cli /usr/local/bin/
COPY --from=redis /usr/local/lib/redis/modules/ /usr/local/lib/redis/modules/

WORKDIR /app

# Dependency layer first (cache-friendly): install deps only, not the project
COPY alfred/pyproject.toml /app/pyproject.toml
RUN uv pip install --system --no-cache -r pyproject.toml \
        --extra voice --extra memory --extra integrations
COPY home-service/pyproject.toml /srv/home-service/pyproject.toml
RUN uv pip install --system --no-cache -r /srv/home-service/pyproject.toml

# Source trees (run from source via PYTHONPATH — one copy, no site-packages duplicate)
COPY alfred/bus/ /app/bus/
COPY alfred/core/ /app/core/
COPY alfred/domains/ /app/domains/
COPY alfred/runner/ /app/runner/
COPY alfred/sdk/ /app/sdk/
COPY alfred/shared/ /app/shared/
COPY alfred/telemetry/ /app/telemetry/
COPY home-service/app/ /srv/home-service/app/
COPY home-service/alfred_ext/ /srv/home-service/alfred_ext/

COPY --from=webbuild /web/dist /app/web/dist

# /app: monorepo packages · /app/sdk: alfred_sdk for home-service · /srv/home-service: app
ENV PYTHONPATH=/app:/app/sdk:/srv/home-service \
    PYTHONUNBUFFERED=1 \
    ALFRED_MANAGE_INFRA=1 \
    ALFRED_DATA_DIR=/data \
    ALFRED_MODELS_DIR=/models \
    HF_HOME=/models/hf \
    ALFRED_SECRETS_BACKEND=cryptfile

EXPOSE 8081

# Model downloads on a cold cache can take minutes — generous start period
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8081/health', timeout=3)"]

ENTRYPOINT ["tini", "--", "python", "-m", "runner", "--no-reload"]
