# Alfred core services — Bridge and Reflex Runner
# Build: container build -t alfred .
# Run bridge:  container run --name bridge  ... alfred python -m bus
# Run reflex:  container run --name reflex  ... alfred python -m core.reflex

FROM python:3.13-slim

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy source
COPY pyproject.toml /app/
COPY bus/ /app/bus/
COPY core/ /app/core/
COPY domains/ /app/domains/
COPY sdk/ /app/sdk/
COPY shared/ /app/shared/
COPY telemetry/ /app/telemetry/

# Non-editable install (editable requires source present, non-editable is correct for containers)
RUN uv pip install --system --no-cache .

# Ensure all packages are importable from /app
ENV PYTHONPATH=/app

# Default: run the reflex runner
CMD ["python", "-m", "core.reflex"]
