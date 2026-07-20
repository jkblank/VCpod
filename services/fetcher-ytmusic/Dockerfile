FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY services/common /app/services/common
COPY services/fetcher-ytmusic /app/services/fetcher-ytmusic

RUN uv sync --frozen --no-dev --package fetcher-ytmusic

ENTRYPOINT ["uv", "run", "fetcher-ytmusic"]
