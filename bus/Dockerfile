FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir .

COPY bus/ /app/bus/

CMD ["python", "-m", "bus"]
